"""Locate and drive the community CTFAK CLI for the EXE -> MFA step.

CTFAK (https://github.com/CTFAK/CTFAK2.0) is a .NET 6 desktop tool that the
Fusion community maintains; it is the only known way to re-serialize the
game-data region of an F2.5 EXE back into a ``.mfa``.  Its ``CTFAK.Cli``
interface is interactive by default, so we always invoke it with the
documented headless arguments::

    CTFAK.Cli.exe -path <game.exe> -parameters "" -tool "Export as MFA" -closeonfinish

and then look for the freshly written ``.mfa``.  Note that ``CTFAK.Cli``
forces its working directory to its *own* folder, so output is scanned
there (and in its ``Dumps/`` subfolder) as well as in the caller's cwd.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from typing import List, Optional

from . import exe_pack

DEFAULT_TOOL = "Export as MFA"
TIMEOUT = 600


class CtfakNotFoundError(RuntimeError):
    pass


def _home() -> str:
    return os.path.expanduser("~")


def candidate_ctfak_paths(hint: Optional[str] = None) -> List[str]:
    """Ordered list of places a CTFAK CLI might live."""
    paths: List[str] = []
    if hint:
        paths.append(os.path.abspath(os.path.expanduser(hint)))

    env_bin = os.environ.get("CTFAK_BIN")
    if env_bin:
        paths.append(os.path.abspath(os.path.expanduser(env_bin)))

    # Directory chosen through the desktop app / CLI --ctfak-dir style env.
    env_dir = os.environ.get("CTS2_CTFAK_DIR")
    if env_dir:
        for pat in ("CTFAK.Cli.exe", "CTFAK.exe", "*/CTFAK.Cli.exe", "*/CTFAK.exe"):
            paths.extend(glob.glob(os.path.join(os.path.abspath(env_dir), pat)))

    # Bundled with this repository (drop a CTFAK build into app/resources/ctfak).
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    bundled = os.path.join(repo_root, "app", "resources", "ctfak")
    if os.path.isdir(bundled):
        for pat in ("CTFAK.Cli.exe", "CTFAK.exe", "*/CTFAK.Cli.exe", "*/CTFAK.exe"):
            paths.extend(glob.glob(os.path.join(bundled, pat)))

    for name in ("CTFAK.Cli.exe", "CTFAK.exe"):
        found = shutil.which(name)
        if found:
            paths.append(found)

    testpaths = [
        os.path.join("CTFAK", "CTFAK.Cli.exe"),
        "CTFAK.exe",
        os.path.join("ctfak", "CTFAK.Cli.exe"),
        os.path.join(_home(), "CTFAK", "CTFAK.Cli.exe"),
        os.path.join(_home(), "ctfak", "CTFAK.Cli.exe"),
        # CTFAK 2.0 build layout
        os.path.join(_home(), "CTFAK2.0", "Interface", "CTFAK.Cli", "bin", "Debug",
                     "net6.0-windows", "CTFAK.Cli.exe"),
        os.path.join(_home(), "CTFAK2.0", "Interface", "CTFAK.Cli", "bin", "Release",
                     "net6.0-windows", "CTFAK.Cli.exe"),
    ]
    for p in testpaths:
        if os.path.isfile(p):
            paths.append(p)

    # de-duplicate, keep order
    seen = set()
    out = []
    for p in paths:
        ap = os.path.normcase(os.path.abspath(p))
        if ap not in seen and os.path.isfile(p):
            seen.add(ap)
            out.append(p)
    return out


def find_ctfak_binary(hint: Optional[str] = None) -> Optional[str]:
    paths = candidate_ctfak_paths(hint)
    return paths[0] if paths else None


def ctfak_command(ctfak: str, game_path: str, tool: str = DEFAULT_TOOL,
                  parameters: str = "") -> List[str]:
    """Build the headless CTFAK 2.0 command line."""
    name = os.path.basename(ctfak).lower()
    if "ctfak.cli" in name or "ctfak" in name:
        return [
            ctfak,
            "-path", os.path.abspath(game_path),
            "-parameters", parameters,
            "-tool", tool,
            "-closeonfinish",
        ]
    # Unknown binary: best effort (legacy single-argument style).
    return [ctfak, os.path.abspath(game_path)]


def _existing_mfas(dirs: List[str]) -> dict:
    known = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.mfa"), recursive=True):
            try:
                st = os.stat(path)
                known[path] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return known


def scan_new_mfa(scan_dirs: List[str], before: dict) -> Optional[str]:
    best = None
    best_key = (0, 0)
    for d in scan_dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.mfa"), recursive=True):
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime, st.st_size)
            if path in before and before[path] == key:
                continue  # unchanged
            if key > best_key:
                best, best_key = path, key
    return best


def exe_to_mfa(exe_path: str, ctfak: Optional[str] = None,
               tool: str = DEFAULT_TOOL, timeout: int = TIMEOUT,
               workdir: Optional[str] = None) -> str:
    """Run CTFAK to unpack an EXE to an MFA. Returns the path of the MFA.

    Raises :class:`CtfakNotFoundError` when no CTFAK binary can be found
    (the message contains setup guidance) and ``RuntimeError`` when CTFAK
    ran but produced nothing usable.
    """
    ctfak_path = ctfak or find_ctfak_binary()
    if not ctfak_path:
        raise CtfakNotFoundError(
            "No CTFAK binary found. EXE -> MFA rebuilds need the community "
            "'CTFAK' tool (it re-serializes the game data out of the EXE).\n"
            "How to get it:\n"
            "  1. Install the .NET 6 Desktop Runtime (x64): "
            "https://dotnet.microsoft.com/en-us/download/dotnet/6.0\n"
            "  2. Get CTFAK 2.0: https://github.com/CTFAK/CTFAK2.0\n"
            "     (build it, or take a build from the CTFAK Discord), then "
            "download https://github.com/CTFAK/.github/raw/main/ctfakrequirements.zip "
            "and extract it next to CTFAK.Cli.exe (template.mfa must sit beside it)\n"
            "  3. Point this tool at CTFAK.Cli.exe: set CTFAK_BIN=/path/to/CTFAK.Cli.exe\n"
            "     or pass --ctfak PATH on the command line\n"
            "     or drop a CTFAK build into app/resources/ctfak/\n"
            "Plain .mfa conversion never needs CTFAK."
        )

    ctfak_dir = os.path.dirname(os.path.abspath(ctfak_path))
    scan_dirs = [ctfak_dir, os.path.join(ctfak_dir, "Dumps")]
    if workdir:
        scan_dirs.append(workdir)
    cwd = workdir or ctfak_dir
    os.makedirs(cwd, exist_ok=True)
    before = _existing_mfas(scan_dirs)
    started = time.time()

    cmd = ctfak_command(ctfak_path, exe_path, tool=tool)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"CTFAK timed out after {timeout}s. Try again; heavily protected "
            "EXEs can be slow. Partial output:\n"
            + ((exc.stdout or "") + (exc.stderr or ""))[-2000:]
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Could not run CTFAK at {ctfak_path!r}: {exc}. On Windows this "
            "usually means the .NET 6 Desktop Runtime is missing."
        ) from exc

    mfa = scan_new_mfa(scan_dirs, before)
    if not mfa:
        detail = (proc.stdout or "") + (proc.stderr or "")
        hint = ""
        if proc.returncode != 0:
            hint = f" (exit code {proc.returncode})"
        raise RuntimeError(
            "CTFAK produced no .mfa" + hint + ".\nLooked in: "
            + ", ".join(scan_dirs)
            + "\n--- CTFAK output ---\n" + detail[-2500:]
        )
    return mfa


def status(hint: Optional[str] = None) -> dict:
    """Machine-readable CTFAK availability report."""
    found = find_ctfak_binary(hint)
    result = {
        "found": bool(found),
        "path": found,
        "searched": candidate_ctfak_paths(hint) if not found else [],
        "native_pack": "built-in",
        "note": (
            "Plain .mfa files and EXEs whose pack contains a raw MFA are "
            "converted by the built-in extractor; CTFAK is only needed for "
            "the full EXE -> MFA rebuild."
        ),
    }
    return result


__all__ = [
    "CtfakNotFoundError",
    "DEFAULT_TOOL",
    "TIMEOUT",
    "candidate_ctfak_paths",
    "find_ctfak_binary",
    "ctfak_command",
    "exe_to_mfa",
    "status",
    "exe_pack",
]
