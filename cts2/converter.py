"""High-level conversion: MFA/EXE -> Scratch/PenguinMod SB3."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

from .mfa import MFA, load_mfa, load_mfa_bytes
from .scratch import build_project


def find_ctfak_binary() -> Optional[str]:
    """Locate a CTFAK CLI binary used to turn a Clickteam EXE into an MFA."""
    candidates = []
    for name in ("CTFAK.Cli.exe", "CTFAK.exe"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    env = os.environ.get("CTFAK_BIN")
    if env:
        candidates.append(env)
    testpaths = [
        "CTFAK/CTFAK.Cli.exe",
        "CTFAK.exe",
        "ctfak/CTFAK.Cli.exe",
        os.path.expanduser("~/CTFAK/CTFAK.Cli.exe"),
        os.path.expanduser("~/ctfak/CTFAK.Cli.exe"),
    ]
    for p in testpaths:
        if os.path.isfile(p):
            candidates.append(p)
    return candidates[0] if candidates else None


def exe_to_mfa(exe_path: str, workdir: Optional[str] = None) -> str:
    """Run CTFAK to unpack an EXE to an MFA (requires a CTFAK binary)."""
    ctfak = find_ctfak_binary()
    if not ctfak:
        raise RuntimeError(
            "EXE conversion needs the community 'CTFAK' CLI. "
            "Install it and set CTFAK_BIN=/path/to/CTFAK.Cli.exe, "
            "then convert the generated .mfa with this tool. "
            "CTFAK: https://github.com/CTFAK/CTFAK"
        )
    tmp = workdir or tempfile.mkdtemp(prefix="cts2_")
    cmd = [ctfak, os.path.abspath(exe_path)]
    proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=600)
    # CTFAK writes the MFA into the working dir (out.mfa or game name .mfa)
    mfa = None
    for root, _, files in os.walk(tmp):
        for f in files:
            if f.lower().endswith(".mfa"):
                candidate = os.path.join(root, f)
                if mfa is None or os.path.getsize(candidate) > os.path.getsize(mfa):
                    mfa = candidate
    if not mfa:
        detail = (proc.stdout or "") + (proc.stderr or "")
        raise RuntimeError("CTFAK produced no .mfa.\n" + detail[-2000:])
    return mfa


def convert_file(input_path: str, output_path: Optional[str] = None,
                 report_path: Optional[str] = None) -> dict:
    """Convert a .mfa or .exe file to an SB3 project.

    Returns {'project': bytes, 'mfa': MFA, 'report': dict}.
    """
    lower = input_path.lower()
    if lower.endswith((".exe", ".ccn", ".apk", ".dat", ".bin")):
        mfa_file = exe_to_mfa(input_path)
        mfa = load_mfa(mfa_file)
    else:
        mfa = load_mfa(input_path)
    sb3, report = build_project(mfa)
    if output_path:
        with open(output_path, "wb") as fh:
            fh.write(sb3)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return {"project": sb3, "mfa": mfa, "report": report}


def convert_bytes(data: bytes, input_name: str = "project.mfa") -> dict:
    if input_name.lower().endswith((".exe", ".ccn", ".apk", ".dat", ".bin")):
        # In web mode, we write to disk so CTFAK can consume it.
        tmp = tempfile.mkdtemp(prefix="cts2_up_")
        src = os.path.join(tmp, input_name)
        with open(src, "wb") as fh:
            fh.write(data)
        mfa_file = exe_to_mfa(src, workdir=tmp)
        mfa = load_mfa(mfa_file)
    else:
        mfa = load_mfa_bytes(data)
    sb3, report = build_project(mfa)
    return {"project": sb3, "mfa": mfa, "report": report}
