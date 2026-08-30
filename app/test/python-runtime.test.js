'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {
  runtimeInfoFor,
  pythonBinaryName,
  findBundledRuntime,
  stageCts2,
  ensurePythonRuntime,
  describeRuntime,
  PYTHON_VERSION,
} = require('../lib/python-runtime.js');
const { runPythonCli } = require('../lib/convert.js');
const { buildZip } = require('./zipbuilder.js');

const REPO_ROOT = path.join(__dirname, '..', '..');
const FIXTURE = path.join(REPO_ROOT, 'tests', 'fixtures', 'minimal.mfa');

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'cts2rt-'));
}

test('runtimeInfoFor targets the windows embeddable zip', () => {
  const info = runtimeInfoFor('win32');
  assert.equal(info.kind, 'embed');
  assert.match(info.url, new RegExp(`python-${PYTHON_VERSION}-embed-amd64\\.zip$`));
  assert.equal(info.binary, 'python.exe');
  assert.equal(runtimeInfoFor('linux').kind, 'system');
  assert.equal(pythonBinaryName('win32'), 'python.exe');
  assert.equal(pythonBinaryName('linux'), 'python3');
});

test('finds a bundled runtime under resources/runtime/<platform>', () => {
  const repo = tmpdir();
  const dir = path.join(repo, 'app', 'resources', 'runtime', 'win32');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'python.exe'), 'MZ');
  assert.equal(findBundledRuntime(repo, 'win32'), dir);
  fs.rmSync(repo, { recursive: true, force: true });
});

test('first-run provisioning: download, extract, stage, convert (E2E)', async () => {
  const userData = tmpdir();
  let downloadedUrl = null;

  // A fake "portable python": a shell script that execs the real python3.
  // Named python.exe so the win32 provisioning flow is exercised verbatim.
  const fakePython = '#!/bin/sh\nexec /usr/bin/env python3 "$@"\n';
  const zipBuf = buildZip([
    { name: 'python.exe', data: fakePython, method: 8 },
    { name: 'python311.zip', data: 'placeholder-stdlib', method: 0 },
    { name: 'libs/_ctypes.pyd', data: 'x', method: 0 },
  ]);
  const fakeDownload = async (url, dest) => {
    downloadedUrl = url;
    fs.writeFileSync(dest, zipBuf);
  };

  const runtime = await ensurePythonRuntime({
    repoRoot: REPO_ROOT,
    userDataDir: userData,
    platform: 'win32',
    download: fakeDownload,
    runtimeInfo: runtimeInfoFor('win32'),
  });

  assert.match(downloadedUrl, /python\.org/);
  assert.equal(runtime.kind, 'provisioned');
  const bin = path.join(runtime.dir, 'python.exe');
  assert.ok(fs.existsSync(bin));
  fs.chmodSync(bin, 0o755); // make the fake binary spawnable on any OS
  // cts2 package + CLI staged next to the runtime
  assert.ok(fs.existsSync(path.join(runtime.dir, 'cts2', 'mfa.py')));
  assert.ok(fs.existsSync(path.join(runtime.dir, 'cts2_cli.py')));
  assert.match(describeRuntime(runtime), /portable/);

  // Second call must reuse the provisioned runtime (no new download).
  const again = await ensurePythonRuntime({
    repoRoot: REPO_ROOT,
    userDataDir: userData,
    platform: 'win32',
    download: fakeDownload,
    runtimeInfo: runtimeInfoFor('win32'),
  });
  assert.equal(again.kind, 'provisioned');
  assert.equal(downloadedUrl.match(/python\.org/g).length, 1);

  // Run the real converter through the provisioned runtime, exactly as the
  // Electron main process does.
  const out = path.join(userData, 'out.sb3');
  const res = await runPythonCli({
    python: bin,
    args: ['cts2_cli.py', FIXTURE, '-o', out],
    cwd: runtime.dir,
  });
  assert.equal(res.code, 0, res.err);
  assert.ok(fs.existsSync(out));
  assert.ok(fs.readFileSync(out).subarray(0, 2).equals(Buffer.from('PK')));

  fs.rmSync(userData, { recursive: true, force: true });
});

test('stageCts2 copies the package and CLI', () => {
  const dir = tmpdir();
  assert.equal(stageCts2(REPO_ROOT, dir), true);
  assert.ok(fs.existsSync(path.join(dir, 'cts2', 'scratch.py')));
  assert.ok(fs.existsSync(path.join(dir, 'cts2_cli.py')));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('bundled runtime wins and gets staged', async () => {
  const repo = tmpdir();
  const dir = path.join(repo, 'app', 'resources', 'runtime', 'linux');
  fs.mkdirSync(dir, { recursive: true });
  const fake = '#!/bin/sh\nexec /usr/bin/env python3 "$@"\n';
  fs.writeFileSync(path.join(dir, 'python3'), fake);
  fs.chmodSync(path.join(dir, 'python3'), 0o755);
  // minimal converter package so staging has something to copy
  fs.mkdirSync(path.join(repo, 'cts2'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'cts2', 'mfa.py'), '# stub\n');
  fs.writeFileSync(path.join(repo, 'cts2_cli.py'), '# stub\n');

  const runtime = await ensurePythonRuntime({
    repoRoot: repo,
    userDataDir: tmpdir(),
    platform: 'linux',
  });
  assert.equal(runtime.kind, 'bundled');
  assert.ok(fs.existsSync(path.join(dir, 'cts2_cli.py')));
  fs.rmSync(repo, { recursive: true, force: true });
});
