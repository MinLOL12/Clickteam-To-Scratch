#!/usr/bin/env node
/**
 * Pre-bundle the portable Python runtime into app/resources/runtime/<plat>
 * so the packaged app needs no first-run download (bigger installer,
 * offline-friendly). Run on the platform you are packaging for:
 *
 *   node scripts/bundle-runtime.js win32
 *
 * The app also provisions the same runtime automatically on first run if
 * the bundled copy is missing - this script is an optimization, not a
 * requirement.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { ensurePythonRuntime, runtimeInfoFor, PYTHON_VERSION } = require('../lib/python-runtime.js');

const repoRoot = path.join(__dirname, '..', '..');

async function main() {
  const platform = process.argv[2] || process.platform;
  const info = runtimeInfoFor(platform);
  if (info.kind !== 'embed') {
    console.error(
      `No embeddable Python binary exists for "${platform}". ` +
        'Only Windows (win32) can be pre-bundled; on other platforms the ' +
        'app uses the system python3.'
    );
    process.exit(1);
  }
  const dest = path.join(repoRoot, 'app', 'resources', 'runtime', platform);
  fs.rmSync(dest, { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });
  console.log(`Bundling Python ${PYTHON_VERSION} (${platform}) into ${dest} ...`);

  // ensurePythonRuntime with a repoRoot that has no bundled runtime will
  // download + extract into userDataDir; we point userDataDir at the
  // resources dir and rename afterwards.
  const staging = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'cts2-bundle-'));
  const runtime = await ensurePythonRuntime({
    repoRoot,
    userDataDir: staging,
    platform,
    onProgress: (p) => {
      if (typeof p === 'object' && p.phase === 'download' && p.total) {
        const pct = Math.round((p.received / p.total) * 100);
        process.stdout.write(`\r  download ${pct}% (${(p.received / 1048576).toFixed(1)} MB)`);
      }
      if (typeof p === 'object' && p.phase === 'extract') process.stdout.write('\r  extracting...');
    },
  });
  process.stdout.write('\n');
  fs.cpSync(path.join(staging, 'runtime', 'python'), dest, { recursive: true });
  fs.rmSync(staging, { recursive: true, force: true });
  // Re-stage the converter against the final location.
  const { stageCts2 } = require('../lib/python-runtime.js');
  stageCts2(repoRoot, dest);
  console.log(`Done. ${dest} is now bundled (committed or shipped as-is).`);
}

main().catch((exc) => {
  console.error(exc);
  process.exit(1);
});
