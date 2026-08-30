/**
 * Locate or provision the Python runtime the converter runs on.
 *
 * Priority:
 *   1. `resources/runtime/<platform>/python(.exe)` bundled with the app
 *   2. a runtime auto-provisioned into the user-data folder on first run
 *   3. a system Python (dev fallback: python3 / python / py -3)
 *
 * On Windows the provisioned runtime is python.org's *embeddable*
 * distribution: a zip that contains a full standard-library Python with
 * no installer, no pip and no system changes. The converter itself is
 * pure standard library, so that is all we need.
 *
 * Every function here is electron-free and unit-testable.
 */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const https = require('node:https');
const http = require('node:http');
const { extractZip } = require('./zip.js');

const PYTHON_VERSION = '3.11.9';

function runtimeInfoFor(platform = 'win32') {
  const v = PYTHON_VERSION;
  if (platform === 'win32') {
    return {
      kind: 'embed',
      url: `https://www.python.org/ftp/python/${v}/python-${v}-embed-amd64.zip`,
      archive: 'zip',
      binary: 'python.exe',
    };
  }
  // No official binary for other platforms: rely on a system python3.
  return { kind: 'system', binary: 'python3' };
}

function pythonBinaryName(platform = 'win32') {
  return platform === 'win32' ? 'python.exe' : 'python3';
}

/** A directory counts as a runtime when it has the python binary. */
function runtimeDirHasPython(dir, platform = 'win32') {
  return dir && fs.existsSync(path.join(dir, pythonBinaryName(platform)));
}

function findBundledRuntime(repoRoot, platform = 'win32') {
  const dir = path.join(repoRoot, 'app', 'resources', 'runtime', platform);
  if (runtimeDirHasPython(dir, platform)) return dir;
  // allow a generic layout too (resources/runtime/python)
  const alt = path.join(repoRoot, 'app', 'resources', 'runtime', 'python');
  if (runtimeDirHasPython(alt, platform)) return alt;
  return null;
}

function findProvisionedRuntime(userDataDir, platform = 'win32') {
  const dir = path.join(userDataDir, 'runtime', 'python');
  if (runtimeDirHasPython(dir, platform)) return dir;
  return null;
}

function findSystemPython(platform = 'win32') {
  const names = platform === 'win32' ? ['python', 'python3', 'py'] : ['python3', 'python'];
  for (const name of names) {
    if (platform === 'win32') {
      const r = spawnSync('where', [name], { encoding: 'utf8' });
      if (r.status === 0 && r.stdout.trim()) {
        return { path: r.stdout.trim().split(/\r?\n/)[0], via: name };
      }
    } else {
      const r = spawnSync('sh', ['-c', `command -v ${name}`], { encoding: 'utf8' });
      if (r.status === 0 && r.stdout.trim()) {
        return { path: r.stdout.trim(), via: name };
      }
    }
  }
  // 'py' launcher on Windows
  if (platform === 'win32') {
    const r = spawnSync('where', ['py'], { encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) {
      return { path: r.stdout.trim().split(/\r?\n/)[0], via: 'py', args: ['-3'] };
    }
  }
  return null;
}

function downloadFile(url, dest, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    const req = lib.get(url, (res) => {
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        downloadFile(res.headers.location, dest, { onProgress }).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`download failed: HTTP ${res.statusCode} for ${url}`));
        return;
      }
      const total = parseInt(res.headers['content-length'] || '0', 10);
      let received = 0;
      const out = fs.createWriteStream(dest);
      res.on('data', (chunk) => {
        received += chunk.length;
        if (onProgress) onProgress({ phase: 'download', received, total, file: path.basename(dest) });
      });
      res.pipe(out);
      out.on('finish', () => out.close(() => resolve(dest)));
      out.on('error', reject);
    });
    req.on('error', reject);
    req.setTimeout(10 * 60 * 1000, () => {
      req.destroy(new Error('download timed out'));
    });
  });
}

/**
 * Copy the converter (cts2 package + CLI entry) next to a python runtime
 * so `python cts2_cli.py ...` works with cwd = runtimeDir.
 */
function stageCts2(repoRoot, runtimeDir) {
  const srcPkg = path.join(repoRoot, 'cts2');
  const srcCli = path.join(repoRoot, 'cts2_cli.py');
  if (!fs.existsSync(srcPkg) || !fs.existsSync(srcCli)) return false;
  const dstPkg = path.join(runtimeDir, 'cts2');
  const dstCli = path.join(runtimeDir, 'cts2_cli.py');
  fs.cpSync(srcPkg, dstPkg, { recursive: true, force: true });
  fs.copyFileSync(srcCli, dstCli);
  return true;
}

/**
 * Ensure a usable python runtime. Returns
 * { pythonPath, args, cwd, kind, dir, provisioned } where
 * `kind` is 'bundled' | 'provisioned' | 'system'.
 *
 * `platform`/`download` are injectable for tests.
 */
async function ensurePythonRuntime(opts = {}) {
  const platform = opts.platform || process.platform;
  const repoRoot = opts.repoRoot;
  const userDataDir = opts.userDataDir;
  const onProgress = opts.onProgress || (() => {});
  const download = opts.download || downloadFile;
  const info = opts.runtimeInfo || runtimeInfoFor(platform);

  const bundled = findBundledRuntime(repoRoot, platform);
  if (bundled) {
    stageCts2(repoRoot, bundled);
    return {
      pythonPath: path.join(bundled, pythonBinaryName(platform)),
      args: [],
      cwd: bundled,
      kind: 'bundled',
      dir: bundled,
      provisioned: false,
    };
  }

  if (userDataDir) {
    const provisioned = findProvisionedRuntime(userDataDir, platform);
    if (provisioned) {
      stageCts2(repoRoot, provisioned);
      return {
        pythonPath: path.join(provisioned, pythonBinaryName(platform)),
        args: [],
        cwd: provisioned,
        kind: 'provisioned',
        dir: provisioned,
        provisioned: true,
      };
    }
  }

  if (info.kind === 'embed' && userDataDir) {
    const dir = path.join(userDataDir, 'runtime', 'python');
    const zipPath = path.join(userDataDir, 'runtime', `python-${PYTHON_VERSION}-embed.zip`);
    fs.mkdirSync(path.dirname(zipPath), { recursive: true });
    onProgress({ phase: 'download', pct: 0, detail: info.url });
    await download(info.url, zipPath, onProgress);
    onProgress({ phase: 'extract', pct: 0, detail: 'extracting runtime' });
    const buf = fs.readFileSync(zipPath);
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    extractZip(buf, dir, onProgress);
    // The embeddable zip disables site-packages via ._pth; the converter
    // is pure stdlib so that is fine, but make the layout explicit.
    stageCts2(repoRoot, dir);
    fs.writeFileSync(path.join(dir, '.cts2-provisioned'), new Date().toISOString());
    return {
      pythonPath: path.join(dir, pythonBinaryName(platform)),
      args: [],
      cwd: dir,
      kind: 'provisioned',
      dir,
      provisioned: true,
    };
  }

  const sys = findSystemPython(platform);
  if (sys) {
    return {
      pythonPath: sys.path,
      args: sys.args || [],
      cwd: repoRoot,
      kind: 'system',
      dir: null,
      provisioned: false,
    };
  }

  throw new Error(
    `No Python runtime available. On Windows the app downloads a portable ` +
      `Python automatically (needs internet once, ~11 MB). On other platforms ` +
      `install python3 or bundle one into app/resources/runtime/${platform}.`
  );
}

function describeRuntime(runtime) {
  if (!runtime) return 'unknown';
  const labels = {
    bundled: 'bundled with the app',
    provisioned: `portable (auto-downloaded Python ${PYTHON_VERSION})`,
    system: `system python (${runtime.via || runtime.pythonPath})`,
  };
  return labels[runtime.kind] || runtime.kind;
}

module.exports = {
  PYTHON_VERSION,
  runtimeInfoFor,
  pythonBinaryName,
  runtimeDirHasPython,
  findBundledRuntime,
  findProvisionedRuntime,
  findSystemPython,
  downloadFile,
  stageCts2,
  ensurePythonRuntime,
  describeRuntime,
};
