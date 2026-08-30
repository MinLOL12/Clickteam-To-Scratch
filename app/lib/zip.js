/**
 * Minimal ZIP reader (no dependencies) - just enough to extract the
 * Python "embeddable" distribution zip during first-run setup.
 * Supports stored (method 0) and deflate (method 8) entries via the
 * central directory, and handles zip64-free archives (which is what
 * python.org ships).
 */
'use strict';
const { inflateRawSync } = require('node:zlib');
const fs = require('node:fs');
const path = require('node:path');

const EOCD_SIG = 0x06054b50;
const CD_SIG = 0x02014b50;
const LOCAL_SIG = 0x04034b50;

function findEocd(buf) {
  // EOCD is the last structure; scan backwards from the tail.
  const min = Math.max(0, buf.length - 22 - 65536);
  for (let i = buf.length - 22; i >= min; i--) {
    if (buf.readUInt32LE(i) === EOCD_SIG) return i;
  }
  throw new Error('not a ZIP file (no end-of-central-directory record)');
}

function listZipEntries(buf) {
  const eocd = findEocd(buf);
  const count = buf.readUInt16LE(eocd + 10);
  let offset = buf.readUInt32LE(eocd + 16);
  const entries = [];
  for (let i = 0; i < count; i++) {
    if (offset + 46 > buf.length || buf.readUInt32LE(offset) !== CD_SIG) {
      throw new Error('corrupt central directory');
    }
    const method = buf.readUInt16LE(offset + 10);
    const compSize = buf.readUInt32LE(offset + 20);
    const uncompSize = buf.readUInt32LE(offset + 24);
    const nameLen = buf.readUInt16LE(offset + 28);
    const extraLen = buf.readUInt16LE(offset + 30);
    const commentLen = buf.readUInt16LE(offset + 32);
    const localOffset = buf.readUInt32LE(offset + 42);
    const name = buf.toString('utf8', offset + 46, offset + 46 + nameLen);
    entries.push({ name, method, compSize, uncompSize, localOffset });
    offset += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

function readZipEntry(buf, name) {
  const entry = listZipEntries(buf).find((e) => e.name === name);
  if (!entry) throw new Error(`entry not found in zip: ${name}`);
  const p = entry.localOffset;
  if (p + 30 > buf.length || buf.readUInt32LE(p) !== LOCAL_SIG) {
    throw new Error(`corrupt local header for ${name}`);
  }
  const nameLen = buf.readUInt16LE(p + 26);
  const extraLen = buf.readUInt16LE(p + 28);
  const start = p + 30 + nameLen + extraLen;
  const raw = buf.subarray(start, start + entry.compSize);
  if (entry.method === 0) return Buffer.from(raw);
  if (entry.method === 8) return inflateRawSync(raw);
  throw new Error(`unsupported zip method ${entry.method} for ${name}`);
}

/** Extract every file in `buf` into `outDir`, returning the list of written paths. */
function extractZip(buf, outDir, { onProgress } = {}) {
  const entries = listZipEntries(buf).filter((e) => !e.name.endsWith('/'));
  const written = [];
  entries.forEach((entry, i) => {
    // Guard against path traversal in zip entry names.
    const safe = entry.name
      .replace(/\\/g, '/')
      .split('/')
      .filter((s) => s !== '..' && s !== '')
      .join(path.sep);
    if (!safe) return;
    const target = path.join(outDir, safe);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, readZipEntry(buf, entry.name));
    written.push(target);
    if (onProgress) onProgress({ phase: 'extract', index: i + 1, total: entries.length, file: entry.name });
  });
  return written;
}

module.exports = { listZipEntries, readZipEntry, extractZip };
