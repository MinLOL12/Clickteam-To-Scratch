# Clickteam → Scratch / PenguinMod

Turn a **Clickteam Fusion (MMF2 / Fusion 2.5) game into a real Scratch 3 /
PenguinMod `.sb3` project** — by pointing the tool at the game's `.exe`.

**You never need an `.mfa` file.** A game you bought (say, FNaF 1) is a single
executable — `FiveNightsatFreddys.exe` — and that file *is* the whole project:
the tool reads the game data straight out of it and rebuilds everything as a
Scratch project. No Clickteam editor, no CTFAK, no decompiler, no `.mfa`.

It is a **desktop app (Electron)**, a CLI and a local web app. The Python
converter is pure standard library (Pillow and lz4 are optional speed-ups);
the desktop app provisions its own portable Python on first run — **no pip,
no venv, no system Python**.

```
game.exe   ──>  [ cts2 built-in readers ]  ──>  output.sb3      <- the normal path
game folder ──> [ finds the .exe for you  ]  ──>  output.sb3
data file (PAME/PAMU) ──> [ direct reader ]  ──>  output.sb3
project.mfa ──>  [ MFA reader              ]  ──>  output.sb3      <- only if you have one
```

## How a game becomes Scratch without an .mfa

A Fusion 2.5 executable is laid out as::

    [ PE executable (the Clickteam runtime) ]
    [ optional "pack" of extra files        ]
    [ game data: PAME/PAMU chunk list       ]

The game-data region is the compiled project: the app header, global
values/strings, every object with its animations, alterable values and
movements, every frame's layers and instances, and the image/sound/font
banks. `cts2/gamedata.py` reads it **directly** — including the
modified-RC4 chunk encryption used by newer builds — so the conversion runs
with **zero external tools**. CTFAK is not required and not invoked; it
remains only an optional last-resort fallback for exotic builds
(`--ctfak`, off by default).

Files are routed by **content, not extension**: an `MZ` header means
executable, `PAME`/`PAMU` means raw game data, `MFU2`/`MFA2` means an MFA
project. A renamed or extension-less game file converts just fine.

## Install

```bash
git clone <this repo>
cd Clickteam-To-Scratch
python3 -m pip install -r requirements.txt   # optional: Pillow + lz4
```

## CLI usage

```bash
# convert a game straight from its .exe — the file you launch to play
python3 cts2_cli.py FiveNightsatFreddys.exe -o five_nights.sb3

# or just point it at the extracted game folder; the .exe is found for you
python3 cts2_cli.py "Five Nights at Freddy's/" -o five_nights.sb3

# inspect the parsed structure as JSON (great for debugging / porting events)
python3 cts2_cli.py --inspect FiveNightsatFreddys.exe > report.json

# look inside an EXE's pack without converting
python3 cts2_cli.py FiveNightsatFreddys.exe --pack-info
python3 cts2_cli.py FiveNightsatFreddys.exe --pack-dump dumps/

# optional: advanced fallback for exotic builds (.ccn/.apk/.bin, protected
# EXEs) — only if you already have CTFAK
python3 cts2_cli.py --ctfak /path/to/CTFAK.Cli.exe game.ccn -o game.sb3
python3 cts2_cli.py --ctfak-status

# local web app (upload in browser, download .sb3)
python3 cts2_cli.py --web --port 8000
```

`.mfa` project files convert too (`python3 cts2_cli.py project.mfa`) — but
only for people who exported one from the Clickteam editor themselves. You
do not need one to convert a game.

## Desktop app (Electron) — no Python setup

The `app/` folder is an Electron desktop app that wraps the same converter.
Drop the game's `.exe`, click *Convert & save…*, done (an `.mfa` works too).

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

## Supported today

| Area | Status |
|---|---|
| Game `.exe` → SB3, built-in (pack + PAME/PAMU game data, decryption) | ✅ **no .mfa, no CTFAK** |
| Folder input (auto-finds the game file inside an extracted build) | ✅ |
| Content-based input detection (renamed / extension-less files) | ✅ |
| Raw PAME/PAMU data files → SB3 | ✅ |
| MMF2 / Fusion 2.5 `.mfa` projects (if you happen to have one) | ✅ |
| Active, backdrop, quick-backdrop, counters, text/lives | ✅ parsed |
| Image bank (24/15/16/32 bpp + zlib + LZ4/LZ4M + alpha + transparent) | ✅ decoded to PNG |
| Sound bank extraction | ✅ parsed (WAV payload kept) |
| Stage + sprites + PNG costumes + green-flag positioning | ✅ generated |
| Costume animation loops | ✅ generated |
| Event groups, conditions/actions/parameters/expressions | ✅ parsed & reported |
| Event → Scratch block transpiler | 🔶 subset; rest visible in **Logic-Notes** |
| `.exe` compiled event programs | 🔶 reported (size/byte counts); not decoded into blocks |
| `.ccn` / `.apk` / `.bin` front-ends | 🔶 optional CTFAK fallback only |
| MMF 1.5 / CNC builds, Fusion 3, encrypted builds | not yet |
| Desktop app: portable Python auto-provisioning, no pip/venv | ✅ (Windows embeddable / system python3 elsewhere) |

## Web UI

`--web` serves a single-page uploader: drop in the game's `.exe`, get the
`.sb3`. It parses the file locally in the Python process. The live preview
origin is a `0.0.0.0`-bound server, so it works from the browser preview
without any localhost configuration.

## Tests

```bash
python3 -m unittest discover -s tests -v   # converter + detection + EXE + game data
cd app && npm test                          # desktop app (pure Node, no Electron needed)
```

## Legal note

Convert **your own** projects, or game files you have explicit permission to
convert (a game you own, for interoperability / preservation / educational
use). Clickteam EXEs may be protected and extraction can be governed by the
rights holder's terms. This project does not bundle any proprietary binaries
or game assets (the Python runtime it fetches is CPython under the PSF
license, and CTFAK is only ever located, never shipped).

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
