"""Compression helpers used by the Clickteam readers (pure stdlib)."""
from __future__ import annotations

import zlib
from typing import Optional

# Default slice size fed to the decompressor per call. Small slices keep the
# transient allocation of a highly compressed (bomb) slice bounded: zlib can
# expand data up to ~1032x, so a 64 KiB slice can never produce more than
# ~66 MiB of output in one call.
_SLICE = 64 * 1024


class DecompressionLimitError(Exception):
    """A zlib stream expanded past the allowed output size (zip bomb?)."""

    def __init__(self, produced: int, max_out: int):
        self.produced = produced
        self.max_out = max_out
        super().__init__(
            f"decompresses beyond the {max_out} byte limit "
            f"({produced} bytes and counting — likely a corrupt or "
            f"hostile stream)"
        )


def zlib_decompress_bounded(data: bytes, max_out: int,
                            wbits: Optional[int] = None) -> bytes:
    """``zlib.decompress`` with a hard cap on the decompressed size.

    A few kilobytes of compressed data can expand to gigabytes (a classic
    decompression bomb).  One-shot ``zlib.decompress`` allocates the whole
    expansion up front, which either gets the process OOM-killed or leaves
    it swapping for many minutes while the progress display freezes on the
    last step — no error, nothing.  Feeding the stream to a decompress
    object in small slices lets us abort the moment the output passes
    ``max_out``.

    Raises :class:`DecompressionLimitError` past the limit and
    ``zlib.error`` on a truncated stream, exactly like the one-shot call.
    Trailing data after the end of the stream is ignored (also like the
    one-shot call).
    """
    d = zlib.decompressobj(wbits) if wbits is not None else zlib.decompressobj()
    out = bytearray()
    n = len(data)
    pos = 0
    while pos < n:
        piece = d.decompress(data[pos : pos + _SLICE])
        out += piece
        pos += _SLICE
        if len(out) > max_out:
            raise DecompressionLimitError(len(out), max_out)
    if not d.eof:
        # Match one-shot zlib.decompress: an incomplete stream is an error,
        # not silently truncated output.
        raise zlib.error(
            -5, "incomplete or truncated stream")
    return bytes(out)


def lz4_block_decompress(data: bytes, expected: int) -> bytes:
    """Decompress a raw LZ4 *block* (no frame header) into ``expected`` bytes.

    Fusion 2.5+ EXEs store image pixels this way. Uses the optional
    ``lz4.block`` accelerator when it is installed and falls back to a
    small pure-Python decoder otherwise, so the converter never needs
    third-party packages.
    """
    try:
        import lz4.block  # type: ignore

        return bytes(lz4.block.decompress(data, uncompressed_size=expected))
    except Exception:
        pass
    return _lz4_block_py(data, expected)


def _lz4_block_py(data: bytes, expected: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 1 > n:
            break
        token = data[i]
        i += 1

        lit_len = token >> 4
        if lit_len == 15:
            while i < n:
                extra = data[i]
                i += 1
                lit_len += extra
                if extra != 255:
                    break
        if i + lit_len > n:
            raise ValueError("LZ4 block: literals run past end of input")
        out.extend(data[i : i + lit_len])
        i += lit_len

        if i == n:
            # Streams are allowed to end after the final literals.
            break
        if i + 2 > n:
            raise ValueError("LZ4 block: truncated match offset")

        offset = data[i] | (data[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise ValueError(f"LZ4 block: invalid match offset {offset}")

        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while i < n:
                extra = data[i]
                i += 1
                match_len += extra
                if extra != 255:
                    break

        start = len(out) - offset
        for k in range(match_len):
            out.append(out[start + k])
        if len(out) >= expected:
            break
    return bytes(out[:expected])
