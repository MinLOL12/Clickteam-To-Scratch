"""Stdlib web UI: drop a Clickteam game .exe, watch the conversion compile
live (per-chunk / per-image / per-event progress), then download an .sb3.

No .mfa needed — the game data is read straight out of the executable.

Endpoints
---------
POST /convert                 multipart upload → {"job": "<id>"}
GET  /api/jobs/<id>           current state as JSON
GET  /api/jobs/<id>/events    server-sent events (progress stream)
GET  /api/jobs/<id>/download  the finished .sb3
GET  /api/jobs/<id>/report    the conversion report (warnings etc.)
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .converter import convert_bytes
from .progress import Reporter

MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # 2 GB is plenty for any game .exe

# in-memory job store: id -> dict(reporter, result, report, error)
JOBS: dict = {}
JOBS_LOCK = threading.Lock()


def _new_job() -> str:
    return uuid.uuid4().hex


def _job_state(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return {"state": "missing"}
        state = {"state": "running", "job": job_id}
        snap = job["reporter"].snapshot()
        state.update(snap)
        if job.get("error"):
            state["state"] = "error"
            state["error"] = job["error"]
        elif job.get("result") is not None:
            state["state"] = "done"
            state["size"] = len(job["result"])
            state["stats"] = job["reporter"].stats
        return state


def _start_job(data: bytes, name: str) -> str:
    job_id = _new_job()
    reporter = Reporter(title=f"Converting {name}")
    job = {"reporter": reporter, "result": None, "report": None, "error": None}
    with JOBS_LOCK:
        JOBS[job_id] = job

    def work() -> None:
        try:
            res = convert_bytes(data, name, progress=reporter)
            job["result"] = res["project"]
            job["report"] = res["report"]
            reporter.finish({
                "sprites": res["report"].get("sprites", 0),
                "blocks": res["report"].get("blocks", 0),
                "images": len(res["mfa"].images),
                "sounds": len(res["mfa"].sounds),
                "frames": len(res["mfa"].frames),
                "warnings": len(res["report"].get("warnings", [])),
            })
        except Exception as exc:  # noqa: BLE001
            job["error"] = str(exc)
            reporter.warn(f"conversion failed: {exc}")
            reporter.finish({"error": str(exc)})

    threading.Thread(target=work, name=f"cts2-job-{job_id[:8]}", daemon=True).start()
    return job_id


INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clickteam to Scratch — converter</title>
<style>
:root{
  --bg:#0b0e1a; --card:#141a2e; --card2:#10162a; --line:#263050; --line2:#1d2540;
  --text:#e8ebff; --dim:#93a0c8; --acc:#4d7cff; --acc2:#8f5bff; --ok:#2ecc8f;
  --err:#ff5d6c; --warn:#f2b63c; --magenta:#ff4fd8;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  background:radial-gradient(1200px 600px at 70% -10%, #1a2244 0%, var(--bg) 55%);
  color:var(--text);min-height:100vh}
.wrap{max-width:860px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.2px}
h1 .grad{background:linear-gradient(90deg,var(--acc),var(--magenta));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--dim);font-size:13px;margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:18px;margin-bottom:16px}
h2{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
  margin:0 0 12px;display:flex;align-items:center;gap:8px}
.drop{border:2px dashed var(--line);border-radius:14px;padding:34px 20px;
  text-align:center;color:var(--dim);cursor:pointer;transition:.18s;
  background:var(--card2)}
.drop:hover,.drop.over{border-color:var(--acc);background:#182042;color:var(--text);
  transform:translateY(-1px);box-shadow:0 8px 30px rgba(77,124,255,.15)}
.drop strong{color:var(--text);font-size:15px}
.drop .muted{font-size:12px;margin-top:6px}
#dropZone:has(~ #panel:not(.hidden)){display:none}
.hidden{display:none !important}

/* ---- progress dashboard ---- */
#overallRow{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
#pctBig{font-size:44px;font-weight:800;font-variant-numeric:tabular-nums;
  background:linear-gradient(90deg,var(--acc),var(--magenta));
  -webkit-background-clip:text;background-clip:text;color:transparent;
  letter-spacing:-1px}
#phaseTitle{font-size:15px;font-weight:600}
#stepText{color:var(--dim);font-size:13px;font-family:ui-monospace,Consolas,monospace;
  min-height:18px;margin-top:2px;word-break:break-word}
.bar{height:14px;background:#0a0e1c;border:1px solid var(--line2);border-radius:8px;
  overflow:hidden;margin:14px 0 8px;position:relative}
.bar>div{height:100%;width:0%;border-radius:8px;
  background:linear-gradient(90deg,var(--acc),var(--acc2),var(--magenta),var(--acc));
  background-size:300% 100%;transition:width .25s ease;
  animation:flow 2.2s linear infinite}
@keyframes flow{0%{background-position:0% 0}100%{background-position:300% 0}}
.bar.done>div{animation:none;background:linear-gradient(90deg,var(--ok),#2ecc8f)}
.bar.err>div{background:var(--err);animation:none}
.barMeta{display:flex;justify-content:space-between;color:var(--dim);
  font-size:12px;font-variant-numeric:tabular-nums}
.spin{display:inline-block;width:16px;height:16px;border:3px solid var(--line);
  border-top-color:var(--acc);border-radius:50%;animation:rot .7s linear infinite;
  vertical-align:-3px;margin-right:6px}
@keyframes rot{to{transform:rotate(360deg)}}

/* phase chips */
#phases{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.phase{font-size:11px;padding:5px 10px;border-radius:20px;border:1px solid var(--line2);
  color:var(--dim);display:flex;align-items:center;gap:6px;transition:.2s}
.phase .dot{width:7px;height:7px;border-radius:50%;background:var(--line2)}
.phase.active{border-color:var(--acc);color:var(--text);box-shadow:0 0 12px rgba(77,124,255,.35)}
.phase.active .dot{background:var(--acc);animation:blink 1s ease-in-out infinite}
.phase.done{border-color:rgba(46,204,143,.5);color:var(--ok)}
.phase.done .dot{background:var(--ok)}
@keyframes blink{50%{opacity:.25}}

/* counters */
#counters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px;margin-top:14px}
.cnt{background:var(--card2);border:1px solid var(--line2);border-radius:12px;
  padding:10px 12px;text-align:center}
.cnt b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.cnt span{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em}
.cnt.live b{animation:pop .5s ease}
@keyframes pop{0%{transform:scale(1.25)}100%{transform:scale(1)}}

/* warnings & notes */
.entry{font-size:12.5px;line-height:1.5;padding:8px 12px;border-radius:10px;
  margin-bottom:8px;display:flex;gap:8px;align-items:flex-start;
  border:1px solid var(--line2);background:var(--card2)}
.entry.warn{border-color:rgba(242,182,60,.45);color:#ffd98a}
.entry.note{border-color:var(--line2);color:var(--dim)}
.entry .ic{flex:none;font-weight:800}
.entry.warn .ic{color:var(--warn)}
#warnCount{color:var(--warn);font-weight:700}
.entryList{max-height:220px;overflow:auto;padding-right:4px}
#doneBanner{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
#downloadBtn{background:linear-gradient(90deg,var(--ok),#23b07a);color:#04120c;
  border:0;padding:12px 22px;border-radius:12px;font-size:15px;font-weight:700;
  cursor:pointer;transition:.15s}
#downloadBtn:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(46,204,143,.35)}
.errbox{color:var(--err);border-color:var(--err);background:rgba(255,93,108,.08)}
code{background:#1b2340;padding:.1em .4em;border-radius:6px;font-size:.92em}
.muted{color:var(--dim)}
a{color:var(--acc)}
#log{max-height:150px;overflow:auto;font-family:ui-monospace,Consolas,monospace;
  font-size:11.5px;color:#93a0c8;background:#0a0e1c;border:1px solid var(--line2);
  border-radius:10px;padding:10px;white-space:pre-wrap;word-break:break-word}
</style>
</head>
<body>
<div class="wrap">
<h1>Clickteam Fusion <span class="grad">→ Scratch / PenguinMod</span></h1>
<p class="sub">Drop a game <code>.exe</code> (no <code>.mfa</code> needed) — the conversion
runs locally, with every step streamed live.</p>

<div class="card">
  <h2>1 · Drop the game</h2>
  <div class="drop" id="drop">
    <strong>Drop the game's .exe here</strong> or click to browse
    <div class="muted">The file you launch to play (e.g. <code>FiveNightsatFreddys.exe</code>).
    An <code>.mfa</code> project or raw <code>PAME/PAMU</code> data file works too.</div>
  </div>
  <input type="file" id="file" class="hidden" accept=".exe,.mfa,.dat,.bin,.ccn,.apk">
</div>

<div class="card hidden" id="panel">
  <h2>2 · Compiling <span id="fileName" class="muted" style="text-transform:none;letter-spacing:0"></span></h2>
  <div id="overallRow">
    <span id="pctBig">0%</span>
    <div style="flex:1;min-width:220px">
      <div id="phaseTitle">Waiting…</div>
      <div id="stepText"></div>
    </div>
    <div class="barMeta"><span id="elapsed">0.0s</span><span id="tickCount"></span></div>
  </div>
  <div class="bar" id="bar"><div></div></div>
  <div class="barMeta"><span id="barLabel">starting…</span><span id="overallPct">0%</span></div>
  <div id="phases"></div>
  <div id="counters"></div>
</div>

<div class="card hidden" id="warnCard">
  <h2>⚠ Warnings &amp; notes <span id="warnCount" style="margin-left:auto"></span></h2>
  <div class="entryList" id="entries"></div>
</div>

<div class="card hidden" id="doneCard">
  <h2>3 · Done</h2>
  <div id="doneBanner">
    <button id="downloadBtn">⬇ Download .sb3</button>
    <span class="muted" id="doneMeta"></span>
  </div>
  <div id="doneSummary" style="margin-top:10px;font-size:12.5px;color:var(--dim)"></div>
</div>

<div class="card hidden" id="logCard">
  <h2>Live log</h2>
  <div id="log"></div>
</div>

<div class="card">
  <h2>How it works</h2>
  <div class="muted" style="font-size:12.5px;line-height:1.6">
    The game is parsed locally: the EXE&rsquo;s embedded game data (PAME/PAMU) is
    decrypted chunk-by-chunk, every image is decoded to <b>PNG</b>, and the event
    lists are compiled into real Scratch blocks (the verified subset — everything
    else stays as readable <i>Logic-Notes</i> in the project). Warnings stream
    live so nothing is silently dropped.
  </div>
</div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const PHASES = [
  ['detect','Detect input'],['read','Read file'],['pack','EXE pack'],['gamedata','Game data'],
  ['chunks','Decrypt chunks'],['objects','Objects'],['frames','Frames'],['images','Images → PNG'],
  ['sounds','Sounds'],['events','Events'],['transpile','Events → Blocks'],
  ['build','Build project'],['zip','Pack .sb3'],
];
const drop = $('drop'), fileInput = $('file');
drop.addEventListener('click', () => fileInput.click());
['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => {
  const f = (e.dataTransfer.files || [])[0];
  if (f) start(f);
});
fileInput.addEventListener('change', () => { if (fileInput.files[0]) start(fileInput.files[0]); });

function el(tag, cls, text){ const n = document.createElement(tag); if (cls) n.className = cls; if (text!=null) n.textContent = text; return n; }

function buildPhases(){
  const box = $('phases'); box.innerHTML = '';
  for (const [id, label] of PHASES){
    const p = el('span', 'phase', ''); p.dataset.id = id;
    p.appendChild(el('span','dot')); p.appendChild(document.createTextNode(label));
    box.appendChild(p);
  }
}
buildPhases();

function setPhaseState(id, state){
  document.querySelectorAll('.phase').forEach(p => {
    if (p.dataset.id === id){ p.classList.remove('active','done'); if (state) p.classList.add(state); }
  });
}

function addEntry(type, text){
  const list = $('entries');
  const e = el('div', 'entry ' + type);
  e.appendChild(el('span','ic', type === 'warn' ? '⚠' : '·'));
  e.appendChild(document.createTextNode(text));
  list.appendChild(e);
  const card = $('warnCard'); card.classList.remove('hidden');
  $('warnCount').textContent = list.querySelectorAll('.entry.warn').length + ' warning(s)';
  list.scrollTop = list.scrollHeight;
}

let jobId = null, es = null, lastOverall = 0, t0 = 0;
function start(file){
  if (jobId){ try{ es && es.close(); }catch(_){} }
  $('fileName').textContent = '— ' + file.name;
  $('panel').classList.remove('hidden');
  $('warnCard').classList.add('hidden'); $('entries').innerHTML = '';
  $('doneCard').classList.add('hidden'); $('logCard').classList.add('hidden');
  $('bar').classList.remove('done','err');
  $('pctBig').textContent = '0%'; $('overallPct').textContent = '0%';
  $('barLabel').textContent = 'uploading…'; $('stepText').textContent = '';
  $('tickCount').textContent = ''; $('log').textContent = '';
  $('counters').innerHTML = '';
  document.querySelectorAll('.phase').forEach(p => p.classList.remove('active','done'));
  const fd = new FormData(); fd.append('file', file);
  fetch('/convert', { method:'POST', body: fd })
    .then(r => r.json())
    .then(js => { if (!js.job) throw new Error(js.error || 'no job'); jobId = js.job; t0 = performance.now(); openStream(jobId); })
    .catch(err => { addEntry('warn', 'upload failed: ' + err); });
}

function openStream(id){
  es = new EventSource('/api/jobs/' + id + '/events');
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch(_){ return; }
    render(ev);
    if (ev.type === 'done'){ es.close(); finish(ev); }
  };
  es.onerror = () => {
    // server closed or reconnect — poll the final state once
    fetch('/api/jobs/' + id).then(r => r.json()).then(st => {
      if (st.state === 'done'){ render(st); finish(st); }
      else if (st.state === 'error'){ fail(st); }
    }).catch(()=>{});
    es.close();
  };
}

const COUNTERS = [['images','Images → PNG'],['sprites','Sprites'],['sounds','Sounds'],
  ['events_mapped','Events → blocks'],['blocks','Blocks emitted'],['frames','Frames']];
function render(ev){
  $('pctBig').textContent = Math.round(ev.overall || 0) + '%';
  $('overallPct').textContent = Math.round(ev.overall || 0) + '%';
  $('bar').firstElementChild.style.width = Math.min(100, ev.overall || 0) + '%';
  $('phaseTitle').textContent = ev.phase_title || '';
  $('stepText').textContent = ev.step || '';
  $('barLabel').textContent = (ev.step || ev.phase_title || '');
  $('elapsed').textContent = ((performance.now() - t0)/1000).toFixed(1) + 's';
  if (ev.type === 'warn' || (ev.type === 'progress' && ev.warnings && ev.warnings.length)){
    // live warnings: show any that aren't already listed
    const shown = new Set($('entries').textContent.split('\n'));
    for (const w of (ev.warnings || [])){ if (!shown.has(w)) addEntry('warn', w); }
  }
  if (ev.phase && ev.phase !== lastOverall){ setPhaseState(ev.phase, 'active'); }
  const order = PHASES.map(p => p[0]);
  const idx = order.indexOf(ev.phase);
  for (let i = 0; i < order.length; i++){
    if (order[i] !== ev.phase && i < idx) setPhaseState(order[i], 'done');
    if (order[i] !== ev.phase && i > idx) setPhaseState(order[i], '');
  }
  if (ev.type === 'done' && ev.stats){
    const box = $('counters'); box.innerHTML = '';
    for (const [key, label] of COUNTERS){
      const v = ev.stats[key];
      if (v === undefined) continue;
      const c = el('div','cnt'); c.appendChild(el('b','', v)); c.appendChild(el('span','', label));
      box.appendChild(c);
    }
  }
}

function finish(ev){
  $('bar').classList.add('done');
  $('pctBig').textContent = '100%'; $('overallPct').textContent = '100%';
  $('bar').firstElementChild.style.width = '100%';
  $('stepText').textContent = 'Done — ' + (ev.elapsed||0) + 's';
  document.querySelectorAll('.phase').forEach(p => p.classList.add('done'));
  const st = ev.stats || {};
  $('doneMeta').textContent = '· ' + (ev.size ? (ev.size/1048576).toFixed(1) + ' MB' : '')
    + ' · ' + st.sprites + ' sprites · ' + st.blocks + ' blocks · ' + (st.warnings||0) + ' warnings';
  $('doneSummary').textContent =
    (st.events_mapped ? st.events_mapped + ' event groups compiled to blocks' : 'no events compiled')
    + (st.images ? ' · ' + st.images + ' images decoded to PNG' : '')
    + (st.frames ? ' · ' + st.frames + ' frames' : '');
  $('doneCard').classList.remove('hidden');
  if (ev.notes && ev.notes.length){ $('logCard').classList.remove('hidden'); $('log').textContent = ev.notes.join('\n'); }
}
function fail(st){
  $('bar').classList.add('err');
  $('stepText').textContent = st.error || 'conversion failed';
  addEntry('warn', 'conversion failed: ' + (st.error || ''));
}
$('downloadBtn').onclick = () => {
  const a = document.createElement('a');
  a.href = '/api/jobs/' + jobId + '/download';
  a.download = 'project.sb3';
  document.body.appendChild(a); a.click(); a.remove();
};
</script>
</body>
</html>
"""


class _H(BaseHTTPRequestHandler):
    server_version = "cts2/0.1"

    def _send(self, code: int, body: bytes, ctype: str, headers=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, INDEX.encode(), "text/html; charset=utf-8")
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job_id, action = parts[2], None
        elif len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            job_id, action = parts[2], parts[3]
        else:
            self._send(404, b"not found", "text/plain")
            return
        state = _job_state(job_id)
        if action is None:
            if state["state"] == "missing":
                self._send(404, b"no such job", "text/plain")
                return
            self._send(200, json.dumps(state).encode(), "application/json")
            return
        if action == "events":
            self._stream_events(job_id)
            return
        if action == "download":
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job or job.get("result") is None:
                    self._send(404, b"not ready", "text/plain")
                    return
                body = job["result"]
            self._send(200, body, "application/zip",
                       {"Content-Disposition": 'attachment; filename="game.sb3"'})
            return
        if action == "report":
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._send(404, b"no such job", "text/plain")
                    return
                body = json.dumps(job.get("report") or {}).encode()
            self._send(200, body, "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def _stream_events(self, job_id: str) -> None:
        """SSE: replay the reporter's buffered events, then stream new ones."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                self.wfile.write(b"event: error\ndata: {\"error\":\"no such job\"}\n\n")
                self.wfile.flush()
                return
            reporter = job["reporter"]
            sent = 0
            sent_done = False
            while True:
                with reporter._lock:
                    hist = list(reporter.history)
                for ev in hist[sent:]:
                    sent += 1
                    if ev.get("type") == "done":
                        sent_done = True
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                if sent_done or job.get("error") or job.get("result") is not None:
                    # final state push for anything that raced
                    st = _job_state(job_id)
                    if not sent_done and st["state"] in ("done", "error"):
                        self.wfile.write(f"data: {json.dumps(st)}\n\n".encode())
                    break
                time.sleep(0.08)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if urlparse(self.path).path != "/convert":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                self._send(413, b"upload too large", "text/plain")
                return
            raw = self.rfile.read(length)
            name = "project.mfa"
            ctype = self.headers.get("Content-Type", "")
            if "boundary=" not in ctype:
                self._send(400, b"expected multipart upload", "text/plain")
                return
            boundary = ctype.split("boundary=", 1)[1].strip().encode()
            if not raw.startswith(b"--" + boundary):
                self._send(400, b"bad multipart body", "text/plain")
                return
            for part in raw.split(b"--" + boundary):
                if b'name="file"' not in part:
                    continue
                header, sep, body = part.partition(b"\r\n\r\n")
                if not sep:
                    continue
                body = body.split(b"\r\n--")[0]
                nm = b"filename="
                if nm in header:
                    raw_name = header.split(nm, 1)[1].split(b"\r\n", 1)[0].strip().strip(b'"')
                    name = raw_name.decode("utf-8", "replace") or name
                job_id = _start_job(body, name)
                self._send(200, json.dumps({"job": job_id}).encode(),
                           "application/json")
                return
            self._send(400, b"Could not parse upload", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._send(400, str(exc).encode(), "text/plain")

    def log_message(self, *args):  # quieter
        pass


def serve(port: int = 8000):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _H)
    print(f"Clickteam To Scratch web UI running at http://0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
