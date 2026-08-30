#!/usr/bin/env python3
"""Command line entry point for Clickteam To Scratch."""
from __future__ import annotations

import argparse
import json
import sys

from cts2.converter import convert_file
from cts2.mfa import load_mfa


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cts2", description="Convert Clickteam Fusion projects to Scratch/PenguinMod SB3")
    ap.add_argument("input", nargs="?", help=".mfa or .exe file")
    ap.add_argument("-o", "--output", help="output .sb3 path (default: input basename + .sb3)")
    ap.add_argument("--report", help="write a JSON conversion report")
    ap.add_argument("--inspect", action="store_true", help="just print a JSON report of the parsed project")
    ap.add_argument("--web", action="store_true", help="start the local upload web app")
    ap.add_argument("--port", type=int, default=8000, help="web port (default 8000)")
    args = ap.parse_args(argv)

    if args.web:
        from cts2.web import serve

        serve(args.port)
        return 0

    if not args.input:
        ap.error("input file is required unless --web is used")

    if args.inspect:
        mfa = load_mfa(args.input)
        print(json.dumps(mfa.report(), indent=2))
        return 0

    out = args.output
    if not out:
        base = args.input.rsplit(".", 1)[0]
        out = base + ".sb3"
    try:
        result = convert_file(args.input, out, args.report)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    mfa = result["mfa"]
    print(f"Converted '{mfa.name or args.input}' -> {out}")
    print(f"Frames: {len(mfa.frames)}, Images: {len(mfa.images)}, Sounds: {len(mfa.sounds)}")
    print(f"Warnings: {len(result['report'].get('warnings', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
