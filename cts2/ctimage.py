"""Decode Clickteam Fusion image bitmaps (uncompressed modes used by MFA files)."""
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


def read_24bpp(data: bytes, width: int, height: int) -> tuple:
    """BGR triplets, padded to 2 bytes."""
    out = bytearray(width * height * 4)
    pos = 0
    pad = _pad(width, 3)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            b = data[pos]
            g = data[pos + 1]
            r = data[pos + 2]
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = 255
            pos += 3
        pos += pad * 3
    return bytes(out), pos


def read_15bpp(data: bytes, width: int, height: int) -> tuple:
    out = bytearray(width * height * 4)
    pos = 0
    pad = _pad(width, 2)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            v = data[pos] | (data[pos + 1] << 8)
            r = ((v & 0x7C00) >> 10) << 3
            g = ((v & 0x03E0) >> 5) << 3
            b = (v & 0x001F) << 3
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = 255
            pos += 2
        pos += pad * 2
    return bytes(out), pos


def read_16bpp(data: bytes, width: int, height: int) -> tuple:
    out = bytearray(width * height * 4)
    pos = 0
    pad = _pad(width, 2)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            v = data[pos] | (data[pos + 1] << 8)
            r = ((v & 0xF800) >> 11) << 3
            g = ((v & 0x07E0) >> 5) << 2
            b = (v & 0x001F) << 3
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = 255
            pos += 2
        pos += pad * 2
    return bytes(out), pos


def read_32bpp(data: bytes, width: int, height: int) -> tuple:
    out = bytearray(width * height * 4)
    pos = 0
    pad = _pad(width, 4)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            b = data[pos]
            g = data[pos + 1]
            r = data[pos + 2]
            a = data[pos + 3]
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = a
            pos += 4
        pos += pad * 4
    return bytes(out), pos


def read_alpha(data: bytes, width: int, height: int, pos: int) -> bytes:
    out = bytearray(width * height)
    pad = _pad(width, 1, 4)
    for y in range(height):
        for x in range(width):
            out[y * width + x] = data[pos]
            pos += 1
        pos += pad
    return bytes(out)


def decode_bmp(width: int, height: int, mode: int, flags: int, body: bytes, transparent=None):
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
            return encode_png(width, height, rgba)
        if not (flags & 0x10):  # no separate alpha plane: use transparent color
            arr = bytearray(rgba)
            if transparent is not None:
                tr, tg, tb, ta = transparent
                for i in range(width * height):
                    j = i * 4
                    if arr[j] == tr and arr[j + 1] == tg and arr[j + 2] == tb:
                        arr[j + 3] = ta
                    else:
                        arr[j + 3] = 255
            else:
                for i in range(width * height):
                    arr[i * 4 + 3] = 255
            return encode_png(width, height, bytes(arr))
        # Alpha flag without RGBA: a separate alpha plane follows the pixels.
    elif mode == 16:
        rgba, used = read_32bpp(body, width, height)
    elif mode == 0:
        # some MFA files store a 32-bit image with mode 0
        rgba, used = read_32bpp(body, width, height)
    else:
        return None

    if flags & 0x10:  # alpha channel
        if used <= len(body):
            alpha = read_alpha(body, width, height, used)
            arr = bytearray(rgba)
            for i in range(width * height):
                arr[i * 4 + 3] = alpha[i]
            rgba = bytes(arr)
    elif transparent is not None:
        tr, tg, tb, ta = transparent
        arr = bytearray(rgba)
        for i in range(width * height):
            if arr[i * 4] == tr and arr[i * 4 + 1] == tg and arr[i * 4 + 2] == tb:
                arr[i * 4 + 3] = ta
        rgba = bytes(arr)

    return encode_png(width, height, rgba)
