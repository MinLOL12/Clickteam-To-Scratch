"""Content-based input detection: what kind of Clickteam file is this?

The converter used to route files purely by extension (``.mfa`` vs ``.exe``
vs ``.dat``...).  That breaks in the real world: a game you bought is a single
EXE, an extracted build may have renamed or extension-less files, and data
blobs are often called ``.dat``/``.bin``.  None of them need an ``.mfa`` —
so this module sniffs the first bytes instead:

* ``MZ``             -> a Windows PE executable (the Fusion 2.5 runtime)
* ``MFU2``/``MFA2``  -> a raw MFA project file (rare: you'd only have one
  if you exported it from the Clickteam editor yourself)
* ``PAME``/``PAMU``  -> a raw Fusion 2.5 game-data region (no PE wrapper —
  e.g. an already-extracted data file)

Anything else is :data:`KIND_UNKNOWN`; the converter then tries its readers
anyway and, as a last resort, the optional CTFAK fallback.
"""
from __future__ import annotations

from typing import Union

KIND_EXE = "exe"
KIND_MFA = "mfa"
KIND_GAMEDATA = "gamedata"
KIND_UNKNOWN = "unknown"

_PE_MAGIC = b"MZ"
_MFA_MAGICS = (b"MFU2", b"MFA2")
_GAMEDATA_MAGICS = (b"PAME", b"PAMU")

_DESCRIPTIONS = {
    KIND_EXE: "a Windows executable (Fusion 2.5 runtime) — game data is read straight out of it",
    KIND_MFA: "an MFA project file",
    KIND_GAMEDATA: "a raw Fusion 2.5 game-data file (PAME/PAMU)",
    KIND_UNKNOWN: "an unrecognized file",
}


def detect_bytes(data: Union[bytes, bytearray]) -> str:
    """Classify a file by its first four bytes."""
    head = bytes(data[:4])
    if head[:2] == _PE_MAGIC:
        return KIND_EXE
    if head in _MFA_MAGICS:
        return KIND_MFA
    if head in _GAMEDATA_MAGICS:
        return KIND_GAMEDATA
    return KIND_UNKNOWN


def detect_file(path: str) -> str:
    """Classify a file on disk by content (not by extension)."""
    with open(path, "rb") as fh:
        return detect_bytes(fh.read(4))


def describe(kind: str) -> str:
    """Human-readable one-liner for a detected kind."""
    return _DESCRIPTIONS.get(kind, _DESCRIPTIONS[KIND_UNKNOWN])
