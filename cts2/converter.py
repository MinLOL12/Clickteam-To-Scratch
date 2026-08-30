"""High-level conversion: MFA/EXE -> Scratch/PenguinMod SB3."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

from . import ctfak as ctfak_mod
from . import exe_pack
from .mfa import MFA, load_mfa, load_mfa_bytes
from .scratch import build_project

# Extensions handled by CTFAK when the built-in pack extractor can't help.
_CTFAK_EXTS = (".exe", ".ccn", ".apk", ".dat", ".bin")


def find_ctfak_binary(hint: Optional[str] = None) -> Optional[str]:
    return ctfak_mod.find_ctfak_binary(hint)


def exe_to_mfa(exe_path: str, ctfak: Optional[str] = None,
               workdir: Optional[str] = None) -> str:
    """Run CTFAK to unpack an EXE to an MFA (requires a CTFAK binary)."""
    return ctfak_mod.exe_to_mfa(exe_path, ctfak=ctfak, workdir=workdir)


def _load_mfa_from_any(input_path: str, ctfak_hint: Optional[str]) -> tuple:
    """Return (mfa, notes). Tries the built-in EXE pack extractor first."""
    lower = input_path.lower()
    notes = []
    if lower.endswith(_CTFAK_EXTS):
        if lower.endswith(".exe"):
            # Fast path: the built-in pack extractor (no external tools).
            try:
                found = exe_pack.extract_mfa_from_exe(input_path)
            except Exception as exc:  # noqa: BLE001
                found = None
                notes.append(f"built-in pack read failed: {exc}")
            if found:
                name, data = found
                notes.append(
                    f"extracted raw MFA ({name}) from the EXE pack without CTFAK"
                )
                return load_mfa_bytes(data), notes
            notes.append(
                "EXE pack does not contain a raw MFA; using CTFAK for the "
                "full EXE -> MFA rebuild"
            )
        # CTFAK path (also the only route for .ccn/.apk/.dat/.bin).
        tmp = tempfile.mkdtemp(prefix="cts2_")
        mfa_file = exe_to_mfa(input_path, ctfak=ctfak_hint, workdir=tmp)
        notes.append(f"CTFAK produced {mfa_file}")
        return load_mfa(mfa_file), notes
    mfa = load_mfa(input_path)
    return mfa, notes


def convert_file(input_path: str, output_path: Optional[str] = None,
                 report_path: Optional[str] = None,
                 ctfak: Optional[str] = None) -> dict:
    """Convert a .mfa or .exe file to an SB3 project.

    Returns {'project': bytes, 'mfa': MFA, 'report': dict}.
    """
    mfa, notes = _load_mfa_from_any(input_path, ctfak)
    sb3, report = build_project(mfa)
    if notes:
        report.setdefault("notes", []).extend(notes)
    if output_path:
        with open(output_path, "wb") as fh:
            fh.write(sb3)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return {"project": sb3, "mfa": mfa, "report": report}


def convert_bytes(data: bytes, input_name: str = "project.mfa",
                  ctfak: Optional[str] = None) -> dict:
    lower = input_name.lower()
    notes = []
    if lower.endswith(_CTFAK_EXTS):
        if lower.endswith(".exe"):
            found = None
            try:
                found = exe_pack.extract_mfa_from_exe(data)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"built-in pack read failed: {exc}")
            if found:
                name, mfa_data = found
                notes.append(
                    f"extracted raw MFA ({name}) from the EXE pack without CTFAK"
                )
                mfa = load_mfa_bytes(mfa_data)
            else:
                notes.append(
                    "EXE pack does not contain a raw MFA; using CTFAK for the "
                    "full EXE -> MFA rebuild"
                )
                tmp = tempfile.mkdtemp(prefix="cts2_up_")
                src = os.path.join(tmp, input_name)
                with open(src, "wb") as fh:
                    fh.write(data)
                mfa_file = exe_to_mfa(src, ctfak=ctfak, workdir=tmp)
                notes.append(f"CTFAK produced {mfa_file}")
                mfa = load_mfa(mfa_file)
        else:
            tmp = tempfile.mkdtemp(prefix="cts2_up_")
            src = os.path.join(tmp, input_name)
            with open(src, "wb") as fh:
                fh.write(data)
            mfa_file = exe_to_mfa(src, ctfak=ctfak, workdir=tmp)
            notes.append(f"CTFAK produced {mfa_file}")
            mfa = load_mfa(mfa_file)
    else:
        mfa = load_mfa_bytes(data)
    sb3, report = build_project(mfa)
    if notes:
        report.setdefault("notes", []).extend(notes)
    return {"project": sb3, "mfa": mfa, "report": report}
