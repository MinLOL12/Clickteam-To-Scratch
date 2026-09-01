"""High-level conversion: game .exe / .mfa / folder -> Scratch SB3.

Routing is by *content*, not extension (see :mod:`cts2.detect`):

1. ``MZ`` executable -> built-in pack extractor, then the native PAME/PAMU
   game-data reader. This is the normal path: point the tool at the game's
   ``.exe`` (e.g. ``FiveNightsatFreddys.exe``) — **no .mfa is ever needed**.
2. ``PAME``/``PAMU`` at offset 0 -> raw game-data file, read directly.
3. ``MFU2``/``MFA2`` -> a raw MFA project file.
4. Anything else -> the readers are tried anyway; the optional CTFAK
   fallback is used only if the user configured one.
5. A *directory* input is scanned for the game automatically.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional, Tuple

from . import audio
from . import ctfak as ctfak_mod
from . import detect, exe_pack, gamedata
from .mfa import MFA, load_mfa, load_mfa_bytes
from .scratch import build_project

# Extensions that hint at game data; the content sniff decides, these only
# order the folder scan and pick the friendliest error message.
_EXE_LIKE = (".exe",)
_CTFAK_ONLY_EXTS = (".ccn", ".apk", ".dat", ".bin")
_FOLDER_HINT_EXTS = tuple(
    dict.fromkeys(_EXE_LIKE + _CTFAK_ONLY_EXTS + (".app", ".pam", ".cca"))
)


def _note_audio_policy(report: dict, progress) -> None:
    """Expose the temporary no-audio policy in every conversion result."""
    if audio.EXTRACTION_ENABLED:
        return
    report.setdefault("notes", []).append(audio.DISABLED_NOTE)
    progress.note(audio.DISABLED_NOTE)


def find_ctfak_binary(hint: Optional[str] = None) -> Optional[str]:
    return ctfak_mod.find_ctfak_binary(hint)


def exe_to_mfa(exe_path: str, ctfak: Optional[str] = None,
               workdir: Optional[str] = None) -> str:
    """Run CTFAK to unpack an EXE to an MFA (optional fallback only)."""
    return ctfak_mod.exe_to_mfa(exe_path, ctfak=ctfak, workdir=workdir)


# --------------------------------------------------------------------------
# folder input: find the game inside an extracted game directory
# --------------------------------------------------------------------------

def iter_folder_candidates(folder: str) -> List[str]:
    """Return plausible game files in ``folder``, most likely first.

    Order: executables first (largest first), then other data-ish files
    (largest first).  Extensions only *order* the list; whether a file is
    actually readable is decided later by the content sniff.
    """
    scored: List[Tuple[Tuple[int, int], str]] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            path = os.path.join(root, fn)
            try:
                size = os.path.getsize(path)
            except OSError:  # pragma: no cover - unreadable entry
                continue
            ext = os.path.splitext(fn)[1].lower()
            is_exe = 1 if ext in _EXE_LIKE else 0
            hint = 1 if ext in _FOLDER_HINT_EXTS else 0
            if not is_exe and not hint:
                continue
            scored.append(((is_exe, size, hint), path))
    scored.sort(reverse=True)
    return [path for _score, path in scored]


def find_game_in_folder(folder: str) -> Optional[str]:
    """Return the single most likely game file inside ``folder``."""
    candidates = iter_folder_candidates(folder)
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# loading (content-based)
# --------------------------------------------------------------------------

def _exe_to_mfa_builtin(exe_path: str, data: bytes,
                        notes: list, progress=None) -> Optional[MFA]:
    """Fast path: recover the game using only the built-in readers.

    1. Raw MFA inside the EXE pack, or
    2. the native PAME/PAMU game-data reader (full rebuild, no tools).
    """
    from .progress import NULL as _NULL
    progress = progress or _NULL
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
    progress.phase("gamedata", total=1)
    progress.step("locating PAME/PAMU game data")
    try:
        mfa, gnotes = gamedata.load_game_data_from_exe(data, progress=progress)
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
        f"Could not convert '{name}' with the built-in readers. A Fusion "
        "2.5 game's .exe — the file you launch, e.g. "
        "FiveNightsatFreddys.exe — converts on its own; no .mfa is ever "
        "needed. Plain .mfa project files work too. This particular file "
        "needs the optional CTFAK fallback (advanced builds only): pass "
        "--ctfak /path/to/CTFAK.Cli.exe, or set CTFAK_BIN."
    )


def _unknown_input_message(input_path: str) -> str:
    name = os.path.basename(input_path)
    return (
        f"'{name}' is not a file the built-in readers recognise. Point the "
        "tool at the game's .exe (the file you launch to play) — the whole "
        "game is read out of it and no .mfa file is needed. A plain .mfa "
        "project file also works."
    )


def _via_ctfak(path_for_ctfak: str, ctfak_hint: Optional[str],
               notes: list) -> MFA:
    """Last resort: drive the user-provided CTFAK, then load its MFA."""
    tmp = tempfile.mkdtemp(prefix="cts2_")
    mfa_file = exe_to_mfa(path_for_ctfak, ctfak=ctfak_hint, workdir=tmp)
    notes.append(f"CTFAK produced {mfa_file}")
    return load_mfa(mfa_file)


def load_from_bytes(data: bytes, input_name: str,
                    ctfak: Optional[str] = None,
                    progress=None) -> Tuple[MFA, List[str]]:
    """Load any supported input from memory. Returns ``(mfa, notes)``."""
    from .progress import NULL as _NULL
    progress = progress or _NULL
    notes: List[str] = []
    kind = detect.detect_bytes(data)
    progress.phase("detect", total=1)
    progress.step(f"detected: {detect.describe(kind)}")
    if kind == detect.KIND_MFA:
        return load_mfa_bytes(data), notes
    if kind == detect.KIND_EXE:
        progress.phase("pack", total=1)
        progress.step("scanning EXE for Fusion pack / game data")
        mfa = _exe_to_mfa_builtin(input_name, data, notes, progress)
        if mfa is not None:
            return mfa, notes
        notes.append("built-in readers could not convert this EXE")
        if not (ctfak or ctfak_mod.find_ctfak_binary()):
            raise RuntimeError(_ctfak_unavailable_message(input_name))
        tmp = tempfile.mkdtemp(prefix="cts2_up_")
        src = os.path.join(tmp, os.path.basename(input_name) or "game.exe")
        with open(src, "wb") as fh:
            fh.write(data)
        return _via_ctfak(src, ctfak, notes), notes
    if kind == detect.KIND_GAMEDATA:
        progress.phase("gamedata", total=1)
        progress.step("reading raw Fusion 2.5 game data (PAME/PAMU)")
        mfa, gnotes = gamedata.load_game_data_from_exe(data, progress=progress)
        notes.append(
            "read the raw Fusion 2.5 game data (PAME/PAMU) directly — "
            "no .mfa, no CTFAK"
        )
        notes.extend(gnotes)
        return mfa, notes
    # Unknown content. Try the built-in readers anyway (cheap, in case the
    # bytes are exotic), then the optional CTFAK fallback.
    mfa = _exe_to_mfa_builtin(input_name, data, notes, progress)
    if mfa is not None:
        return mfa, notes
    if not (ctfak or ctfak_mod.find_ctfak_binary()):
        raise RuntimeError(_unknown_input_message(input_name))
    tmp = tempfile.mkdtemp(prefix="cts2_up_")
    src = os.path.join(tmp, os.path.basename(input_name) or "input.bin")
    with open(src, "wb") as fh:
        fh.write(data)
    return _via_ctfak(src, ctfak, notes), notes


def load_project(path: str, ctfak: Optional[str] = None,
                 progress=None) -> Tuple[MFA, List[str]]:
    """Load any supported input: a game .exe, an .mfa, a data file — or a
    whole extracted game *folder*, whose game file is found automatically."""
    from .progress import NULL as _NULL
    progress = progress or _NULL
    if os.path.isdir(path):
        candidates = iter_folder_candidates(path)
        if not candidates:
            raise RuntimeError(
                f"no game found in folder '{path}'. Point the tool at the "
                "game's .exe (the file you launch to play) — no .mfa is "
                "needed."
            )
        failures: List[str] = []
        for cand in candidates:
            notes: List[str] = []
            progress.phase("read", total=1)
            progress.step(f"trying '{os.path.basename(cand)}'")
            try:
                with open(cand, "rb") as fh:
                    data = fh.read()
                mfa, notes = load_from_bytes(data, cand, ctfak, progress)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                failures.append(f"{os.path.basename(cand)}: {exc}")
                continue
            notes.insert(0, f"folder input: using {os.path.basename(cand)}")
            notes.extend(f"skipped {f}" for f in failures)
            return mfa, notes
        raise RuntimeError(
            "no convertible game found in folder '{}'. Tried:\n  {}".format(
                path, "\n  ".join(failures or ["(no candidate files)"]))
        )
    progress.phase("read", total=1)
    progress.step(f"reading {os.path.basename(path)}")
    with open(path, "rb") as fh:
        data = fh.read()
    return load_from_bytes(data, path, ctfak, progress)


# Back-compat alias (the old name is used by older call sites).
_load_mfa_from_any = load_project


def _merge_progress_warnings(report: dict, progress) -> None:
    """Fold the *readers'* warnings into the SB3 report.

    The game-data and MFA readers stream their warnings live (unreadable
    frame, broken image, skipped event body…) but they never see this report,
    so a conversion that quietly dropped half a game used to finish with a
    summary claiming "no warnings".  The reporter's list is already
    de-duplicated and capped, so it can be appended as it stands.
    """
    listed = report.setdefault("warnings", [])
    seen = set(listed)
    for w in getattr(progress, "warnings", None) or ():
        if w not in seen:
            seen.add(w)
            listed.append(w)
    suppressed = getattr(progress, "suppressed_warnings", 0)
    if suppressed:
        listed.append(f"{suppressed} further warning(s) were not listed "
                      "(duplicates, or beyond the display cap)")


def convert_file(input_path: str, output_path: Optional[str] = None,
                 report_path: Optional[str] = None,
                 ctfak: Optional[str] = None,
                 progress=None) -> dict:
    """Convert a game .exe / .mfa / data file / folder to an SB3 project.

    Returns {'project': bytes, 'mfa': MFA, 'report': dict}.
    """
    from .progress import NULL as _NULL
    progress = progress or _NULL
    mfa, notes = load_project(input_path, ctfak=ctfak, progress=progress)
    sb3, report = build_project(mfa, progress)
    if notes:
        report.setdefault("notes", []).extend(notes)
    _merge_progress_warnings(report, progress)
    _note_audio_policy(report, progress)
    if output_path:
        progress.step(f"writing {os.path.basename(output_path)}")
        with open(output_path, "wb") as fh:
            fh.write(sb3)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    progress.finish({"sprites": report.get("sprites", 0),
                     "blocks": report.get("blocks", 0),
                     "images": len(mfa.images),
                     "frames": len(mfa.frames),
                     "warnings": len(report.get("warnings", []))})
    return {"project": sb3, "mfa": mfa, "report": report}


def convert_bytes(data: bytes, input_name: str = "project.mfa",
                  ctfak: Optional[str] = None,
                  progress=None) -> dict:
    from .progress import NULL as _NULL
    progress = progress or _NULL
    mfa, notes = load_from_bytes(data, input_name, ctfak, progress)
    sb3, report = build_project(mfa, progress)
    if notes:
        report.setdefault("notes", []).extend(notes)
    _merge_progress_warnings(report, progress)
    _note_audio_policy(report, progress)
    progress.finish({"sprites": report.get("sprites", 0),
                     "blocks": report.get("blocks", 0),
                     "images": len(mfa.images),
                     "frames": len(mfa.frames),
                     "warnings": len(report.get("warnings", []))})
    return {"project": sb3, "mfa": mfa, "report": report}
