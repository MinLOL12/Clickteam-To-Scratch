/**
 * Run the Python converter (cts2_cli.py) and stream its output.
 * Electron-free and unit-testable.
 */
'use strict';
const { spawn } = require('node:child_process');
const path = require('node:path');

const EXE_LIKE = /\.(exe|ccn|apk|dat|bin)$/i;

function isExeLike(fileName) {
  return EXE_LIKE.test(String(fileName || ''));
}

function defaultOutputName(inputPath) {
  const base = path.basename(inputPath);
  const dot = base.lastIndexOf('.');
  return path.join(path.dirname(inputPath), (dot > 0 ? base.slice(0, dot) : base) + '.sb3');
}

/** CLI args for cts2_cli.py given the resolved inputs. */
function buildCliArgs({ input, output, report, ctfak }) {
  const args = ['cts2_cli.py', input, '-o', output];
  if (report) args.push('--report', report);
  if (ctfak) args.push('--ctfak', ctfak);
  return args;
}

/**
 * Spawn the python CLI. Resolves { code, out, err }.
 * onLog(line) receives every output line as it arrives.
 */
function runPythonCli({ python, args, cwd, onLog, timeoutMs = 20 * 60 * 1000 }) {
  return new Promise((resolve) => {
    let out = '';
    let err = '';
    let finished = false;
    const finish = (code) => {
      if (finished) return;
      finished = true;
      resolve({ code, out, err });
    };
    let child;
    try {
      child = spawn(python, args, { cwd });
    } catch (exc) {
      err = String(exc);
      finish(-1);
      return;
    }
    const timer = setTimeout(() => {
      try {
        child.kill('SIGTERM');
      } catch {
        /* already dead */
      }
      err += `\n[cts2] timed out after ${Math.round(timeoutMs / 1000)}s`;
      finish(-2);
    }, timeoutMs);

    const forward = (data, sink) => {
      for (const line of data.toString('utf8').split(/\r?\n/)) {
        if (line) {
          sink(line);
          if (onLog) onLog(line);
        }
      }
    };
    child.stdout.on('data', (d) => forward(d, (l) => (out += l + '\n')));
    child.stderr.on('data', (d) => forward(d, (l) => (err += l + '\n')));
    child.on('error', (exc) => {
      err += String((exc && exc.message) || exc) + '\n';
      clearTimeout(timer);
      finish(-1);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      finish(code == null ? -1 : code);
    });
  });
}

module.exports = { isExeLike, defaultOutputName, buildCliArgs, runPythonCli, EXE_LIKE };
