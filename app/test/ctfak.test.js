'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {
  findCtfak,
  ctfakCommand,
  saveCtfakSelection,
  listCtfakInDir,
} = require('../lib/ctfak.js');

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'ctfak-test-'));
}

test('ctfakCommand builds the headless CTFAK 2.0 line', () => {
  const cmd = ctfakCommand('C:\\CTFAK\\CTFAK.Cli.exe', 'C:\\games\\demo.exe');
  assert.equal(cmd[0], 'C:\\CTFAK\\CTFAK.Cli.exe');
  assert.deepEqual(cmd.slice(1), [
    '-path',
    'C:\\games\\demo.exe',
    '-parameters',
    '',
    '-tool',
    'Export as MFA',
    '-closeonfinish',
  ]);
});

test('ctfakCommand falls back to a plain invocation for unknown tools', () => {
  assert.deepEqual(ctfakCommand('some_tool', 'game.exe'), ['some_tool', 'game.exe']);
});

test('finds a CTFAK via CTFAK_BIN', () => {
  const dir = tmpdir();
  const exe = path.join(dir, 'CTFAK.Cli.exe');
  fs.writeFileSync(exe, 'MZ');
  const env = { ...process.env, CTFAK_BIN: exe };
  const found = findCtfak({ env, platform: 'linux', userDataDir: '', repoRoot: '' });
  assert.ok(found);
  assert.equal(found.path, exe);
  assert.equal(found.source, 'CTFAK_BIN');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('user selection (ctfak.json) wins over environment', () => {
  const dir = tmpdir();
  const userData = path.join(dir, 'ud');
  const picked = path.join(dir, 'picked', 'CTFAK.Cli.exe');
  const envOne = path.join(dir, 'env', 'CTFAK.Cli.exe');
  fs.mkdirSync(path.dirname(picked), { recursive: true });
  fs.mkdirSync(path.dirname(envOne), { recursive: true });
  fs.writeFileSync(picked, 'MZ');
  fs.writeFileSync(envOne, 'MZ');
  saveCtfakSelection(userData, picked);
  const env = { ...process.env, CTFAK_BIN: envOne };
  const found = findCtfak({ env, platform: 'linux', userDataDir: userData, repoRoot: '' });
  assert.ok(found);
  assert.equal(found.path, picked);
  assert.equal(found.source, 'user selection');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('scans extraDirs (CTFAK 2.0 build layout, one level deep)', () => {
  const dir = tmpdir();
  const buildDir = path.join(dir, 'build'); // CTFAK2.0/<something>/CTFAK.Cli
  fs.mkdirSync(buildDir, { recursive: true });
  const exe = path.join(buildDir, 'CTFAK.Cli');
  fs.writeFileSync(exe, '#!/bin/sh');
  const found = findCtfak({
    env: {},
    platform: 'linux',
    userDataDir: '',
    repoRoot: '',
    extraDirs: [dir],
  });
  assert.ok(found);
  assert.equal(found.path, exe);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('returns null when nothing matches', () => {
  const dir = tmpdir();
  const found = findCtfak({
    env: {},
    platform: 'linux',
    userDataDir: dir,
    repoRoot: dir,
    extraDirs: [dir],
  });
  assert.equal(found, null);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('listCtfakInDir ignores missing dirs', () => {
  assert.deepEqual(listCtfakInDir('/definitely/not/here'), []);
});
