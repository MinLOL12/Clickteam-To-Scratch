"""Minimal dependency-free PNG writer (RGBA/RGB) used for SB3 costumes.

If Pillow is installed it is fast-path'd, otherwise we encode a PNG by hand.
"""
from __future__ import annotations

import io
import struct
import zlib

try:  # optional speed-up
    from PIL import Image as _PIL  # type: ignore

    HAVE_PIL = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_PIL = False

#: zlib level for the fallback encoder.  Level 9 shaves ~1% off a costume and
#: costs ~2x the time; a bank of a few thousand images is where a conversion
#: used to burn minutes, so the default is the balanced level 6.
COMPRESS_LEVEL = 6


def _chunk(tag: bytes, data: bytes) -> bytes:
    c = struct.pack(">I", len(data)) + tag + data
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return c + struct.pack(">I", crc)


def _filter_zero(raw: bytes, width: int, height: int, channels: int) -> bytes:
    """Prefix every scanline with filter type 0 (None).

    Written into a pre-sized buffer with row slices: a per-row
    ``bytearray.append`` + ``+=`` storm costs as much as the image itself on
    big costumes.
    """
    stride = width * channels
    out = bytearray((stride + 1) * height)
    for y in range(height):
        src = y * stride
        dst = y * (stride + 1)
        out[dst] = 0
        out[dst + 1: dst + 1 + stride] = raw[src:src + stride]
    return bytes(out)


def _encode_png_rgba(width: int, height: int, rgba) -> bytes:
    if width <= 0 or height <= 0:
        width = max(width, 1)
        height = max(height, 1)
    raw = _filter_zero(rgba, width, height, 4)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (sig + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, COMPRESS_LEVEL))
            + _chunk(b"IEND", b""))


def _encode_png_rgb(width: int, height: int, rgb) -> bytes:
    if width <= 0 or height <= 0:
        width = max(width, 1)
        height = max(height, 1)
    raw = _filter_zero(rgb, width, height, 3)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (sig + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, COMPRESS_LEVEL))
            + _chunk(b"IEND", b""))


def encode_png(width: int, height: int, pixels) -> bytes:
    """Encode a flat pixel list into PNG bytes.

    `pixels` may be a bytearray/bytes/list of ints in RGBA or RGB with length
    width*height*4 or width*height*3.
    """
    n = len(pixels)
    if not isinstance(pixels, (bytes, bytearray, memoryview)):
        # lists / tuples of ints: make one packed buffer instead of letting
        # every helper below deal with per-item lookups
        pixels = bytes(pixels)
    if HAVE_PIL:
        try:
            if n == width * height * 4:
                im = _PIL.new("RGBA", (width, height))
                im.frombytes(bytes(pixels))
            elif n == width * height * 3:
                im = _PIL.new("RGB", (width, height))
                im.frombytes(bytes(pixels))
            else:
                raise ValueError("bad pixel count")
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
        except Exception:
            pass
    if n == width * height * 4:
        return _encode_png_rgba(width, height, pixels)
    if n == width * height * 3:
        return _encode_png_rgb(width, height, pixels)
    raise ValueError("bad pixel count for png encoder")
