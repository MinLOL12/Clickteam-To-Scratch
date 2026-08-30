
(() => {
  const $ = (id) => document.getElementById(id);
  const logEl = $('log');
  const filesEl = $('files');
  const state = { files: [], busy: false };

  window.cts.onLog((line) => {
    if (logEl.textContent.startsWith('Waiting')) logEl.textContent = '';
    logEl.textContent += line + '\n';
    logEl.scrollTop = logEl.scrollHeight;
  });

  function addLog(line) {
    if (logEl.textContent.startsWith('Waiting')) logEl.textContent = '';
    logEl.textContent += line + '\n';
    logEl.scrollTop = logEl.scrollHeight;
  }

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

  async function convertOne(f) {
    if (state.busy) return;
    const save = await window.cts.pickSave(f.name);
    if (save.canceled || !save.filePath) return;
    state.busy = true;
    f.status = 'busy'; f.statusLabel = 'converting…';
    renderFiles();
    addLog(`\n=== ${f.name} -> ${save.filePath.split(/[\\/]/).pop()} ===`);
    const res = await window.cts.convert(f.path, save.filePath);
    state.busy = false;
    if (res.ok) {
      f.status = 'done'; f.statusLabel = 'done (' + (res.size/1024).toFixed(0) + ' kB)';
      addLog('[done] ' + res.output);
    } else {
      f.status = 'err'; f.statusLabel = 'error';
      addLog('[error] ' + res.error);
    }
    renderFiles();
  }

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
