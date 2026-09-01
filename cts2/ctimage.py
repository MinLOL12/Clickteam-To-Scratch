"""Decode Clickteam Fusion image bitmaps (uncompressed modes used by MFA files).

Every function here works on *whole rows* (or whole images) instead of
walking pixels: a game bank holds thousands of bitmaps, and a per-pixel
Python loop over a 320x240 image already costs tens of milliseconds — enough
to make a conversion look frozen on "decoding images".  Extended-slice
assignment, ``bytes.translate`` and big-integer ``and``/``or`` do the same
work at C speed.
"""
from __future__ import annotations

from .png import encode_png


def _pad(width: int, point_size: int, align: int = 2) -> int:
    """Number of padding *pixels* at the end of a scanline."""
    rem = (width * point_size) % align
    if rem == 0:
        return 0
    pad_bytes = align - rem
    import math

    return int(math.ceil(pad_bytes / point_size))


# --------------------------------------------------------------------------
# buffer helpers (whole-image, no per-pixel loop)
# --------------------------------------------------------------------------

def _gather_rows(data: bytes, height: int, row_bytes: int,
                 stride: int) -> bytes:
    """Concatenate ``height`` scanlines of ``data``, dropping row padding."""
    needed = (height - 1) * stride + row_bytes
    if needed > len(data):
        # Same failure mode as the per-pixel reader it replaces: the data
        # simply runs out mid-image, and callers turn that into a placeholder.
        raise IndexError(
            f"bitmap truncated: need {needed} bytes, have {len(data)}")
    if stride == row_bytes:
        return data[: height * row_bytes]        # no padding: one slice
    return b"".join(data[y * stride: y * stride + row_bytes]
                    for y in range(height))


_SLICE = 1 << 22


def _combine_bytes(a: bytes, b: bytes, op) -> bytes:
    """``op`` (``or`` / ``and``) applied per byte, in C-speed chunks.

    ``int`` arithmetic on a whole buffer is the trick: ``bytes`` -> ``int``
    keeps the byte lanes aligned, so an ``|``/``&`` of two integers is exactly
    a per-byte ``|``/``&`` — with no Python loop over the pixels.
    """
    n = len(a)
    if n != len(b):
        raise ValueError("mismatched buffer sizes")
    if n == 0:
        return b""
    if n <= _SLICE:
        return op(int.from_bytes(a, "little"),
                  int.from_bytes(b, "little")).to_bytes(n, "little")
    pieces = []
    for pos in range(0, n, _SLICE):
        size = min(_SLICE, n - pos)
        pieces.append(op(int.from_bytes(a[pos:pos + size], "little"),
                         int.from_bytes(b[pos:pos + size], "little")
                         ).to_bytes(size, "little"))
    return b"".join(pieces)


def _or_bytes(a: bytes, b: bytes) -> bytes:
    return _combine_bytes(a, b, lambda x, y: x | y)


def _and_bytes(a: bytes, b: bytes) -> bytes:
    return _combine_bytes(a, b, lambda x, y: x & y)


def _table(fn) -> bytes:
    """256-entry ``bytes.translate`` table."""
    return bytes(fn(v) & 0xFF for v in range(256))


def _eq_table(value: int) -> bytes:
    """Translate table: 0xFF where the byte equals ``value``, else 0x00."""
    return bytes(0xFF if v == value else 0x00 for v in range(256))


_INVERT = _table(lambda v: (~v) & 0xFF)

# 5-5-5 (15 bpp) and 5-6-5 (16 bpp) channel expansions, as translate tables
# over the low/high byte of every pixel.
_T_R15 = _table(lambda v: (v << 1) & 0xF8)
_T_G15_LO = _table(lambda v: (v & 0xE0) >> 2)
_T_G15_HI = _table(lambda v: (v & 0x03) << 6)
_T_R16 = _table(lambda v: v & 0xF8)
_T_G16_LO = _table(lambda v: (v & 0xE0) >> 3)
_T_G16_HI = _table(lambda v: (v & 0x07) << 5)
_T_BLUE5 = _table(lambda v: (v & 0x1F) << 3)


# --------------------------------------------------------------------------
# pixel formats
# --------------------------------------------------------------------------

def read_24bpp(data: bytes, width: int, height: int) -> tuple:
    """BGR triplets, padded to 2 bytes."""
    n = width * height
    row_bytes = width * 3
    stride = row_bytes + _pad(width, 3) * 3
    body = _gather_rows(data, height, row_bytes, stride)
    out = bytearray(n * 4)
    out[0::4] = body[2::3]          # R
    out[1::4] = body[1::3]          # G
    out[2::4] = body[0::3]          # B
    out[3::4] = b"\xff" * n         # A
    return bytes(out), stride * height


def read_15bpp(data: bytes, width: int, height: int) -> tuple:
    n = width * height
    row_bytes = width * 2
    stride = row_bytes + _pad(width, 2) * 2
    body = _gather_rows(data, height, row_bytes, stride)
    lo = body[0::2]
    hi = body[1::2]
    out = bytearray(n * 4)
    out[0::4] = hi.translate(_T_R15)
    out[1::4] = _or_bytes(lo.translate(_T_G15_LO), hi.translate(_T_G15_HI))
    out[2::4] = lo.translate(_T_BLUE5)
    out[3::4] = b"\xff" * n
    return bytes(out), stride * height


def read_16bpp(data: bytes, width: int, height: int) -> tuple:
    n = width * height
    row_bytes = width * 2
    stride = row_bytes + _pad(width, 2) * 2
    body = _gather_rows(data, height, row_bytes, stride)
    lo = body[0::2]
    hi = body[1::2]
    out = bytearray(n * 4)
    out[0::4] = hi.translate(_T_R16)
    out[1::4] = _or_bytes(lo.translate(_T_G16_LO), hi.translate(_T_G16_HI))
    out[2::4] = lo.translate(_T_BLUE5)
    out[3::4] = b"\xff" * n
    return bytes(out), stride * height


def read_32bpp(data: bytes, width: int, height: int) -> tuple:
    n = width * height
    row_bytes = width * 4
    stride = row_bytes + _pad(width, 4) * 4
    body = _gather_rows(data, height, row_bytes, stride)
    out = bytearray(body)
    out[0::4] = body[2::4]          # R <- third stored byte
    out[2::4] = body[0::4]          # B <- first stored byte
    # G and the stored alpha byte keep their positions.
    return bytes(out), stride * height


def read_alpha(data: bytes, width: int, height: int, pos: int) -> bytes:
    """The separate 8-bit alpha plane that follows the pixel data."""
    stride = width + _pad(width, 1, 4)
    return _gather_rows(data[pos:] if pos else data, height, width, stride)


def _color_key_match(rgba: bytes, width: int, height: int,
                     transparent: tuple) -> bytes:
    """One byte per pixel: 0xFF where the RGB equals the transparent colour."""
    r = rgba[0::4]
    g = rgba[1::4]
    b = rgba[2::4]
    tr, tg, tb = transparent[0], transparent[1], transparent[2]
    return _and_bytes(
        _and_bytes(r.translate(_eq_table(tr)), g.translate(_eq_table(tg))),
        b.translate(_eq_table(tb)))


def decode_bmp(width: int, height: int, mode: int, flags: int, body: bytes,
               transparent=None):
    """Return PNG bytes for a Clickteam bitmap.

    flags bits: bit0 RLE, bit1 RLEW, bit2 RLET, bit3 LZX, bit4 Alpha,
    bit7 RGBA.  Only the uncompressed modes (4, 6, 7, 8, 16) are decoded;
    compressed images are skipped gracefully.
    """
    if (flags & 0x0F) != 0:
        return None
    mode = mode & 0xFF
    if mode == 4:
        rgba, used = read_24bpp(body, width, height)
    elif mode == 6:
        rgba, used = read_15bpp(body, width, height)
    elif mode == 7:
        rgba, used = read_16bpp(body, width, height)
    elif mode == 8:
        # Fusion 2.5+ EXEs: 32 bits per pixel, 8 bits per channel (BGRA).
        rgba, used = read_32bpp(body, width, height)
        if flags & 0x80:  # RGBA flag: the 4th byte is the alpha channel
            return encode_png(width, height, _ensure_visible(rgba, width, height))
        if not (flags & 0x10):  # no separate alpha plane: use transparent color
            arr = bytearray(rgba)
            if transparent is not None:
                tr, tg, tb, ta = transparent
                mask = _color_key_match(rgba, width, height, transparent)
                arr[3::4] = mask.translate(_table(
                    lambda v, ta=ta: ta if v == 0xFF else 255))
            else:
                arr[3::4] = b"\xff" * (width * height)
            return encode_png(width, height,
                              _ensure_visible(bytes(arr), width, height))
        # Alpha flag without RGBA: a separate alpha plane follows the pixels.
    elif mode == 16:
        # CTFAK treats 2.5+ decoded buffers as mode 16 (= 32-bit BGRA with
        # alpha already interleaved).  Honour an alpha plane if flagged,
        # otherwise keep the 4th byte as alpha.
        rgba, used = read_32bpp(body, width, height)
        if not (flags & 0x10):
            return encode_png(width, height, _ensure_visible(rgba, width, height))
    elif mode == 0:
        # some MFA files store a 32-bit image with mode 0
        rgba, used = read_32bpp(body, width, height)
    else:
        return None

    n = width * height
    if flags & 0x10:  # alpha channel
        if used <= len(body):
            alpha = read_alpha(body, width, height, used)
            arr = bytearray(rgba)
            arr[3::4] = alpha
            rgba = bytes(arr)
    elif transparent is not None:
        # Colour-key the transparent pixel to the configured alpha, leaving
        # every other pixel exactly as the reader produced it.
        tr, tg, tb, ta = transparent
        mask = _color_key_match(rgba, width, height, (tr, tg, tb))
        old = rgba[3::4]
        keyed = _or_bytes(
            _and_bytes(mask, bytes([ta & 0xFF]) * n),
            _and_bytes(mask.translate(_INVERT), old))
        arr = bytearray(rgba)
        arr[3::4] = keyed
        rgba = bytes(arr)

    return encode_png(width, height, _ensure_visible(rgba, width, height))


def _ensure_visible(rgba: bytes, width: int, height: int) -> bytes:
    """If every pixel is fully transparent but colour data is present, force
    opaque alpha.  Some Fusion banks leave alpha at 0 while still storing
    RGB, which made Scratch costumes load as blank invisible sprites.
    """
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return rgba
    n = width * height
    if rgba[3] or rgba[n * 4 - 1]:    # the usual case: already visible
        return rgba
    alpha = rgba[3::4]
    if alpha.count(0) != n:            # something is already visible
        return rgba
    # No opaque pixel at all: only rescue it when there is colour to show.
    for offset in (0, 1, 2):
        if rgba[offset::4].count(0) != n:
            arr = bytearray(rgba)
            arr[3::4] = b"\xff" * n
            return bytes(arr)
    return rgba
