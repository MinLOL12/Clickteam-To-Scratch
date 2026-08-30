"""Small little-endian binary reader/writer used by the Clickteam MFA parser."""
from __future__ import annotations

import struct
from typing import BinaryIO, List, Union


class Reader:
    def __init__(self, data: Union[bytes, bytearray, BinaryIO]):
        if isinstance(data, (bytes, bytearray)):
            self._data = bytes(data)
        elif hasattr(data, "read"):
            self._data = data.read()
        else:
            self._data = bytes(data)
        self._fp = 0

    def __len__(self) -> int:
        return len(self._data)

    def tell(self) -> int:
        return self._fp

    def seek(self, pos: int, whence: int = 0) -> int:
        if whence == 1:
            self._fp += pos
        elif whence == 2:
            self._fp = len(self._data) + pos
        else:
            self._fp = pos
        return self._fp

    def skip(self, n: int) -> None:
        self._fp += n

    def remaining(self) -> int:
        return len(self._data) - self._fp

    def at_end(self) -> bool:
        return self._fp >= len(self._data)

    def read(self, n: int) -> bytes:
        if n < 0:
            n = self.remaining()
        out = self._data[self._fp : self._fp + n]
        self._fp += len(out)
        return out

    def _unpack(self, fmt: str, size: int):
        data = self.read(size)
        if len(data) != size:
            raise EOFError(f"wanted {size} bytes, got {len(data)} at {self._fp - len(data)}")
        return struct.unpack(fmt, data)[0]

    def u8(self) -> int:
        return self.read(1)[0]

    def i8(self) -> int:
        return struct.unpack("<b", self.read(1))[0]

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def i16(self) -> int:
        return self._unpack("<h", 2)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def i32(self) -> int:
        return self._unpack("<i", 4)

    def u64(self) -> int:
        return self._unpack("<Q", 8)

    def i64(self) -> int:
        return self._unpack("<q", 8)

    def f32(self) -> float:
        return self._unpack("<f", 4)

    def f64(self) -> float:
        return self._unpack("<d", 8)

    def ascii(self, n: int = -1) -> str:
        raw = self.read(n)
        return raw.split(b"\x00", 1)[0].decode("latin-1", "replace") if n >= 0 else raw.decode("latin-1", "replace")

    def wide(self, n: int = -1) -> str:
        if n < 0:
            chars = []
            while self.remaining() >= 2:
                c = self.u16()
                if c == 0:
                    break
                chars.append(c)
            return "".join(chr(c) for c in chars)
        raw = self.read(n * 2)
        return raw.decode("utf-16-le", "replace")

    def autounicode(self) -> str:
        length = self.i16()
        check = self.i16()
        if check != 0x8000:
            # be tolerant for malformed MFAs, but preserve position
            pass
        return self.wide(max(length, 0))

    def color(self) -> tuple:
        r = self.u8()
        g = self.u8()
        b = self.u8()
        a = self.u8()
        return (r, g, b, a)

    def peek_u8(self) -> int:
        old = self._fp
        v = self.u8()
        self._fp = old
        return v

    def peek_u16(self) -> int:
        old = self._fp
        v = self.u16()
        self._fp = old
        return v

    def peek_u32(self) -> int:
        old = self._fp
        v = self.u32()
        self._fp = old
        return v


class Writer:
    def __init__(self):
        self._buf = bytearray()

    def tell(self) -> int:
        return len(self._buf)

    def getvalue(self) -> bytes:
        return bytes(self._buf)

    def raw(self, data: bytes) -> None:
        self._buf.extend(data)

    def u8(self, v: int) -> None:
        self.raw(struct.pack("<B", v & 0xFF))

    def i8(self, v: int) -> None:
        self.raw(struct.pack("<b", v & 0xFF))

    def u16(self, v: int) -> None:
        self.raw(struct.pack("<H", v & 0xFFFF))

    def i16(self, v: int) -> None:
        self.raw(struct.pack("<h", v & 0xFFFF))

    def u32(self, v: int) -> None:
        self.raw(struct.pack("<I", v & 0xFFFFFFFF))

    def i32(self, v: int) -> None:
        self.raw(struct.pack("<i", v & 0xFFFFFFFF))

    def f32(self, v: float) -> None:
        self.raw(struct.pack("<f", v))

    def ascii(self, s: str) -> None:
        self.raw(s.encode("latin-1", "replace"))

    def wide(self, s: str) -> None:
        self.raw(s.encode("utf-16-le"))

    def autounicode(self, s: str) -> None:
        self.u16(len(s))
        self.u16(0x8000)
        self.wide(s)

    def color(self, rgba: tuple) -> None:
        r, g, b, a = rgba
        self.u8(r)
        self.u8(g)
        self.u8(b)
        self.u8(a)
