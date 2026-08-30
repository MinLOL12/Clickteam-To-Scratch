"""Compression helpers used by the Clickteam readers (pure stdlib)."""
from __future__ import annotations


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
