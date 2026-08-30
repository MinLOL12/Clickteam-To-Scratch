'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { isExeLike, defaultOutputName, buildCliArgs, runPythonCli } = require('../lib/convert.js');
const { findSystemPython } = require('../lib/python-runtime.js');

const REPO_ROOT = path.join(__dirname, '..', '..');
const FIXTURE = path.join(REPO_ROOT, 'tests', 'fixtures', 'minimal.mfa');

test('isExeLike recognizes the CTFAK-fed extensions', () => {
  assert.ok(isExeLike('game.exe'));
  assert.ok(isExeLike('game.CCN'));
  assert.ok(isExeLike('app.apk'));
  assert.ok(isExeLike('data.bin'));
  assert.ok(!isExeLike('game.mfa'));
  assert.ok(!isExeLike(''));
});

test('defaultOutputName swaps the extension for .sb3', () => {
  assert.equal(
    defaultOutputName('/g/my game.exe'),
    path.join('/g', 'my game.sb3')
  );
  assert.equal(defaultOutputName('a.mfa'), path.join('.', 'a.sb3'));
});

test('buildCliArgs assembles the cts2_cli.py invocation', () => {
  assert.deepEqual(
    buildCliArgs({ input: 'a.mfa', output: 'a.sb3', ctfak: null }),
    ['cts2_cli.py', 'a.mfa', '-o', 'a.sb3', '--progress', 'json']
  );
  assert.deepEqual(
    buildCliArgs({ input: 'a.exe', output: 'a.sb3', ctfak: 'C:\\CTFAK\\CTFAK.Cli.exe' }),
    ['cts2_cli.py', 'a.exe', '-o', 'a.sb3', '--ctfak', 'C:\\CTFAK\\CTFAK.Cli.exe', '--progress', 'json']
  );
  assert.deepEqual(
    buildCliArgs({ input: 'a.mfa', output: 'a.sb3', progress: false }),
    ['cts2_cli.py', 'a.mfa', '-o', 'a.sb3']
  );
});

test('runPythonCli converts a real MFA via the system python', { skip: !fs.existsSync(FIXTURE) }, async () => {
  const sys = findSystemPython(process.platform);
  if (!sys) {
    // No system python in this environment; the provisioning test covers
    // the runtime path with a fake portable python instead.
    return;
  }
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cts2conv-'));
  const out = path.join(tmp, 'out.sb3');
  const lines = [];
  const res = await runPythonCli({
    python: sys.path,
    args: [...(sys.args || []), 'cts2_cli.py', FIXTURE, '-o', out],
    cwd: REPO_ROOT,
    onLog: (l) => lines.push(l),
  });
  assert.equal(res.code, 0, res.err);
  assert.ok(fs.existsSync(out));
  assert.ok(lines.some((l) => l.includes('Converted')));
  fs.rmSync(tmp, { recursive: true, force: true });
});
