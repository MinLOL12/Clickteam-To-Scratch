"""Native extractor for the Clickteam Fusion 2.5 EXE "pack".

Layout of an F2.5 executable (as documented by the community CTFAK tools)::

    [ PE executable (the Clickteam runtime) ]
    [ pack: header + file manifest + files  ]   <- "PackData"
    [ game data (PAME/PAMU chunk list)      ]   <- read by cts2/gamedata.py

The pack starts at the raw data pointer of a PE section called ``.extra``
(falling back to the end of the last section).  The pack holds a manifest
of named payload files; each payload is either raw data or a zlib stream
marked with the ``0xD9F8`` (i16 ``-9608``) sentinel.

This module recovers those payloads using only the Python standard
library. The game-data region that follows the pack is parsed by
``cts2/gamedata.py`` — together they cover the full EXE -> MFA rebuild
with no external tools (no CTFAK needed).
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

from .compress import DecompressionLimitError, zlib_decompress_bounded

# A pack entry past this decompressed size is a bomb, not a game file.
_PACK_MAX_BYTES = 1024 * 1024 * 1024

BINGO_MARKER = 0xD9F8  # i16 -9608: payload is a zlib stream


class PackError(ValueError):
    """Raised when a file does not look like an F2.5 executable pack."""


@dataclass
class PackFile:
    name: str
    data: bytes
    compressed: bool = False

    @property
    def size(self) -> int:
        return len(self.data)


def _u16(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<H", buf, pos)[0]


def _u32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


def _i32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<i", buf, pos)[0]


def pe_overlay_offset(data: bytes) -> int:
    """Return the file offset where PE sections end (the overlay).

    Clickteam appends PackData + PAME/PAMU game data after the last
    section's raw bytes.  A dedicated ``.extra`` section, when present,
    points straight at that overlay.

    Uses the COFF ``SizeOfOptionalHeader`` field so PE32 *and* PE32+
    (and any other optional-header size) are parsed correctly.  A
    hardcoded 224-byte skip misses real Fusion runtimes whose optional
    header is not exactly that length.
    """
    if len(data) < 64 or data[0:2] != b"MZ":
        raise PackError("not a PE executable (missing MZ header)")
    lfanew = _u32(data, 60)
    if lfanew <= 0 or lfanew + 24 > len(data) or data[lfanew:lfanew + 4] != b"PE\x00\x00":
        raise PackError("no PE header found")
    section_count = _u16(data, lfanew + 6)
    if section_count == 0:
        raise PackError("PE has no sections")
    opt_size = _u16(data, lfanew + 20)
    pos = lfanew + 24 + opt_size
    extra = None
    overlay = 0
    for _i in range(section_count):
        if pos + 40 > len(data):
            raise PackError("PE section table truncated")
        name = data[pos:pos + 8].split(b"\x00", 1)[0].decode("latin-1", "replace")
        raw_size = _u32(data, pos + 16)
        raw_ptr = _u32(data, pos + 20)
        end = raw_ptr + raw_size
        if end > overlay:
            overlay = end
        if name == ".extra":
            extra = raw_ptr
        pos += 40
    position = extra if extra is not None else overlay
    if position <= 0 or position >= len(data):
        raise PackError("pack offset points outside the file")
    return position


def find_pack_start(data: bytes) -> int:
    """Locate the start offset of the pack inside an F2.5 EXE.

    Mirrors CTFAK's ``CalculateEntryPoint``: the ``.extra`` section's
    raw pointer, or the overlay after the last section.
    """
    return pe_overlay_offset(data)


def _decode_name(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def read_pack(data: bytes, start: Optional[int] = None) -> Tuple[str, int, List[PackFile]]:
    """Parse the pack at ``start`` (default: discovered from the PE).

    Layout (mirrors CTFAK's ``PackData.Read``)::

        +0   8-byte header
        +8   u32 headerSize (== 32)
        +12  u32 dataSize    (pack size, includes the 32-byte trailer)
        +16  u32 formatVersion
        +20  i32 check (ignored)
        +24  i32 check (== 0)
        +28  u32 count
        +32  items x count:
                 u16 nameLen
                 name (2*nameLen bytes in PAMU/unicode builds,
                       nameLen bytes in PAME builds)
                 i32 bingo
                 i32 size
                 payload: u16 0xD9F8 + zlib(size) when compressed,
                          otherwise raw `size` bytes
        +dataSize-32  "PAMU" or "PAME" trailer magic (+ 28 reserved bytes)

    Returns ``(magic, format_version, files)``.
    """
    if start is None:
        start = find_pack_start(data)
    if start + 36 > len(data):
        raise PackError("pack header truncated")

    header_size = _u32(data, start + 8)
    data_size = _u32(data, start + 12)
    # only the trailer magic (at start + data_size - 32) must fit
    if header_size != 32 or data_size < 64 or start + data_size - 28 > len(data):
        raise PackError(
            f"implausible pack header (headerSize={header_size}, dataSize={data_size})"
        )

    magic = data[start + data_size - 32:start + data_size - 28]
    if magic not in (b"PAMU", b"PAME"):
        raise PackError(f"no PAMU/PAME magic in pack (found {magic!r})")
    unicode = magic == b"PAMU"

    format_version = _u32(data, start + 16)
    # start+20: reserved check (ignored, varies by build)
    if _i32(data, start + 24) != 0:
        raise PackError("pack check word is not zero; not a Fusion 2.5 pack?")
    count = _u32(data, start + 28)
    if count > 4096:
        raise PackError(f"implausible pack file count ({count})")

    files: List[PackFile] = []
    pos = start + 32
    end = start + data_size - 32  # items must end before the trailer
    for _ in range(count):
        if pos + 2 > end:
            raise PackError("pack manifest truncated")
        name_len = _u16(data, pos)
        pos += 2
        name_bytes = 2 * name_len if unicode else name_len
        if pos + name_bytes + 8 > end:
            raise PackError("pack manifest truncated (entry header)")
        if unicode:
            name = data[pos:pos + name_bytes].decode("utf-16-le", "replace")
        else:
            name = _decode_name(data[pos:pos + name_bytes])
        pos += name_bytes
        # _bingo + size
        size = _i32(data, pos + 4)
        pos += 8
        if size < 0:
            raise PackError(f"bad payload size {size} for {name!r}")
        marker = data[pos:pos + 2]
        if marker == struct.pack("<H", BINGO_MARKER):
            # zlib stream, `size` compressed bytes
            if pos + 2 + size > end:
                raise PackError(f"compressed payload truncated for {name!r}")
            payload = data[pos + 2:pos + 2 + size]
            try:
                data_out = zlib_decompress_bounded(payload, _PACK_MAX_BYTES)
            except DecompressionLimitError as exc:
                raise PackError(
                    f"pack file {name!r} is a decompression bomb "
                    f"({exc.produced} bytes and counting)") from exc
            except zlib.error as exc:
                raise PackError(f"zlib failure in pack file {name!r}: {exc}") from exc
            pos += 2 + size
            files.append(PackFile(name, data_out, compressed=True))
        else:
            if pos + size > end:
                raise PackError(f"payload truncated for {name!r}")
            payload = data[pos:pos + size]
            pos += size
            files.append(PackFile(name, payload, compressed=False))
    return magic.decode("ascii"), format_version, files


def extract_pack(source: Union[str, bytes]) -> Tuple[str, int, List[PackFile]]:
    """Extract the pack from a file path or raw EXE bytes."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        with open(source, "rb") as fh:
            data = fh.read()
    return read_pack(data)


def find_mfa_in_pack(files: List[PackFile]) -> Optional[PackFile]:
    """Return a pack entry that looks like an MFA project, if any."""
    by_magic: Optional[PackFile] = None
    by_name: Optional[PackFile] = None
    for f in files:
        if f.data[:4] in (b"MFU2", b"MFA2"):
            by_magic = f
            break
        if by_name is None and f.name.lower().endswith(".mfa"):
            by_name = f
    return by_magic or by_name


def extract_mfa_from_exe(source: Union[str, bytes]) -> Optional[Tuple[str, bytes]]:
    """Try to recover a raw MFA from an F2.5 EXE without external tools.

    Returns ``(name, mfa_bytes)`` or ``None`` when the pack does not
    contain a usable MFA (the caller should fall back to CTFAK).
    """
    try:
        _magic, _version, files = extract_pack(source)
    except PackError:
        return None
    mfa = find_mfa_in_pack(files)
    if mfa is None:
        return None
    return mfa.name, mfa.data


def dump_pack(source: Union[str, bytes], out_dir: str) -> List[PackFile]:
    """Write every pack file into ``out_dir`` (useful for inspection)."""
    os.makedirs(out_dir, exist_ok=True)
    _magic, _version, files = extract_pack(source)
    for f in files:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f.name)
        with open(os.path.join(out_dir, safe or "entry.bin"), "wb") as fh:
            fh.write(f.data)
    return files


def pack_summary(source: Union[str, bytes]) -> Dict:
    magic, version, files = extract_pack(source)
    mfa = find_mfa_in_pack(files) if files else None
    return {
        "magic": magic,
        "format_version": version,
        "files": [
            {"name": f.name, "size": f.size, "compressed": f.compressed} for f in files
        ],
        "mfa_entry": mfa.name if mfa else None,
    }
