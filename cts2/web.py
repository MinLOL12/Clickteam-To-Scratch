"""Tiny stdlib web UI for uploading a Clickteam .mfa/.exe and downloading SB3."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from urllib.parse import parse_qs, urlparse

from .converter import convert_bytes

INDEX = """<!doctype html>
<html><head><meta charset="utf-8"><title>Clickteam to Scratch</title>
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:3rem auto;padding:0 1rem;background:#0f1220;color:#ebf}
h1{font-size:1.6rem}
.card{background:#1a2035;border:1px solid #2a3355;border-radius:12px;padding:1.5rem;margin-bottom:1rem}
input[type=file]{display:block;margin:1rem 0}
button{background:#3d6bff;color:#fff;border:0;padding:.7rem 1.2rem;border-radius:8px;cursor:pointer}
.msg{white-space:pre-wrap;background:#10131d;border:1px solid #2a3355;border-radius:8px;padding:1rem;font-size:.85rem}
code{background:#26304d;padding:.1rem .35rem;border-radius:4px}
</style></head><body>
<h1>Clickteam Fusion (MMF2 / Fusion 2.5) → Scratch / PenguinMod</h1>
<div class="card">
<p>Upload a <code>.mfa</code> project file. EXE files require the community
<code>CTFAK</code> CLI installed locally with <code>CTFAK_BIN</code> set.</p>
<form method="post" enctype="multipart/form-data" id="f">
<input type="file" name="file" id="file">
<button type="submit">Convert to .sb3</button>
</form>
</div>
<div class="card"><h2>How it works</h2>
<p>MFA files are parsed locally in your browser-side process: frames, frame
items/instances, image &amp; sound banks, globals and the event tree are read,
then a real Scratch 3 / PenguinMod <code>.sb3</code> project is generated with
sprites, costumes, positions and starter scripts.</p>
<p>See the README for the supported event subset and EXE notes.</p>
</div>
<div id="msg" class="msg"></div>
<script>
const f=document.getElementById('f'),msg=document.getElementById('msg');
f.addEventListener('submit', async e=>{
 e.preventDefault();
 const file=document.getElementById('file').files[0];
 if(!file){msg.textContent='Select a .mfa file.';return}
 msg.textContent='Converting...';
 const fd=new FormData();fd.append('file',file);
 const r=await fetch('/convert',{method:'POST',body:fd});
 if(!r.ok){msg.textContent='Conversion failed: '+await r.text();return}
 const blob=await r.blob();
 const a=document.createElement('a');
 a.href=URL.createObjectURL(blob);a.download=file.name.replace(/\\.([^.]+)$/,'')+'.sb3';
 a.click();
 msg.textContent='Done! Downloading '+a.download;
});
</script>
</body></html>"""


class _H(BaseHTTPRequestHandler):
    server_version = "cts2/0.1"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/":
            self._send(200, INDEX.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/convert":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            name = "project.mfa"
            # parse multipart minimally
            boundary = None
            ctype = self.headers.get("Content-Type", "")
            if "boundary=" in ctype:
                boundary = ctype.split("boundary=", 1)[1].strip().encode()
            if boundary and raw.startswith(b"--" + boundary):
                # crude parser: split on boundary, locate filename and body
                parts = raw.split(b"--" + boundary)
                for part in parts:
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
                    result = convert_bytes(body, name)
                    self._send(
                        200,
                        result["project"],
                        "application/zip",
                    )
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
