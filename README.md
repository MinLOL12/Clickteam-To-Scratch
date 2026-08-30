# Clickteam → Scratch / PenguinMod

A CLI + local web app that reads **Clickteam Fusion (MMF2 / Fusion 2.5)** `.mfa`
projects and exports them as a real **Scratch 3 / PenguinMod `.sb3`** project.

It does **not** require the Clickteam editor. Everything runs with plain
Python (Pillow and lz4 are optional speed-ups).

```
input.mfa  ──>  [ clickteam-to-scratch ]  ──>  output.sb3
input.exe  ──>  [ optional CTFAK CLI  ]  ──>  .mfa  ──>  output.sb3
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
- **EXE**: Clickteam EXEs are compressed/obfuscated, so the pipeline uses the
  community **CTFAK** CLI as an external front-end to recover the `.mfa` first.
  This tool auto-detects `CTFAK.Cli.exe` (set `CTFAK_BIN` to override).

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

# inspect the parsed structure as JSON (great for debugging / porting events)
python3 cts2_cli.py --inspect game.mfa > report.json

# local web app (upload in browser, download .sb3)
python3 cts2_cli.py --web --port 8000
```

### EXE support

EXEs require an existing CTFAK CLI:

```bash
export CTFAK_BIN=/path/to/CTFAK.Cli.exe
python3 cts2_cli.py game.exe -o game.sb3
```

If CTFAK is not found the tool tells you exactly which binary to provide. You
only need it for the EXE → MFA step; `.mfa` conversion never needs it.

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
| `.exe` / `.ccn` / `.apk` / `.dat` front-ends | 🔶 requires CTFAK |
| Fusion 3 / encrypted builds | not yet |

## Web UI

`--web` serves a single-page uploader. It parses the file locally in the
Python process and returns the `.sb3`. The live preview origin is a
`0.0.0.0`-bound server, so it works from the browser preview without any
localhost configuration.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Legal note

Convert **your own** projects, or game files you have explicit permission to
convert. Clickteam EXEs may be protected and CTFAK-style extraction can be
governed by the rights holder's terms. This project is for interoperability /
educational use and does not bundle any proprietary binaries.

## Credits / format research

The MFA binary layouts used here are the same facts documented by the
community tools:

- [CTFAK / CTFAK — Clickteam Fusion Army Knife](https://github.com/CTFAK/CTFAK)
- [Anaconda — Clickteam Fusion decompiler](https://github.com/fnmwolf/Anaconda)

`cts2/` is an independent Python implementation; no CTFAK/Anaconda source code
is shipped or executed by this repository.
