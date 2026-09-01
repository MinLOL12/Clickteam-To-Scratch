"""Tests for the bounded zlib decompressor (decompression-bomb guard)."""
from __future__ import annotations

import os
import random
import struct
import time
import unittest
import zlib

from cts2.compress import (DecompressionLimitError, _lz4_block_py,
                           lz4_block_decompress, zlib_decompress_bounded)

try:
    import lz4.block  # noqa: F401
    _HAS_LZ4 = True
except Exception:
    _HAS_LZ4 = False


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


# ---------------------------------------------------------------------------
# LZ4 block decoding (Fusion 2.5+ image banks)
# ---------------------------------------------------------------------------

def lz4_seq(literals: bytes, offset: int = 0, match_len: int = 0) -> bytes:
    """One raw LZ4 sequence: literals, then an optional back-reference."""
    out = bytearray()
    lit_n = min(len(literals), 15)
    ml_n = min(max(match_len - 4, 0), 15) if match_len else 0
    out.append((lit_n << 4) | ml_n)
    out += _lz4_len_bytes(len(literals) - lit_n)
    out += literals
    if match_len:
        out += struct.pack("<H", offset)
        out += _lz4_len_bytes(match_len - 4 - ml_n)
    return bytes(out)


def _lz4_len_bytes(rest: int) -> bytes:
    """Length overflow bytes: as many 0xFF as needed, then the remainder."""
    out = bytearray()
    while rest >= 255:
        out.append(255)
        rest -= 255
    if out or rest:
        out.append(rest)
    return bytes(out)


def lz4_reference(block: bytes, expected: int) -> bytes:
    """A deliberately naive LZ4 block decoder: the oracle for the fast one."""
    out = bytearray()
    i = 0
    n = len(block)
    while i < n:
        token = block[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while i < n:
                add = block[i]
                i += 1
                lit += add
                if add != 255:
                    break
        if i + lit > n:
            raise ValueError("literals past end of input")
        out += block[i:i + lit]
        i += lit
        if len(out) >= expected:
            break
        if i >= n:
            break
        if i + 2 > n:
            raise ValueError("truncated match offset")
        off = block[i] | (block[i + 1] << 8)
        i += 2
        if off == 0 or off > len(out):
            raise ValueError("invalid match offset")
        ml = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while i < n:
                add = block[i]
                i += 1
                ml += add
                if add != 255:
                    break
        start = len(out) - off
        for k in range(ml):
            out.append(out[start + k])
            if len(out) >= expected:
                break
    return bytes(out[:expected])


class TestLz4BlockDecompress(unittest.TestCase):
    def test_literals_only(self):
        raw = os.urandom(500)
        self.assertEqual(lz4_block_decompress(lz4_seq(raw), len(raw)), raw)

    def test_overlapping_run_expands(self):
        # offset 1 with a long match: the "solid colour" sprite area that used
        # to copy one byte at a time.
        block = lz4_seq(b"\xaa", 1, 1000)
        self.assertEqual(lz4_block_decompress(block, 1001), b"\xaa" * 1001)
        self.assertEqual(lz4_reference(block, 1001), b"\xaa" * 1001)

    def test_long_match_is_not_slow(self):
        block = lz4_seq(b"\x11\x22\x33\x44", 4, 300000)
        raw = b"\x11\x22\x33\x44" * 75000
        started = time.perf_counter()
        self.assertEqual(lz4_block_decompress(block, len(raw)), raw)
        self.assertLess(time.perf_counter() - started, 5.0,
                        "a single solid image must not stall the conversion")

    def test_literals_and_match_extensions(self):
        literals = bytes((i * 7) & 0xFF for i in range(700))
        # first sequence carries the back-reference; the last is pure literals
        block = lz4_seq(literals, len(literals), 600) + lz4_seq(b"tail")
        raw = literals + literals[:600] + b"tail"
        self.assertEqual(lz4_block_decompress(block, len(raw)), raw)
        self.assertEqual(lz4_reference(block, len(raw)), raw)

    def test_expected_truncates(self):
        raw = os.urandom(200)
        block = lz4_seq(raw)
        self.assertEqual(lz4_block_decompress(block, 40), raw[:40])

    def test_invalid_offset_raises_strict(self):
        block = lz4_seq(b"abcd", 9999, 8)
        with self.assertRaises(ValueError) as ctx:
            _lz4_block_py(block, 1 << 20)
        self.assertIn("offset", str(ctx.exception))
        # ...and the public entry point refuses it too, whether or not the
        # optional lz4 accelerator is installed (it raises its own error type,
        # which is why the readers around it catch Exception).
        with self.assertRaises(Exception):
            lz4_block_decompress(block, 1 << 20)

    def test_short_output_raises_even_when_lz4_rejects_the_block(self):
        # The optional lz4 package enforces extra stream rules; an incomplete
        # image must still be refused (and reported) rather than accepted as
        # a half-decoded costume.
        block = lz4_seq(b"abcd", 9999, 8)
        with self.assertRaises(ValueError):
            _lz4_block_py(block, 1 << 20)
        self.assertEqual(_lz4_block_py(block, 1 << 20, tolerant=True), b"abcd")

    def test_tolerant_returns_the_partial_prefix(self):
        broken = lz4_seq(b"hello", 9999, 8)
        good = lz4_seq(b"hello")
        self.assertEqual(lz4_block_decompress(broken, 1 << 20, tolerant=True),
                         b"hello")
        # A truncated offset is tolerated the same way.
        self.assertEqual(lz4_block_decompress(good + b"\x01", 1 << 20,
                                              tolerant=True), b"hello")

    def test_fuzz_against_naive_decoder(self):
        # Random garbage: the forgiving decoder must never raise in tolerant
        # mode, must agree with the naive reference whenever both succeed, and
        # must only ever return a prefix of what the strict run produced.
        rng = random.Random(20240917)
        for _ in range(400):
            block = bytes(rng.randrange(256) for _ in range(rng.randrange(60)))
            expected = rng.choice([1, 8, 64, 1 << 20])
            tolerant = lz4_block_decompress(block, expected, tolerant=True)
            self.assertLessEqual(len(tolerant), expected)
            try:
                strict = _lz4_block_py(block, expected)
            except ValueError:
                strict = None
            else:
                self.assertEqual(strict, tolerant)
            try:
                ref = lz4_reference(block, expected)
            except ValueError:
                continue
            self.assertEqual(ref, strict if strict is not None else tolerant)

    @unittest.skipUnless(_HAS_LZ4, "lz4 not installed")
    def test_matches_the_real_lz4_library(self):
        import lz4.block as real

        samples = [b"a", b"aaaa" * 3000, os.urandom(4096), b"\x00" * 100000,
                   bytes(range(256)) * 40, b"Clickteam Fusion" * 5000]
        for raw in samples:
            blob = real.compress(raw, store_size=False)
            self.assertEqual(lz4_block_decompress(blob, len(raw)), raw)
            self.assertEqual(lz4_reference(blob, len(raw)), raw)


if __name__ == "__main__":
    unittest.main()
