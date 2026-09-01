
(() => {
  const $ = (id) => document.getElementById(id);
  const logEl = $('log');
  const filesEl = $('files');
  const state = { files: [], busy: false };

  // The log is *appended* through a buffer and flushed once per frame:
  // `logEl.textContent += line` re-serialises the whole box for every line,
  // which turns a chatty conversion into quadratic DOM work and a window
  // that stops painting.
  const LOG_MAX_LINES = 4000;
  let logLines = [];
  let logDropped = 0;
  let logStarted = false;
  let logScheduled = false;
  function flushLog() {
    logScheduled = false;
    if (!logLines.length) return;
    const text = logLines.join('\n') + '\n';
    logLines = [];
    logEl.append(text);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function addLog(line) {
    if (!logStarted) {
      logStarted = true;
      logEl.textContent = '';           // drop the "Waiting for files…" note
    }
    if (logLines.length >= LOG_MAX_LINES) {
      logDropped++;
      return;
    }
    logLines.push(line);
    if (!logScheduled) {
      logScheduled = true;
      (window.requestAnimationFrame || ((f) => setTimeout(f, 16)))(flushLog);
    }
  }

  window.cts.onLog(addLog);

  function refreshButtons() {
    $('convertBtn').disabled = state.busy || state.files.length === 0;
    $('clearBtn').disabled = state.busy || state.files.length === 0;
  }

  function renderFiles() {
    filesEl.innerHTML = '';
    for (const f of state.files) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = f.name;
      name.title = f.path;
      const badge = document.createElement('span');
      badge.className = 'badge ' + (f.status || '');
      badge.textContent = f.statusLabel || 'queued';
      const conv = document.createElement('button');
      conv.className = 'ghost';
      conv.textContent = 'Convert';
      conv.disabled = state.busy;
      conv.onclick = () => convertOne(f);
      li.append(name, badge, conv);
      filesEl.appendChild(li);
    }
    refreshButtons();
  }

  function addFiles(paths) {
    let added = false;
    for (const p of paths || []) {
      if (state.files.some((f) => f.path === p)) continue;
      const name = p.split(/[\\/]/).pop();
      state.files.push({ path: p, name, status: '', statusLabel: 'queued' });
      added = true;
    }
    if (added) { renderFiles(); }
  }

  // ------------------------------------------------------------------
  // animated conversion progress
  // ------------------------------------------------------------------
  const PHASES = [
    ['detect','Detect input'],['read','Read file'],['pack','EXE pack'],['gamedata','Game data'],
    ['chunks','Decrypt chunks'],['objects','Objects'],['frames','Frames'],['images','Images → PNG'],
    ['events','Events'],['build','Build project'],['transpile','Events → Blocks'],
    ['zip','Pack .sb3'],
  ];
  const ORDER = PHASES.map((p) => p[0]);
  (function buildPhases() {
    const box = $('phases');
    for (const [id, label] of PHASES) {
      const p = document.createElement('span');
      p.className = 'phase'; p.dataset.id = id;
      const d = document.createElement('span'); d.className = 'dot';
      p.append(d, document.createTextNode(label));
      box.appendChild(p);
    }
  })();

  function setPhase(id, cls) {
    const p = document.querySelector(`.phase[data-id="${id}"]`);
    if (p) { p.classList.remove('active', 'done'); if (cls) p.classList.add(cls); }
  }

  const seenWarns = new Set();
  let warnSeen = 0, entryNodes = 0;
  const MAX_ENTRIES = 400;      // the converter caps its list too
  function addEntry(type, text) {
    if (seenWarns.has(text)) return;
    seenWarns.add(text);
    const list = $('entries');
    if (entryNodes >= MAX_ENTRIES) {
      // Keep the panel small instead of re-laying out thousands of nodes,
      // which is what used to make the window stop painting mid-conversion.
      if (entryNodes === MAX_ENTRIES) {
        const more = document.createElement('div');
        more.className = 'entry note';
        more.id = 'entriesMore';
        list.appendChild(more);
      }
      entryNodes++;
      const more = $('entriesMore');
      if (more) more.textContent = '… further messages are in the summary below';
      return;
    }
    entryNodes++;
    const e = document.createElement('div');
    e.className = 'entry ' + type;
    const ic = document.createElement('span'); ic.className = 'ic';
    ic.textContent = type === 'warn' ? '⚠' : '·';
    e.append(ic, document.createTextNode(text));
    list.appendChild(e);
    $('warnCard').classList.remove('hidden');
    if (type === 'warn') warnSeen++;
    // O(1): counting a JS variable beats a querySelectorAll scan per entry.
    $('warnCount').textContent = warnSeen + ' warning(s)';
    list.scrollTop = list.scrollHeight;
  }

  let convT0 = 0;
  function convStart(fileName) {
    $('convPanel').classList.remove('hidden');
    $('warnCard').classList.add('hidden');
    $('entries').innerHTML = '';
    seenWarns.clear();
    entryNodes = 0; warnSeen = 0;
    $('bar').classList.remove('done', 'err');
    $('fileName').textContent = '— ' + fileName;
    $('pctBig').textContent = '0%'; $('overallPct').textContent = '0%';
    $('phaseTitle').textContent = 'Starting…';
    $('stepText').textContent = ''; $('barLabel').textContent = 'starting…';
    $('tickCount').textContent = ''; $('counters').innerHTML = '';
    document.querySelectorAll('.phase').forEach((p) => p.classList.remove('active', 'done'));
    convT0 = performance.now();
  }

  function convRender(ev) {
    if (!ev) return;
    const pct = Math.min(100, ev.overall || 0);
    $('pctBig').textContent = Math.round(pct) + '%';
    $('overallPct').textContent = Math.round(pct) + '%';
    $('bar').firstElementChild.style.width = pct + '%';
    $('phaseTitle').textContent = ev.phase_title || '';
    if (ev.step) $('stepText').textContent = ev.step;
    if (ev.step || ev.phase_title) $('barLabel').textContent = ev.step || ev.phase_title;
    $('elapsed').textContent = ((performance.now() - convT0) / 1000).toFixed(1) + 's';
    if (ev.phase) {
      const idx = ORDER.indexOf(ev.phase);
      setPhase(ev.phase, 'active');
      for (let i = 0; i < ORDER.length; i++) {
        if (ORDER[i] !== ev.phase && i < idx) setPhase(ORDER[i], 'done');
        if (ORDER[i] !== ev.phase && i > idx) setPhase(ORDER[i], null);
      }
    }
    for (const w of ev.warnings || []) addEntry('warn', w);
    if (ev.type === 'done' && ev.stats) {
      const box = $('counters'); box.innerHTML = '';
      const counters = [['images','Images → PNG'],['sprites','Sprites'],
        ['events_mapped','Events → blocks'],['blocks','Blocks emitted'],['frames','Frames']];
      for (const [key, label] of counters) {
        const v = ev.stats[key];
        if (v === undefined) continue;
        const c = document.createElement('div'); c.className = 'cnt';
        const b = document.createElement('b'); b.textContent = v;
        const s = document.createElement('span'); s.textContent = label;
        c.append(b, s); box.appendChild(c);
      }
      $('bar').classList.add('done');
      $('pctBig').textContent = '100%'; $('overallPct').textContent = '100%';
      $('bar').firstElementChild.style.width = '100%';
      document.querySelectorAll('.phase').forEach((p) => p.classList.add('done'));
      $('phaseTitle').textContent = 'Done ✓';
    }
  }

  // ------------------------------------------------------------------
  // conversion
  // ------------------------------------------------------------------
  async function convertOne(f) {
    if (state.busy) return;
    const save = await window.cts.pickSave(f.name);
    if (save.canceled || !save.filePath) return;
    state.busy = true;
    f.status = 'busy'; f.statusLabel = 'converting…';
    renderFiles();
    addLog(`\n=== ${f.name} -> ${save.filePath.split(/[\\/]/).pop()} ===`);
    convStart(f.name);
    const res = await window.cts.convert(f.path, save.filePath);
    state.busy = false;
    if (res.ok) {
      f.status = 'done'; f.statusLabel = 'done (' + (res.size/1024).toFixed(0) + ' kB)';
      addLog('[done] ' + res.output);
      convRender({ type: 'done', stats: res.report || {}, warnings: res.warnings || [] });
      for (const w of res.warnings || []) addEntry('warn', w);
      if (res.report && res.report.notes && res.report.notes.length) {
        addLog(res.report.notes.join('\n'));
      }
    } else {
      f.status = 'err'; f.statusLabel = 'error';
      addLog('[error] ' + res.error);
      $('bar').classList.add('err');
      $('phaseTitle').textContent = 'Conversion failed';
      $('stepText').textContent = res.error;
    }
    renderFiles();
  }

  window.cts.onConvertProgress((ev) => {
    if (state.busy) convRender(ev);
  });

  $('addBtn').onclick = async () => addFiles(await window.cts.pickFiles());
  $('clearBtn').onclick = () => { state.files = []; renderFiles(); };
  $('convertBtn').onclick = async () => {
    if (!state.files.length || state.busy) return;
    for (const f of state.files) {
      if (f.status === 'done' || f.status === 'err') continue;
      await convertOne(f);
    }
  };

  const drop = $('drop');
  ['dragenter','dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave','drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => {
    const paths = [...(e.dataTransfer.files || [])].map((f) => f.path);
    addFiles(paths.filter(Boolean));
  });
  drop.addEventListener('click', () => $('addBtn').click());

  // runtime card
  async function refreshRuntime() {
    const s = await window.cts.runtimeStatus();
    const el = $('runtimeStatus');
    if (s.runtime) {
      el.innerHTML = '<span class="ok">✓ ready</span> — ' + s.runtime.kind + '<br><span class="path"></span>';
      el.querySelector('.path').textContent = s.runtime.pythonPath;
    } else {
      el.innerHTML = '<span class="warn">not set up yet</span> — the app will fetch a portable Python on first convert.';
    }
  }
  $('runtimeBtn').onclick = async () => {
    const bar = $('runtimeProgress'); const fill = bar.firstElementChild;
    bar.style.display = 'block'; fill.style.width = '5%';
    $('runtimeBtn').disabled = true;
    const stop = window.cts.onProgress((p) => {
      if (p && p.phase === 'download' && p.total) fill.style.width = Math.max(5, (p.received/p.total*100)|0) + '%';
      if (p && p.phase === 'extract') fill.style.width = '90%';
    });
    const res = await window.cts.runtimeSetup();
    stop();
    $('runtimeBtn').disabled = false;
    if (res.ok) { fill.style.width = '100%'; setTimeout(() => bar.style.display = 'none', 600); }
    else bar.style.display = 'none';
    refreshRuntime();
  };

  // ctfak card (optional fallback only — EXE conversion is built-in)
  async function refreshCtfak() {
    const s = await window.cts.ctfakStatus();
    const el = $('ctfakStatus');
    if (s.found) {
      el.innerHTML = '<span class="ok">✓ found</span> <span class="path"></span><br><span class="muted">via ' + s.source + '</span>';
      el.querySelector('.path').textContent = s.path;
    } else {
      el.innerHTML = '<span class="ok">not needed</span> — EXE conversion is built-in; CTFAK is only an advanced fallback.';
    }
    $('ctfakHelp').textContent = s.help || '';
  }
  $('ctfakBtn').onclick = async () => {
    const res = await window.cts.pickCtfak();
    if (res.ok) addLog('[ctfak] saved selection: ' + res.path);
    refreshCtfak();
  };

  document.querySelectorAll('[data-url]').forEach((a) => {
    a.href = '#';
    a.addEventListener('click', (e) => { e.preventDefault(); window.cts.openExternal(a.dataset.url); });
  });

  refreshRuntime();
  refreshCtfak();
  renderFiles();
})();
