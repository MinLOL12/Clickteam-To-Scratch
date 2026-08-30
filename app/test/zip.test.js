'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { listZipEntries, readZipEntry, extractZip } = require('../lib/zip.js');
const { buildZip } = require('./zipbuilder.js');

test('lists entries (stored + deflate)', () => {
  const buf = buildZip([
    { name: 'a.txt', data: 'hello', method: 0 },
    { name: 'dir/b.bin', data: Buffer.from([0, 1, 2, 3, 4, 5, 6, 7]), method: 8 },
  ]);
  const entries = listZipEntries(buf);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].name, 'a.txt');
  assert.equal(entries[0].method, 0);
  assert.equal(entries[1].name, 'dir/b.bin');
  assert.equal(entries[1].method, 8);
});

test('reads stored and deflated entries', () => {
  const payload = Buffer.from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  const buf = buildZip([
    { name: 'a.txt', data: 'hello world', method: 0 },
    { name: 'b.bin', data: payload, method: 8 },
  ]);
  assert.equal(readZipEntry(buf, 'a.txt').toString(), 'hello world');
  assert.ok(readZipEntry(buf, 'b.bin').equals(payload));
  assert.throws(() => readZipEntry(buf, 'missing'), /not found/);
});

test('extracts into a directory', () => {
  const buf = buildZip([
    { name: 'top.txt', data: 'x', method: 0 },
    { name: 'sub/nested.txt', data: 'y', method: 8 },
  ]);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zip-'));
  const written = extractZip(buf, dir);
  assert.equal(written.length, 2);
  assert.equal(fs.readFileSync(path.join(dir, 'top.txt'), 'utf8'), 'x');
  assert.equal(fs.readFileSync(path.join(dir, 'sub', 'nested.txt'), 'utf8'), 'y');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('rejects non-zip data', () => {
  assert.throws(() => listZipEntries(Buffer.from('not a zip')), /not a ZIP/);
});
