/**
 * Locate the community CTFAK CLI (needed for the full EXE -> MFA rebuild).
 * Electron-free and unit-testable.
 *
 * Search order:
 *   1. the path the user picked in the UI (saved in userData/ctfak.json)
 *   2. CTFAK_BIN environment variable
 *   3. CTS2_CTFAK_DIR environment variable (directory)
 *   4. directories passed via `extraDirs` (e.g. app resources)
 *   5. PATH (CTFAK.Cli.exe / CTFAK.exe)
 *   6. common install/build locations
 */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const WIN_NAMES = ['CTFAK.Cli.exe', 'CTFAK.exe', 'ctfak.cli.exe', 'ctfak.exe'];
const OTHER_NAMES = ['CTFAK.Cli', 'CTFAK.Cli.exe', 'CTFAK', 'ctfak.cli', 'ctfak'];

function listCtfakInDir(dir, platform = 'win32') {
  if (!dir || !fs.existsSync(dir)) return [];
  const names = platform === 'win32' ? WIN_NAMES : OTHER_NAMES;
  const out = [];
  for (const name of names) {
    const p = path.join(dir, name);
    if (fs.existsSync(p) && fs.statSync(p).isFile()) out.push(p);
  }
  // one level deep (CTFAK 2.0 build layouts)
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    entries = [];
  }
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    for (const n of names) {
      const p = path.join(dir, e.name, n);
      if (fs.existsSync(p) && fs.statSync(p).isFile()) out.push(p);
    }
  }
  return out;
}

function whichCtfak(platform = 'win32') {
  const out = [];
  const names = platform === 'win32' ? ['CTFAK.Cli.exe', 'CTFAK.exe'] : ['CTFAK.Cli', 'ctfak'];
  for (const name of names) {
    const r =
      platform === 'win32'
        ? spawnSync('where', [name], { encoding: 'utf8' })
        : spawnSync('sh', ['-c', `command -v ${name}`], { encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) {
      out.push(r.stdout.trim().split(/\r?\n/)[0]);
    }
  }
  return out;
}

function findCtfak(opts = {}) {
  const {
    repoRoot = '',
    userDataDir = '',
    env = process.env,
    platform = process.platform,
    extraDirs = [],
  } = opts;
  const candidates = [];
  const pushed = (p, source) => {
    if (p) candidates.push({ path: p, source });
  };

  // 1. user selection
  if (userDataDir) {
    try {
      const sel = JSON.parse(fs.readFileSync(path.join(userDataDir, 'ctfak.json'), 'utf8'));
      if (sel && sel.path) pushed(sel.path, 'user selection');
    } catch {
      /* no selection */
    }
    for (const p of listCtfakInDir(path.join(userDataDir, 'ctfak'), platform)) {
      pushed(p, 'app cache');
    }
  }
  // 2. environment
  if (env.CTFAK_BIN) pushed(env.CTFAK_BIN, 'CTFAK_BIN');
  if (env.CTS2_CTFAK_DIR) {
    for (const p of listCtfakInDir(env.CTS2_CTFAK_DIR, platform)) pushed(p, 'CTS2_CTFAK_DIR');
  }
  // 3. extra dirs (app resources, dev layout)
  for (const dir of extraDirs) {
    for (const p of listCtfakInDir(dir, platform)) pushed(p, 'app resources');
  }
  if (repoRoot) {
    for (const p of listCtfakInDir(path.join(repoRoot, 'app', 'resources', 'ctfak'), platform)) {
      pushed(p, 'bundled resources/ctfak');
    }
  }
  // 4. PATH
  for (const p of whichCtfak(platform)) pushed(p, 'PATH');
  // 5. common locations
  const home = os.homedir();
  const common = [
    path.join(home, 'CTFAK'),
    path.join(home, 'ctfak'),
    path.join(home, 'CTFAK2.0', 'Interface', 'CTFAK.Cli', 'bin', 'Debug', 'net6.0-windows'),
    path.join(home, 'CTFAK2.0', 'Interface', 'CTFAK.Cli', 'bin', 'Release', 'net6.0-windows'),
    path.join(home, 'Downloads', 'CTFAK'),
    path.join(os.tmpdir(), 'CTFAK'),
  ];
  for (const dir of common) {
    for (const p of listCtfakInDir(dir, platform)) pushed(p, 'common location');
  }

  const seen = new Set();
  for (const c of candidates) {
    const norm = c.path.toLowerCase();
    if (seen.has(norm)) continue;
    seen.add(norm);
    try {
      if (fs.existsSync(c.path) && fs.statSync(c.path).isFile()) return c;
    } catch {
      /* keep looking */
    }
  }
  return null;
}

/**
 * Headless CTFAK 2.0 command line. `CTFAK.Cli` is interactive by default,
 * so we pass the documented -path/-parameters/-tool/-closeonfinish args.
 */
function ctfakCommand(ctfakPath, gamePath, tool = 'Export as MFA') {
  const name = path.basename(ctfakPath).toLowerCase();
  if (name.includes('ctfak')) {
    return [
      ctfakPath,
      '-path',
      gamePath,
      '-parameters',
      '',
      '-tool',
      tool,
      '-closeonfinish',
    ];
  }
  return [ctfakPath, gamePath];
}

function saveCtfakSelection(userDataDir, ctfakPath) {
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.writeFileSync(
    path.join(userDataDir, 'ctfak.json'),
    JSON.stringify({ path: ctfakPath }, null, 2)
  );
}

function ctfakSetupHelp() {
  return [
    'CTFAK is only needed for the full EXE -> MFA rebuild. The app converts',
    'plain .mfa files (and EXEs whose pack contains a raw MFA) without it.',
    '',
    'To enable CTFAK:',
    '  1. Install the .NET 6 Desktop Runtime (x64):',
    '     https://dotnet.microsoft.com/en-us/download/dotnet/6.0',
    '  2. Get CTFAK 2.0: https://github.com/CTFAK/CTFAK2.0',
    '     (build it, or take a ready build from the CTFAK Discord)',
    '  3. Download the requirements zip and extract it next to CTFAK.Cli.exe',
    '     (template.mfa must sit beside the exe):',
    '     https://github.com/CTFAK/.github/raw/main/ctfakrequirements.zip',
    '  4. Click "Choose CTFAK.Cli.exe..." below, or drop a CTFAK build into',
    '     app/resources/ctfak/, or set CTFAK_BIN to the exe path.',
  ].join('\n');
}

module.exports = {
  findCtfak,
  ctfakCommand,
  saveCtfakSelection,
  ctfakSetupHelp,
  listCtfakInDir,
};
