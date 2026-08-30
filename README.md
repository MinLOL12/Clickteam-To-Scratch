# Clickteam → Scratch / PenguinMod

A **desktop app (Electron)**, a CLI and a local web app that read
**Clickteam Fusion (MMF2 / Fusion 2.5)** `.mfa` projects — and Fusion 2.5
`.exe` files — and export them as a real **Scratch 3 / PenguinMod `.sb3`**
project.

It does **not** require the Clickteam editor, **CTFAK**, or any other
external tool. The Python converter is pure standard library (Pillow and lz4
are optional speed-ups); the desktop app provisions its own portable Python
on first run — **no pip, no venv, no system Python**.

```
input.mfa  ──>  [ cts2 ]  ────────────────────────────────>  output.sb3
input.exe  ──>  [ cts2 built-in pack extractor          ]  ──>  output.sb3   (when the pack holds a raw MFA)
input.exe  ──>  [ cts2 native PAME/PAMU game-data reader ]  ──>  output.sb3   (full rebuild)
```

## It is not "impossible" — here is the route

- **MFA → data**: `.mfa` is an open, documented-by-the-community binary format.
  `cts2/mfa.py` reads the header, image/sound/font banks, globals, frames,
  frame items/instances, layers and the event tree.
- **Images**: Clickteam's bitmaps (24/15/16/32 bpp, zlib "LZX", alpha,
  transparent colour) are decoded and re-encoded as PNG costumes.
- **Scratch output**: `cts2/scratch.py` writes a valid `.sb3` project with a
  Stage, one sprite per frame instance, real PNG costumes, initial positions
  and green-flag scripts (including costume animation).
- **Events**: every event group (conditions/actions/parameters/expressions) is
  parsed into a JSON report and embedded as a visible **Logic-Notes** sprite in
  the SB3. The simple structural subset is already usable; deeper Clickteam
  condition/action → Scratch block translation is an active area. See
  *Supported today* below for exactly what currently makes it into the project.
- **EXE (built-in)**: F2.5 EXEs carry a *pack* (the `.extra` PE section) whose
  files are recovered with a pure-stdlib extractor (`cts2/exe_pack.py`).
  When the pack contains a raw MFA the whole conversion runs with **zero
  external tools**.
- **EXE (full rebuild)**: for EXEs whose game data lives in the PAME/PAMU
  region (most Fusion 2.5 games), `cts2/gamedata.py` reads that region
  **directly** — app header, global values, frame items with animations,
  alterable values and movements, every frame's layers/instances, and the
  image/sound/font banks — including the modified-RC4 chunk encryption used
  by newer builds. Still **zero external tools**. CTFAK is not required and
  not invoked; it remains only an optional last-resort fallback that you can
  point at with `--ctfak` if you happen to have it.

## Install

```bash
git clone <this repo>
cd Clickteam-To-Scratch
python3 -m pip install -r requirements.txt   # optional: Pillow + lz4
```

## CLI usage

```bash
# convert an MFA project
python3 cts2_cli.py game.mfa -o game.sb3

# convert a Fusion 2.5 EXE (built-in readers; nothing to install)
python3 cts2_cli.py game.exe -o game.sb3

# inspect the parsed structure as JSON (great for debugging / porting events)
python3 cts2_cli.py --inspect game.mfa > report.json

# look inside an EXE's pack without converting (built-in extractor)
python3 cts2_cli.py game.exe --pack-info
python3 cts2_cli.py game.exe --pack-dump dumps/

# optional: advanced fallback for exotic builds (.ccn/.apk/.dat/.bin,
# protected EXEs) — only if you already have CTFAK
python3 cts2_cli.py --ctfak /path/to/CTFAK.Cli.exe game.exe -o game.sb3
python3 cts2_cli.py --ctfak-status

# local web app (upload in browser, download .sb3)
python3 cts2_cli.py --web --port 8000
```

## Desktop app (Electron) — no Python setup

The `app/` folder is an Electron desktop app that wraps the same converter.
Drop a `.mfa` **or** `.exe`, click *Convert & save…*, done.

```bash
cd app
npm install
npm start          # run the app (dev mode)
npm test           # unit + integration tests (pure Node, no Electron needed)
npm run dist:win   # build a Windows installer + portable exe (on Windows)
```

What "no Python setup" means in practice:

- On **first convert** the app downloads python.org's *embeddable* Python
  (Windows, ~11 MB, one time) into your user-data folder, extracts it, and
  stages the `cts2` converter next to it. No installer, no pip, no venv, no
  system changes. A progress bar shows what is happening.
- If you'd rather skip even that one-time download, run
  `node app/scripts/bundle-runtime.js win32` once before packaging and the
  portable Python ships inside the installer.
- On dev machines the app happily falls back to a system `python3`
  (Linux/macOS have no official embeddable build).

**EXE support in the app**: EXEs are converted by the built-in readers
(pack extractor first, then the native PAME/PAMU game-data reader) — no
external tools. An optional "CTFAK fallback" card exists only for exotic
cases; you can ignore it.

### Optional CTFAK fallback (advanced)

For `.ccn` / `.apk` / `.dat` / `.bin` inputs, or rare EXE builds the
built-in reader cannot handle, the tool can drive the community
[CTFAK](https://github.com/CTFAK/CTFAK2.0) CLI if you provide it
(a Windows-only third-party .NET tool). The converter invokes CTFAK 2.0
headlessly (`-path … -parameters "" -tool "Export as MFA" -closeonfinish`)
and scans CTFAK's own directory for the freshly written `.mfa`. Nothing is
downloaded or bundled; you must obtain CTFAK yourself:

```bash
export CTFAK_BIN=/path/to/CTFAK.Cli.exe     # or pass --ctfak PATH
python3 cts2_cli.py game.ccn -o game.sb3
```

## Supported today

| Area | Status |
|---|---|
| MMF2 / Fusion 2.5 `.mfa` header, banks, frames | ✅ parsed |
| Active, backdrop, quick-backdrop, counters, text/lives | ✅ parsed |
| Image bank (24/15/16/32 bpp + zlib + alpha + transparent) | ✅ decoded to PNG |
| Sound bank extraction | ✅ parsed (WAV payload kept) |
| Stage + sprites + PNG costumes + green-flag positioning | ✅ generated |
| Costume animation loops | ✅ generated |
| Event groups, conditions/actions/parameters/expressions | ✅ parsed & reported |
| Event → Scratch block transpiler | 🔶 subset; rest visible in **Logic-Notes** |
| `.exe` built-in pack extraction (PE + PackData + zlib, stdlib only) | ✅ |
| `.exe` full rebuild from PAME/PAMU game data (objects, animations, frames, banks, decryption) | ✅ built-in — **no CTFAK** |
| `.exe` compiled event programs | 🔶 reported (size/byte counts); not decoded into blocks |
| `.ccn` / `.apk` / `.dat` / `.bin` front-ends | 🔶 optional CTFAK fallback only |
| MMF 1.5 / CNC builds, Fusion 3, encrypted builds | not yet |
| Desktop app: portable Python auto-provisioning, no pip/venv | ✅ (Windows embeddable / system python3 elsewhere) |

## Web UI

`--web` serves a single-page uploader. It parses the file locally in the
Python process and returns the `.sb3`. The live preview origin is a
`0.0.0.0`-bound server, so it works from the browser preview without any
localhost configuration.

## Tests

```bash
python3 -m unittest discover -s tests -v   # converter + EXE pack + game data
cd app && npm test                          # desktop app (pure Node, no Electron needed)
```

## Legal note

Convert **your own** projects, or game files you have explicit permission to
convert. Clickteam EXEs may be protected and CTFAK-style extraction can be
governed by the rights holder's terms. This project is for interoperability /
educational use and does not bundle any proprietary binaries (the Python
runtime it fetches is CPython under the PSF license, and CTFAK is only ever
located, never shipped).

## Credits / format research

The binary layouts used here are the same facts documented by the community
tools:

- [CTFAK / CTFAK — Clickteam Fusion Army Knife](https://github.com/CTFAK/CTFAK)
- [CTFAK 2.0 — Clickteam Fusion decompiler (archived)](https://github.com/CTFAK/CTFAK2.0)
  — the PE/`.extra` section + PackData + PAME/PAMU game-data layout followed
  by `cts2/exe_pack.py` and `cts2/gamedata.py` comes from its reader
  implementation.
- [Anaconda — Clickteam Fusion decompiler](https://github.com/fnmwolf/Anaconda)

`cts2/` is an independent Python implementation; no CTFAK/Anaconda source code
is shipped or executed by this repository.
