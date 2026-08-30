# Clickteam → Scratch / PenguinMod

A **desktop app (Electron)**, a CLI and a local web app that read
**Clickteam Fusion (MMF2 / Fusion 2.5)** `.mfa` projects — and Fusion 2.5
`.exe` files — and export them as a real **Scratch 3 / PenguinMod `.sb3`**
project.

It does **not** require the Clickteam editor. The Python converter is pure
standard library (Pillow and lz4 are optional speed-ups); the desktop app
provisions its own portable Python on first run — **no pip, no venv, no
system Python**.

```
input.mfa  ──>  [ cts2 ]  ────────────────────────────>  output.sb3
input.exe  ──>  [ cts2 built-in pack extractor ]  ────>  output.sb3   (when the pack holds a raw MFA)
input.exe  ──>  [ CTFAK CLI (auto-located) ]  ──>  .mfa  ──>  output.sb3   (full rebuild)
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
- **EXE (full rebuild)**: for EXEs whose game data is only re-serializable by
  the community **CTFAK** CLI, this tool auto-locates `CTFAK.Cli.exe`
  (user selection, `CTFAK_BIN`, bundled `app/resources/ctfak/`, PATH, common
  build locations) and drives it headlessly with the documented
  `-path / -parameters / -tool "Export as MFA" / -closeonfinish` arguments.

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

# convert a Fusion 2.5 EXE (built-in pack extractor first, CTFAK if needed)
python3 cts2_cli.py game.exe -o game.sb3

# inspect the parsed structure as JSON (great for debugging / porting events)
python3 cts2_cli.py --inspect game.mfa > report.json

# look inside an EXE's pack without converting (built-in extractor)
python3 cts2_cli.py game.exe --pack-info
python3 cts2_cli.py game.exe --pack-dump dumps/

# point at a CTFAK build explicitly (or use CTFAK_BIN)
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

**EXE support in the app**: the built-in pack extractor runs first (no
external tool). If a full rebuild is required, the app shows a **CTFAK**
status card — it auto-detects an existing `CTFAK.Cli.exe` and also lets you
point at one with a single click; the setup card lists the exact steps
(.NET 6 Desktop Runtime → CTFAK 2.0 → requirements zip). CTFAK itself is a
Windows-only third-party .NET tool, so it can only be *located/chosen*, not
created — everything else is automatic.

### EXE support (CLI)

EXEs whose pack does not contain a raw MFA need the community CTFAK CLI:

```bash
export CTFAK_BIN=/path/to/CTFAK.Cli.exe
python3 cts2_cli.py game.exe -o game.sb3
```

The converter invokes CTFAK 2.0 headlessly
(`-path … -parameters "" -tool "Export as MFA" -closeonfinish`) and scans
CTFAK's own directory (it force-chdirs there) for the freshly written `.mfa`.
If CTFAK is not found the tool tells you exactly which binary to provide and
how to get it. You only need it for that one step; `.mfa` conversion and
pack-resident EXE conversion never need it.

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
| `.exe` full rebuild (PAME/PAMU game data → MFA) | 🔶 requires CTFAK (auto-located) |
| `.ccn` / `.apk` / `.dat` / `.bin` front-ends | 🔶 requires CTFAK |
| Fusion 3 / encrypted builds | not yet |
| Desktop app: portable Python auto-provisioning, no pip/venv | ✅ (Windows embeddable / system python3 elsewhere) |

## Web UI

`--web` serves a single-page uploader. It parses the file locally in the
Python process and returns the `.sb3`. The live preview origin is a
`0.0.0.0`-bound server, so it works from the browser preview without any
localhost configuration.

## Tests

```bash
python3 -m unittest discover -s tests -v   # converter + EXE pack extractor + CTFAK
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

The MFA binary layouts used here are the same facts documented by the
community tools:

- [CTFAK / CTFAK — Clickteam Fusion Army Knife](https://github.com/CTFAK/CTFAK)
- [CTFAK 2.0 — Clickteam Fusion decompiler (archived)](https://github.com/CTFAK/CTFAK2.0)
  — the PE/`.extra` section + PackData + PAME/PAMU game-data layout followed
  by `cts2/exe_pack.py` comes from its reader implementation.
- [Anaconda — Clickteam Fusion decompiler](https://github.com/fnmwolf/Anaconda)

`cts2/` is an independent Python implementation; no CTFAK/Anaconda source code
is shipped or executed by this repository.
