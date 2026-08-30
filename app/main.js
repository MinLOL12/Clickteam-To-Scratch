/**
 * Clickteam to Scratch - Electron main process.
 *
 * The heavy lifting is done by the Python converter (cts2), which the app
 * runs on a portable Python runtime it provisions itself - no pip, no
 * venv, no system Python required.
 */
const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');

const { ensurePythonRuntime, describeRuntime } = require('./lib/python-runtime.js');
const {
  findCtfak,
  saveCtfakSelection,
  ctfakSetupHelp,
} = require('./lib/ctfak.js');
const {
  isExeLike,
  defaultOutputName,
  buildCliArgs,
  runPythonCli,
} = require('./lib/convert.js');

const isDev = !app.isPackaged;

/** Directory that contains the cts2 package + cts2_cli.py. */
function cts2Root() {
  // dev: the repo root (app/ lives inside the repo)
  // packaged: electron-builder copies cts2/ + cts2_cli.py into extraResources
  return isDev ? path.join(__dirname, '..') : process.resourcesPath;
}

/** Directories to search for a bundled CTFAK build (dev + packaged). */
function ctfakExtraDirs() {
  return [
    path.join(__dirname, 'resources', 'ctfak'), // app.asar/resources/ctfak (packaged)
    path.join(__dirname, '..', 'app', 'resources', 'ctfak'), // dev
  ];
}

function userDataDir() {
  return app.getPath('userData');
}

function ctfakSearchOpts() {
  return {
    repoRoot: cts2Root(),
    userDataDir: userDataDir(),
    env: process.env,
    platform: process.platform,
    extraDirs: ctfakExtraDirs(),
  };
}

let win = null;
let cachedRuntime = null;

function send(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

function logLine(line) {
  send('log', line);
}

async function getRuntime() {
  if (cachedRuntime) return cachedRuntime;
  const runtime = await ensurePythonRuntime({
    repoRoot: cts2Root(),
    userDataDir: userDataDir(),
    platform: process.platform,
    onProgress: (p) => {
      if (typeof p === 'object') send('progress', p);
    },
  });
  cachedRuntime = runtime;
  logLine(`[runtime] using ${describeRuntime(runtime)}: ${runtime.pythonPath}`);
  return runtime;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1080,
    height: 720,
    minWidth: 820,
    minHeight: 560,
    title: 'Clickteam to Scratch',
    backgroundColor: '#0f1220',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.on('closed', () => {
    win = null;
  });
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------

ipcMain.handle('pick-files', async () => {
  const res = await dialog.showOpenDialog(win, {
    title: 'Choose Clickteam project files',
    buttonLabel: 'Convert',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Clickteam files', extensions: ['mfa', 'exe', 'ccn', 'apk', 'dat', 'bin'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  return res.canceled ? [] : res.filePaths;
});

ipcMain.handle('pick-save', (_e, inputName) =>
  dialog.showSaveDialog(win, {
    title: 'Save Scratch project as...',
    defaultPath: defaultOutputName(inputName),
    filters: [{ name: 'Scratch 3 project', extensions: ['sb3'] }],
  })
);

ipcMain.handle('runtime-status', () => ({
  runtime: cachedRuntime
    ? { kind: cachedRuntime.kind, pythonPath: cachedRuntime.pythonPath }
    : null,
}));

ipcMain.handle('runtime-setup', async () => {
  cachedRuntime = null; // force re-resolution
  const runtime = await ensurePythonRuntime({
    repoRoot: cts2Root(),
    userDataDir: userDataDir(),
    platform: process.platform,
    onProgress: (p) => {
      if (typeof p === 'object') send('progress', p);
    },
  });
  cachedRuntime = runtime;
  return { ok: true, runtime: { kind: runtime.kind, pythonPath: runtime.pythonPath } };
});

ipcMain.handle('ctfak-status', () => {
  const found = findCtfak(ctfakSearchOpts());
  return {
    found: !!found,
    path: found ? found.path : null,
    source: found ? found.source : null,
    help: ctfakSetupHelp(),
    links: {
      ctfak: 'https://github.com/CTFAK/CTFAK2.0',
      dotnet: 'https://dotnet.microsoft.com/en-us/download/dotnet/6.0',
      requirements: 'https://github.com/CTFAK/.github/raw/main/ctfakrequirements.zip',
    },
  };
});

ipcMain.handle('pick-ctfak', async () => {
  const res = await dialog.showOpenDialog(win, {
    title: 'Choose CTFAK.Cli.exe',
    buttonLabel: 'Use CTFAK',
    properties: ['openFile'],
    filters: [
      { name: 'CTFAK', extensions: ['exe'] },
      { name: 'All files', extensions: ['*'] },
    ],
  });
  if (res.canceled || !res.filePaths.length) return { ok: false };
  const p = res.filePaths[0];
  saveCtfakSelection(userDataDir(), p);
  const found = findCtfak(ctfakSearchOpts());
  return { ok: true, path: found ? found.path : p };
});

ipcMain.handle('open-external', (_e, url) => {
  if (/^https?:\/\//.test(url)) shell.openExternal(url);
  return true;
});

ipcMain.handle('convert', async (_e, { input, output }) => {
  if (!input || !output) return { ok: false, error: 'missing input/output' };
  const exeLike = isExeLike(input);
  const ctfak = exeLike ? findCtfak(ctfakSearchOpts()) : null;

  let runtime;
  try {
    runtime = await getRuntime();
  } catch (exc) {
    return { ok: false, error: String((exc && exc.message) || exc) };
  }

  const args = buildCliArgs({ input, output, ctfak: ctfak ? ctfak.path : null });
  logLine(`[convert] ${path.basename(input)} -> ${path.basename(output)}`);
  if (exeLike) {
    if (ctfak) logLine(`[ctfak] using ${ctfak.path} (${ctfak.source})`);
    else logLine('[ctfak] not found - trying built-in EXE pack extraction first');
  }
  const { code, out, err } = await runPythonCli({
    python: runtime.pythonPath,
    args,
    cwd: runtime.cwd || cts2Root(),
    onLog: logLine,
  });
  if (code !== 0) {
    return {
      ok: false,
      error: ((err || out).split('\n').slice(-12).join('\n')) || `exit code ${code}`,
    };
  }
  if (!fs.existsSync(output)) {
    return { ok: false, error: 'conversion finished but the .sb3 file was not written' };
  }
  return { ok: true, output, size: fs.statSync(output).size };
});

// ---------------------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(() => {
    createWindow();
    // Warm the runtime in the background so the first conversion is quick.
    getRuntime().catch(() => {
      /* surfaced later via runtime-setup / convert */
    });
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
