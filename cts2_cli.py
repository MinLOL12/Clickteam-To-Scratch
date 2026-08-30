#!/usr/bin/env python3
"""Command line entry point for Clickteam To Scratch."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from cts2.converter import convert_file, load_project
from cts2.ctfak import status as ctfak_status
from cts2.mfa import load_mfa
from cts2.progress import Reporter


# --------------------------------------------------------------------------
# animated progress rendering (stderr)
# --------------------------------------------------------------------------

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class _AnsiRenderer:
    """Draws a live progress bar + step line on a TTY."""

    def __init__(self, out, tick_hz: float = 24.0):
        self.out = out
        self.tick_hz = tick_hz
        self._last = 0.0
        self._last_frame = 0
        self._start = time.monotonic()
        self._shown = False
        self._last_phase = None

    def _fps_ok(self) -> bool:
        now = time.monotonic()
        if now - self._last < 1.0 / self.tick_hz:
            return False
        self._last = now
        return True

    def render(self, ev: dict) -> None:
        if not self._fps_ok():
            return
        self._draw(ev)

    def _draw(self, ev: dict) -> None:
        self._shown = True
        etype = ev.get("type")
        if etype == "warn":
            self._clear()
            self.out.write(f"  ⚠  {ev.get('message', '')}\n")
            self._last_frame = 0
        elif etype == "note":
            self._clear()
            self.out.write(f"  ·  {ev.get('message', '')}\n")
        pct = ev.get("pct", 0)
        overall = ev.get("overall", 0)
        width = 28
        filled = int(width * min(max(pct, 0), 100) / 100.0)
        spin = _SPINNER[int(time.monotonic() * 8) % len(_SPINNER)]
        bar = "█" * filled + "░" * (width - filled)
        line = (f"\r{spin} {bar} {pct:5.1f}%  [{ev.get('phase_title', '')}] "
                f"{ev.get('step', '')[:70]}")
        if ev.get("type") == "done":
            line += f"\n{spin} overall {overall:.0f}% · elapsed {ev.get('elapsed', 0):.1f}s"
        self.out.write(line)
        self.out.flush()
        self._last_frame += 1

    def clear(self) -> None:
        if self._shown:
            self.out.write("\r" + " " * 120 + "\r")
            self.out.flush()
            self._shown = False


class _PlainRenderer:
    """Non-TTY: one line per phase change; warnings/notes stream inline."""

    def __init__(self, out):
        self.out = out
        self._phase = None

    def render(self, ev: dict) -> None:
        etype = ev.get("type")
        if etype in ("warn", "note"):
            tag = "warning" if etype == "warn" else "note"
            self.out.write(f"  [{tag}] {ev.get('message', '')}\n")
            self.out.flush()
        elif etype == "phase" and ev.get("phase_title") != self._phase:
            self._phase = ev.get("phase_title")
            self.out.write(f"→ {ev.get('phase_title')}\n")
            self.out.flush()
        elif etype == "done":
            self.out.write(
                f"✓ {ev.get('phase_title', '')} — {ev.get('overall', 0):.0f}% "
                f"in {ev.get('elapsed', 0):.1f}s\n")
            self.out.flush()


class _JsonRenderer:
    """Machine-readable: one JSON event per line on the given stream."""

    def __init__(self, out):
        self.out = out

    def render(self, ev: dict) -> None:
        self.out.write("[cts2-progress] " + json.dumps(ev) + "\n")
        self.out.flush()


def _make_reporter(progress_mode: str, stderr) -> Reporter:
    if progress_mode == "off":
        return Reporter(sink=None)
    if progress_mode == "json":
        return Reporter(sink=_JsonRenderer(stderr).render)
    if stderr.isatty():
        ren = _AnsiRenderer(stderr)
        return Reporter(sink=ren.render)
    ren = _PlainRenderer(stderr)
    return Reporter(sink=ren.render)


def _print_warnings(report: dict, out) -> None:
    warnings = report.get("warnings", []) or []
    if not warnings:
        out.write("No warnings.\n")
        return
    out.write(f"{len(warnings)} warning(s):\n")
    for i, w in enumerate(warnings, start=1):
        out.write(f"  ⚠  {w}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="cts2",
        description="Convert a Clickteam Fusion game to Scratch/PenguinMod SB3 — "
                    "just point it at the game's .exe. No .mfa needed.")
    ap.add_argument("input", nargs="?",
                    help="the game's .exe (the file you launch) — or an .mfa "
                         "project, a PAME/PAMU data file, or a folder "
                         "containing the game")
    ap.add_argument("-o", "--output", help="output .sb3 path (default: input basename + .sb3)")
    ap.add_argument("--report", help="write a JSON conversion report")
    ap.add_argument("--inspect", action="store_true", help="just print a JSON report of the parsed project")
    ap.add_argument("--progress", choices=["auto", "json", "off"], default="auto",
                    help="progress output: auto (animated on a terminal), json "
                         "([cts2-progress] JSON lines), off")
    ap.add_argument("--ctfak",
                    help="optional advanced fallback: path to CTFAK.Cli.exe. "
                         "EXE conversion works without it.")
    ap.add_argument("--ctfak-status", action="store_true",
                    help="show whether the optional CTFAK fallback is available, then exit")
    ap.add_argument("--pack-dump", metavar="DIR",
                    help="for .exe input: dump the built-in pack files into DIR")
    ap.add_argument("--pack-info", action="store_true",
                    help="for .exe input: print the pack manifest as JSON, then exit")
    ap.add_argument("--web", action="store_true", help="start the local upload web app")
    ap.add_argument("--port", type=int, default=8000, help="web port (default 8000)")
    args = ap.parse_args(argv)

    if args.ctfak:
        os.environ["CTFAK_BIN"] = os.path.abspath(os.path.expanduser(args.ctfak))

    if args.web:
        from cts2.web import serve

        serve(args.port)
        return 0

    if args.ctfak_status:
        print(json.dumps(ctfak_status(), indent=2))
        return 0

    if not args.input:
        ap.error("input file is required unless --web is used")

    is_dir = os.path.isdir(args.input)
    if (args.pack_info or args.pack_dump) and not is_dir:
        from cts2 import exe_pack

        try:
            if args.pack_info:
                print(json.dumps(exe_pack.pack_summary(args.input), indent=2))
            if args.pack_dump:
                files = exe_pack.dump_pack(args.input, args.pack_dump)
                print(f"Dumped {len(files)} pack files to {args.pack_dump}")
        except exe_pack.PackError as exc:
            print(f"Pack error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.inspect:
        notes: list = []
        try:
            if os.path.isdir(args.input):
                mfa, notes = load_project(args.input)
            else:
                with open(args.input, "rb") as fh:
                    data = fh.read()
                from cts2 import detect
                from cts2 import converter

                kind = detect.detect_bytes(data)
                if kind == detect.KIND_MFA:
                    mfa = load_mfa(args.input)
                elif kind == detect.KIND_EXE:
                    mfa = converter._exe_to_mfa_builtin(args.input, data, notes)
                else:
                    mfa, notes = load_project(args.input)
                if mfa is None:
                    print(
                        f"Error: could not read '{args.input}' with the built-in "
                        "readers", file=sys.stderr)
                    return 1
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        report = mfa.report()
        report["notes"] = notes
        print(json.dumps(report, indent=2))
        return 0

    out = args.output
    if not out:
        base = args.input.rstrip("/\\").rsplit(".", 1)[0] if not is_dir else args.input.rstrip("/\\")
        out = base + ".sb3"

    reporter = _make_reporter(args.progress, sys.stderr)
    try:
        result = convert_file(args.input, out, args.report, ctfak=args.ctfak,
                              progress=reporter)
    except Exception as exc:  # noqa: BLE001
        if isinstance(reporter.sink, _AnsiRenderer):
            reporter.sink.clear()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if isinstance(reporter.sink, _AnsiRenderer):
        reporter.sink.clear()
    mfa = result["mfa"]
    report = result["report"]
    print(f"Converted '{mfa.name or args.input}' -> {out}")
    print(f"Frames: {len(mfa.frames)}, Images: {len(mfa.images)}, "
          f"Sounds: {len(mfa.sounds)}, Sprites: {report.get('sprites', 0)}")
    print(f"Events: {report.get('events_total', 0)} groups, "
          f"{report.get('events_mapped', 0)} compiled to "
          f"{report.get('blocks', 0)} Scratch blocks"
          f"{' (+' + str(report.get('unmapped_events', 0)) + ' kept as notes)' if report.get('unmapped_events') else ''}")
    for note in report.get("notes", []):
        print(f"note: {note}")
    print()
    _print_warnings(report, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
