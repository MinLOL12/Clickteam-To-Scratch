#!/usr/bin/env python3
"""Command line entry point for Clickteam To Scratch."""
from __future__ import annotations

import argparse
import json
import os
import sys

from cts2.converter import convert_file
from cts2.ctfak import status as ctfak_status
from cts2.mfa import load_mfa


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cts2", description="Convert Clickteam Fusion projects to Scratch/PenguinMod SB3")
    ap.add_argument("input", nargs="?", help=".mfa or .exe file")
    ap.add_argument("-o", "--output", help="output .sb3 path (default: input basename + .sb3)")
    ap.add_argument("--report", help="write a JSON conversion report")
    ap.add_argument("--inspect", action="store_true", help="just print a JSON report of the parsed project")
    ap.add_argument("--ctfak",
                    help="optional advanced fallback: path to CTFAK.Cli.exe. "
                         "EXE conversion works without it.")
    ap.add_argument("--ctfak-status", action="store_true",
                    help="show whether the optional CTFAK fallback is available, then exit")
    ap.add_argument("--pack-dump", metavar="DIR",
                    help="for .exe input: dump the built-in pack files into DIR (no CTFAK needed)")
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

    if args.pack_info or args.pack_dump:
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
        lower = args.input.lower()
        if lower.endswith((".exe", ".ccn", ".apk", ".dat", ".bin")):
            from cts2 import converter

            with open(args.input, "rb") as fh:
                data = fh.read()
            notes: list = []
            mfa = converter._exe_to_mfa_builtin(args.input, data, notes)
            if mfa is None:
                print(
                    f"Error: could not read '{args.input}' with the built-in "
                    "readers", file=sys.stderr)
                return 1
            report = mfa.report()
            report["notes"] = notes
            print(json.dumps(report, indent=2))
            return 0
        mfa = load_mfa(args.input)
        print(json.dumps(mfa.report(), indent=2))
        return 0

    out = args.output
    if not out:
        base = args.input.rsplit(".", 1)[0]
        out = base + ".sb3"
    try:
        result = convert_file(args.input, out, args.report, ctfak=args.ctfak)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    mfa = result["mfa"]
    print(f"Converted '{mfa.name or args.input}' -> {out}")
    print(f"Frames: {len(mfa.frames)}, Images: {len(mfa.images)}, Sounds: {len(mfa.sounds)}")
    for note in result["report"].get("notes", []):
        print(f"note: {note}")
    print(f"Warnings: {len(result['report'].get('warnings', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
