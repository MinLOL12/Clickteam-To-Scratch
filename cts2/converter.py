"""High-level conversion: MFA/EXE -> Scratch/PenguinMod SB3."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

from . import ctfak as ctfak_mod
from . import exe_pack, gamedata
from .mfa import MFA, load_mfa, load_mfa_bytes
from .scratch import build_project

# Extensions that are not .mfa; .exe is fully handled by the built-in
# readers. Other formats (.ccn/.apk/.dat/.bin) only have the optional
# CTFAK route.
_EXE_LIKE = (".exe",)
_CTFAK_ONLY_EXTS = (".ccn", ".apk", ".dat", ".bin")


def find_ctfak_binary(hint: Optional[str] = None) -> Optional[str]:
    return ctfak_mod.find_ctfak_binary(hint)


def exe_to_mfa(exe_path: str, ctfak: Optional[str] = None,
               workdir: Optional[str] = None) -> str:
    """Run CTFAK to unpack an EXE to an MFA (optional fallback only)."""
    return ctfak_mod.exe_to_mfa(exe_path, ctfak=ctfak, workdir=workdir)


def _exe_to_mfa_builtin(exe_path: str, data: bytes,
                        notes: list) -> Optional[MFA]:
    """Fast path: recover the MFA using only the built-in readers.

    1. Raw MFA inside the EXE pack, or
    2. the native PAME/PAMU game-data reader (full rebuild, no tools).
    """
    try:
        found = exe_pack.extract_mfa_from_exe(data)
    except Exception as exc:  # noqa: BLE001
        found = None
        notes.append(f"built-in pack read failed: {exc}")
    if found:
        name, mfa_data = found
        notes.append(
            f"extracted raw MFA ({name}) from the EXE pack without CTFAK"
        )
        return load_mfa_bytes(mfa_data)
    try:
        mfa, gnotes = gamedata.load_game_data_from_exe(data)
    except gamedata.GameDataError as exc:
        notes.append(f"built-in game-data reader: {exc}")
        return None
    notes.append(
        "rebuilt the project from the EXE game data (PAME/PAMU) with the "
        "built-in reader — no CTFAK needed"
    )
    notes.extend(gnotes)
    return mfa


def _ctfak_unavailable_message(input_path: str) -> str:
    name = os.path.basename(input_path)
    return (
        f"Could not convert '{name}' with the built-in readers. Plain .mfa "
        "files, and Fusion 2.5 EXEs whose pack or game data is readable, "
        "convert without any external tools. This file needs the optional "
        "CTFAK fallback (advanced builds only): pass --ctfak "
        "/path/to/CTFAK.Cli.exe, or set CTFAK_BIN."
    )


def _load_mfa_from_any(input_path: str, ctfak_hint: Optional[str]) -> tuple:
    """Return (mfa, notes). Built-in readers first; CTFAK is optional."""
    lower = input_path.lower()
    notes = []
    if lower.endswith(_EXE_LIKE):
        with open(input_path, "rb") as fh:
            data = fh.read()
        mfa = _exe_to_mfa_builtin(input_path, data, notes)
        if mfa is not None:
            return mfa, notes
        notes.append("built-in readers could not convert this EXE")
        # Optional advanced fallback: only if the user configured one.
        if not (ctfak_hint or ctfak_mod.find_ctfak_binary()):
            raise RuntimeError(_ctfak_unavailable_message(input_path))
        tmp = tempfile.mkdtemp(prefix="cts2_")
        mfa_file = exe_to_mfa(input_path, ctfak=ctfak_hint, workdir=tmp)
        notes.append(f"CTFAK produced {mfa_file}")
        return load_mfa(mfa_file), notes
    if lower.endswith(_CTFAK_ONLY_EXTS):
        # These formats have no built-in reader; CTFAK is genuinely
        # optional-and-required here, and never auto-installed.
        if not (ctfak_hint or ctfak_mod.find_ctfak_binary()):
            raise RuntimeError(_ctfak_unavailable_message(input_path))
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
    if lower.endswith(_EXE_LIKE):
        mfa = _exe_to_mfa_builtin(input_name, data, notes)
        if mfa is None:
            if not (ctfak or ctfak_mod.find_ctfak_binary()):
                raise RuntimeError(_ctfak_unavailable_message(input_name))
            tmp = tempfile.mkdtemp(prefix="cts2_up_")
            src = os.path.join(tmp, input_name)
            with open(src, "wb") as fh:
                fh.write(data)
            mfa_file = exe_to_mfa(src, ctfak=ctfak, workdir=tmp)
            notes.append(f"CTFAK produced {mfa_file}")
            mfa = load_mfa(mfa_file)
    elif lower.endswith(_CTFAK_ONLY_EXTS):
        if not (ctfak or ctfak_mod.find_ctfak_binary()):
            raise RuntimeError(_ctfak_unavailable_message(input_name))
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
