"""Minimal dependency-free PNG writer (RGBA/RGB) used for SB3 costumes.

If Pillow is installed it is fast-path'd, otherwise we encode a PNG by hand.
"""
from __future__ import annotations

import struct
import zlib

try:  # optional speed-up
    from PIL import Image as _PIL  # type: ignore

    HAVE_PIL = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_PIL = False


def _chunk(tag: bytes, data: bytes) -> bytes:
    c = struct.pack(">I", len(data)) + tag + data
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return c + struct.pack(">I", crc)


def _encode_png_rgba(width: int, height: int, rgba: bytearray) -> bytes:
    if width <= 0 or height <= 0:
        width = max(width, 1)
        height = max(height, 1)
    # add filter byte 0 per scanline
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride : (y + 1) * stride]
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")


def _encode_png_rgb(width: int, height: int, rgb: bytearray) -> bytes:
    if width <= 0 or height <= 0:
        width = max(width, 1)
        height = max(height, 1)
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride : (y + 1) * stride]
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")


def encode_png(width: int, height: int, pixels) -> bytes:
    """Encode a flat pixel list into PNG bytes.

    `pixels` may be a bytearray/bytes/list of ints in RGBA or RGB with length
    width*height*4 or width*height*3.
    """
    if HAVE_PIL:
        try:
            n = len(pixels)
            if n == width * height * 4:
                im = _PIL.new("RGBA", (width, height))
                im.frombytes(bytes(pixels))
            elif n == width * height * 3:
                im = _PIL.new("RGB", (width, height))
                im.frombytes(bytes(pixels))
            else:
                raise ValueError("bad pixel count")
            import io

            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
        except Exception:
            pass
    if len(pixels) == width * height * 4:
        return _encode_png_rgba(width, height, bytearray(pixels))
    if len(pixels) == width * height * 3:
        return _encode_png_rgb(width, height, bytearray(pixels))
    raise ValueError("bad pixel count for png encoder")
