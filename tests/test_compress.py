"""Tests for the bounded zlib decompressor (decompression-bomb guard)."""
from __future__ import annotations

import os
import unittest
import zlib

from cts2.compress import DecompressionLimitError, zlib_decompress_bounded


def _bomb_stream(target_bytes: int, block: int = 1 << 20) -> bytes:
    """One zlib stream that expands to ``target_bytes`` of zeros."""
    co = zlib.compressobj(9)
    parts = []
    sent = 0
    while sent < target_bytes:
        parts.append(co.compress(b"\x00" * block))
        sent += block
    parts.append(co.flush())
    return b"".join(parts)


class TestZlibDecompressBounded(unittest.TestCase):
    def test_roundtrip(self):
        data = zlib.compress(b"hello world" * 1000)
        self.assertEqual(zlib_decompress_bounded(data, 1 << 20),
                         b"hello world" * 1000)

    def test_large_roundtrip_is_still_fast(self):
        raw = os.urandom(4 << 20)
        self.assertEqual(zlib_decompress_bounded(zlib.compress(raw), 1 << 30),
                         raw)

    def test_trailing_data_tolerated_like_one_shot(self):
        data = zlib.compress(b"payload")
        self.assertEqual(zlib_decompress_bounded(data + b"junk", 1024),
                         b"payload")

    def test_truncated_stream_raises_zlib_error(self):
        data = zlib.compress(b"payload" * 100)
        with self.assertRaises(zlib.error):
            zlib_decompress_bounded(data[:-4], 1 << 20)

    def test_over_limit_raises_instead_of_allocating(self):
        # 32 MiB of zeros from a few-KiB stream, with a 1 MiB budget.
        bomb = _bomb_stream(32 << 20)
        self.assertLess(len(bomb), 100 * 1024)
        with self.assertRaises(DecompressionLimitError) as ctx:
            zlib_decompress_bounded(bomb, 1 << 20)
        self.assertIn("limit", str(ctx.exception))

    def test_raw_deflate_wbits(self):
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        stream = co.compress(b"raw deflate payload") + co.flush()
        self.assertEqual(
            zlib_decompress_bounded(stream, 1024, wbits=-15),
            b"raw deflate payload")


if __name__ == "__main__":
    unittest.main()
