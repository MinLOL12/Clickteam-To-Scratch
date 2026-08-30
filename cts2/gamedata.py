"""Native reader for the Clickteam Fusion 2.5 EXE *game data* region.

An F2.5 executable is laid out as::

    [ PE executable (the Clickteam runtime) ]
    [ optional "pack" (PackData)                          ]
    [ game data: PAME/PAMU header + chunk list            ]

The game-data region is the runtime serialization of the project: the
application header, global values/strings, the frame items (objects with
their alterable values, movements and animations), every frame (layers,
object instances), and the image/sound/font banks.  It is the same data
CTFAK re-serializes into a ``.mfa`` -- this module reads it directly with
the Python standard library, so **no external tools are needed** to
convert an F2.5 EXE.

The binary layouts implemented here follow the community-documented
format (CTFAK / Anaconda):

* ``Chunk``: i16 id, i16 flags, i32 size, then ``size`` bytes.
  flags bit0 = zlib compressed (``[decompSize u32][compSize u32][zlib]``),
  bit1 = encrypted (modified RC4 seeded from the game name, copyright and
  editor filename), bit1+bit0 = compressed *and* encrypted.
* Chunk ids: 8739 app header, 8740 app name, 8741 app author, 8747 frame
  handles, 8745/8767 chunked frame items, 8787/8788/8790 flat 2.5+ frame
  items, 8754/8755 global values/strings, 8756 extensions, 13107 frame,
  26214 image bank, 26215 font bank, 26216 sound bank, 32639 end marker.

This is an independent Python implementation; no CTFAK/Anaconda code is
shipped or executed.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import exe_pack
from .bin import Reader
from .compress import lz4_block_decompress
from .ctimage import decode_bmp
from .png import encode_png
from .mfa import (
    Animation,
    AnimationDirection,
    FontItem,
    Frame,
    FrameInstance,
    ImageItem,
    Layer,
    MFA,
    Movement,
    ObjectData,
    SoundItem,
    ValueItem,
)

# --------------------------------------------------------------------------
# chunk ids
# --------------------------------------------------------------------------

CHUNK_APP_HEADER = 8739
CHUNK_APP_NAME = 8740
CHUNK_APP_AUTHOR = 8741
CHUNK_FRAME_ITEMS_OLD1 = 8745
CHUNK_FRAME_HANDLES = 8747
CHUNK_EXT_DATA = 8748
CHUNK_EDITOR_FILENAME = 8750
CHUNK_TARGET_FILENAME = 8751
CHUNK_GLOBAL_VALUES = 8754
CHUNK_GLOBAL_STRINGS = 8755
CHUNK_EXTENSIONS = 8756
CHUNK_APP_ICON = 8757
CHUNK_BINARY_FILES = 8760
CHUNK_COPYRIGHT = 8763
CHUNK_FRAME_ITEMS_OLD2 = 8767
CHUNK_EXE_ONLY = 8768
CHUNK_SHADERS = 8771
CHUNK_EXTENDED_HEADER = 8773
CHUNK_FRAME_ITEMS_25 = 8787
CHUNK_FRAME_ITEM_NAMES_25 = 8788
CHUNK_FRAME_ITEM_SHADERS_25 = 8789
CHUNK_FRAME_ITEM_PROPS_25 = 8790
CHUNK_TRUE_TYPE_FONTS = 8793
CHUNK_FRAME = 13107
CHUNK_IMAGE_BANK = 26214
CHUNK_FONT_BANK = 26215
CHUNK_SOUND_BANK = 26216
CHUNK_LAST = 32639  # 0x7F7F

# frame sub-chunks
FRAME_HEADER = 13108
FRAME_NAME = 13109
FRAME_PALETTE = 13111
FRAME_INSTANCES = 13112
FRAME_EVENTS = 13117
FRAME_LAYERS = 13121

# object info sub-chunks (chunked frame items)
OBJINFO_HEADER = 17476
OBJINFO_NAME = 17477
OBJINFO_PROPS = 17478
OBJINFO_SHADER = 17480

# compression / encryption flags
FLAG_COMPRESSED = 1
FLAG_ENCRYPTED = 2
FLAG_BOTH = 3

MAGIC_CHAR = 54
GAME_HEADERS = (b"PAME", b"PAMU")
CNCV1_VERSION = 0x207  # MMF 1.5-era CNC files

RUNTIME_MMF2 = 0x0302


class GameDataError(ValueError):
    """Raised when the input is not readable F2.5 game data."""


@dataclass
class _Chunk:
    id: int
    flags: int
    size: int
    raw: bytes


@dataclass
class _ObjectProps:
    animations: List[Animation] = field(default_factory=list)
    values: List[int] = field(default_factory=list)
    strings: List[str] = field(default_factory=list)
    movements: List[Movement] = field(default_factory=list)
    counter: Optional[Tuple[int, int, int]] = None  # initial, min, max
    image: Optional[int] = None
    fill_color: Optional[Tuple[int, int, int, int]] = None
    width: int = 0
    height: int = 0


@dataclass
class _ObjectInfo:
    handle: int
    object_type: int
    flags: int = 0
    name: str = ""
    props: Optional[_ObjectProps] = None


# --------------------------------------------------------------------------
# modified-RC4 decryption used by newer Fusion builds
# --------------------------------------------------------------------------

def _rotate(value: int) -> int:
    return ((value << 7) | (value >> 1)) & 0xFF


def _key_string(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code & 0xFF:
            out.append(code & 0xFF)
        if (code >> 8) & 0xFF:
            out.append((code >> 8) & 0xFF)
    return bytes(out)


def _make_key(data1: str, data2: str, data3: str) -> bytes:
    """Build the 256-byte chunk-decryption key from three strings."""
    data = bytearray(_key_string(data1 or "") + _key_string(data2 or "") +
                     _key_string(data3 or ""))
    data_len = len(data)
    data.extend(b"\x00" * (256 - data_len))
    last_key_byte = MAGIC_CHAR
    v34 = MAGIC_CHAR
    for i in range(data_len + 1):
        v34 = _rotate(v34)
        data[i] ^= v34
        last_key_byte = (last_key_byte + data[i] * ((v34 & 1) + 2)) & 0xFF
    data[data_len + 1] = last_key_byte
    return bytes(data)


def _init_decryption_table(magic_key: bytes) -> bytes:
    """Modified RC4 key schedule; returns the 256-byte state table."""
    buf = bytearray(range(256))
    accum = MAGIC_CHAR
    hash_ = MAGIC_CHAR
    never_reset_key = True
    i2 = 0
    key = 0
    for i in range(256):
        hash_ = _rotate(hash_)
        if never_reset_key:
            accum = (accum + (2 if (hash_ & 1) == 0 else 3)) & 0xFF
            accum = (accum * magic_key[key]) & 0xFF
        if hash_ == magic_key[key]:
            hash_ = _rotate(MAGIC_CHAR)
            key = 0
            never_reset_key = False
        i2 = (i2 + ((hash_ ^ magic_key[key]) + buf[i])) & 0xFF
        buf[i2], buf[i] = buf[i], buf[i2]
        key = (key + 1) & 0xFF
    return bytes(buf)


def _transform_chunk(data: bytearray, table: bytes) -> bytearray:
    """Modified RC4 PRGA, applied in place."""
    buf = bytearray(table)
    i = 0
    i2 = 0
    for j in range(len(data)):
        i = (i + 1) & 0xFF
        i2 = (i2 + buf[i]) & 0xFF
        buf[i2], buf[i] = buf[i], buf[i2]
        data[j] ^= buf[(buf[i] + buf[i2]) & 0xFF]
    return data


class _Decryptor:
    """Keeps the decryption table for the current key materials."""

    def __init__(self, build: int):
        self.build = build
        self._table: Optional[bytes] = None
        self._table_key: Optional[tuple] = None

    def _table_for(self, name: str, copyright_: str, editor: str) -> bytes:
        if self.build > 284:
            key = _make_key(name, copyright_, editor)
        else:
            key = _make_key(editor, name, copyright_)
        return _init_decryption_table(key)

    def decode(self, data: bytes, chunk_id: int, name: str, copyright_: str,
               editor: str) -> bytes:
        table = self._table_for(name, copyright_, editor)
        buf = bytearray(data)
        _transform_chunk(buf, table)
        return bytes(buf)

    def decode_mode3(self, data: bytes, chunk_id: int, name: str,
                     copyright_: str, editor: str) -> bytes:
        """Flag 3: [decompSize u32] + encrypted [compSize u32 + zlib]."""
        if len(data) < 4:
            raise ValueError("encrypted chunk too small")
        decompressed_size = struct.unpack_from("<I", data, 0)[0]
        raw = bytearray(data[4:])
        if (chunk_id & 1) == 1 and self.build > 284:
            raw[0] ^= (chunk_id & 0xFF) ^ (chunk_id >> 8)
        _transform_chunk(raw, self._table_for(name, copyright_, editor))
        if len(raw) < 4:
            raise ValueError("encrypted chunk too small")
        comp_size = struct.unpack_from("<I", raw, 0)[0]
        if comp_size < 0 or comp_size > len(raw) - 4:
            raise ValueError("bad compressed size in encrypted chunk")
        return zlib.decompress(bytes(raw[4 : 4 + comp_size]))


def _decode_chunk(chunk: _Chunk, decryptor: _Decryptor, name: str,
                  copyright_: str, editor: str, warnings: List[str]) -> Optional[bytes]:
    """Decrypt/decompress a chunk payload. Returns None when undecodable."""
    try:
        if chunk.flags == 0:
            return chunk.raw
        if chunk.flags == 1:
            if len(chunk.raw) < 8:
                raise ValueError("compressed chunk too small")
            comp_size = struct.unpack_from("<I", chunk.raw, 4)[0]
            if comp_size < 0 or comp_size > len(chunk.raw) - 8:
                raise ValueError("bad compressed size")
            return zlib.decompress(chunk.raw[8 : 8 + comp_size])
        if chunk.flags == 2:
            return decryptor.decode(chunk.raw, chunk.id, name, copyright_, editor)
        if chunk.flags == 3:
            return decryptor.decode_mode3(chunk.raw, chunk.id, name, copyright_, editor)
        warnings.append(
            f"chunk {chunk.id} has unknown flags {chunk.flags}; reading raw"
        )
        return chunk.raw
    except Exception as exc:  # noqa: BLE001 - best effort per chunk
        warnings.append(f"could not decode chunk {chunk.id}: {exc}")
        return None


# --------------------------------------------------------------------------
# low-level readers
# --------------------------------------------------------------------------

# Shared by the old-style chunked object-info reader; set from the game
# header ("PAMU" -> unicode strings) before any object chunk is parsed.
_GLOBAL_UNICODE = True


def _cstring(r: Reader, unicode: bool) -> str:
    """Null-terminated string; wide when the game data is PAMU."""
    if unicode:
        chars = []
        while r.remaining() >= 2:
            c = r.u16()
            if c == 0:
                break
            chars.append(c)
        return "".join(chr(c) for c in chars)
    out = bytearray()
    while r.remaining() >= 1:
        b = r.u8()
        if b == 0:
            break
        out.append(b)
    return out.decode("latin-1", "replace")


def _wstring(r: Reader) -> str:
    """Null-terminated wide string (always UTF-16, even in PAME games)."""
    chars = []
    while r.remaining() >= 2:
        c = r.u16()
        if c == 0:
            break
        chars.append(c)
    return "".join(chr(c) for c in chars)


def _read_chunk_list(r: Reader) -> List[_Chunk]:
    """Read (id, flags, size, raw) entries until the Last marker / EOF."""
    chunks: List[_Chunk] = []
    while r.remaining() >= 8:
        cid = r.i16()
        flags = r.i16()
        size = r.i32()
        if cid == CHUNK_LAST:
            break
        if size < 0 or size > r.remaining():
            raise GameDataError(f"chunk {cid} has implausible size {size}")
        chunks.append(_Chunk(cid, flags, size, r.read(size)))
    return chunks


def _read_animations(r: Reader) -> List[Animation]:
    start = r.tell()
    r.i16()  # header size
    count = r.i16()
    if count <= 0 or count > 4096:
        return []
    offsets = [r.i16() for _ in range(count)]
    animations: List[Animation] = []
    for i, off in enumerate(offsets):
        anim = Animation(name=str(i))
        if off <= 0:
            animations.append(anim)
            continue
        try:
            r.seek(start + off)
            dir_offsets = [r.i16() for _ in range(32)]
            for j, doff in enumerate(dir_offsets):
                if doff <= 0:
                    continue
                r.seek(start + off + doff)
                if r.remaining() < 6:
                    continue
                min_speed = r.i8()
                max_speed = r.i8()
                repeat = r.i16()
                back_to = r.i16()
                frame_count = r.u16()
                frames = [r.i16() for _ in range(min(frame_count, 65536))]
                anim.directions.append(
                    AnimationDirection(j, min_speed, max_speed, repeat, back_to,
                                        [f for f in frames if f != -1])
                )
        except Exception:  # noqa: BLE001
            continue
        animations.append(anim)
    return animations


def _read_alterable_values(r: Reader) -> List[int]:
    count = r.i16()
    if count <= 0 or count > 4096:
        return []
    return [r.i32() for _ in range(count)]


def _read_alterable_strings(r: Reader) -> List[str]:
    count = r.i16()
    if count <= 0 or count > 4096:
        return []
    return [_wstring(r) for _ in range(count)]


def _read_movements(r: Reader, unicode: bool) -> List[Movement]:
    root = r.tell()
    count = r.u32()
    if count > 4096:
        return []
    out = []
    for i in range(count):
        r.seek(root + 4 + 16 * i)
        if r.remaining() < 16:
            break
        r.i32()  # name offset
        movement_id = r.i32()
        new_offset = r.i32()
        r.i32()  # data size
        player = 0
        typ = 0
        moving = 0
        direction = 0
        try:
            r.seek(root + new_offset)
            player = r.u16()
            typ = r.u16()
            moving = r.u8()
            r.skip(3)
            direction = r.i32()
        except Exception:  # noqa: BLE001
            pass
        out.append(Movement("", "", movement_id, player, typ, moving, direction))
    return out


def _read_counter(r: Reader) -> Optional[Tuple[int, int, int]]:
    if r.remaining() < 14:
        return None
    r.i16()  # size
    initial = r.i32()
    minimum = r.i32()
    maximum = r.i32()
    return initial, minimum, maximum


def _read_object_common(r: Reader, two_five_plus: bool,
                        build: int) -> _ObjectProps:
    """Read an ObjectCommon block (F2.5 EXE object properties)."""
    props = _ObjectProps()
    start = r.tell()
    anim_off = 0
    mov_off = 0
    values_off = 0
    strings_off = 0
    counter_off = 0
    identifier = ""
    if two_five_plus:
        r.i32()  # size
        anim_off = r.i16()
        mov_off = r.i16()
        r.u16()  # version
        r.skip(2)
        r.i16()  # extension offset
        counter_off = r.i16()
    elif build >= 284:
        # MMF2 build 284+ (FNaF 1's layout): size, 2 skipped bytes, then a
        # peeked "check" word that distinguishes two field orders.  Both
        # orders continue *right after the size field* — the check is a
        # peek, so seek back before reading the offsets (CTFAK seeks to
        # currentPosition + 4 here; skipping that leaves every offset
        # shifted by 2 bytes and the animations unreadable).
        r.i32()  # size
        r.skip(2)
        check = r.i32()
        r.seek(start + 4)
        if build == 284 and check == 0:
            counter_off = r.i16()
            r.i32()  # version
            mov_off = r.i16()
            r.i16()  # extension offset
            anim_off = r.i16()
        else:
            anim_off = r.i16()
            mov_off = r.i16()
            r.i32()  # version
            r.i16()  # extension offset
            counter_off = r.i16()
    else:
        r.i32()  # size
        mov_off = r.i16()
        anim_off = r.i16()
        r.i16()  # version
        counter_off = r.i16()
        r.i16()  # system object offset
        r.skip(2)
    r.u16()  # flags
    r.i16()  # "do not create at start" marker
    for _ in range(8):
        r.i16()  # qualifiers
    if not (two_five_plus or build >= 284):
        r.i16()  # extension offset
    else:
        r.i16()  # system object offset
    values_off = r.i16()
    strings_off = r.i16()
    r.u16()  # new flags
    r.u16()  # preferences
    identifier = r.ascii(4) if two_five_plus or build >= 284 else r.ascii(2)
    r.color()  # background color
    r.u32()  # fade in offset
    r.u32()  # fade out offset

    if anim_off > 0:
        try:
            r.seek(start + anim_off)
            props.animations = _read_animations(r)
        except Exception:  # noqa: BLE001
            pass
    if values_off > 0:
        try:
            r.seek(start + values_off)
            props.values = _read_alterable_values(r)
        except Exception:  # noqa: BLE001
            pass
    if strings_off > 0:
        try:
            r.seek(start + strings_off)
            props.strings = _read_alterable_strings(r)
        except Exception:  # noqa: BLE001
            pass
    if mov_off > 0:
        try:
            r.seek(start + mov_off)
            props.movements = _read_movements(r, unicode=False)
        except Exception:  # noqa: BLE001
            pass
    if counter_off > 0 and identifier in (
        "XT", "CNTR", "SCORE", "LIVE", "CN", "LIVES",
    ):
        try:
            r.seek(start + counter_off)
            props.counter = _read_counter(r)
        except Exception:  # noqa: BLE001
            pass
    return props


def _read_quickbackdrop(r: Reader) -> _ObjectProps:
    props = _ObjectProps()
    try:
        r.i32()  # size
        r.i16()  # obstacle type
        r.i16()  # collision type
        props.width = r.i32()
        props.height = r.i32()
        r.i16()  # border size
        r.color()  # border color
        shape_type = r.i16()
        fill_type = r.i16()
        if shape_type == 1:
            r.i16()
        elif fill_type == 1:
            props.fill_color = r.color()
        elif fill_type == 2:
            props.fill_color = r.color()
            r.color()
            r.i16()
        props.image = r.i16()
    except Exception:  # noqa: BLE001
        pass
    return props


def _read_backdrop(r: Reader) -> _ObjectProps:
    props = _ObjectProps()
    try:
        r.i32()  # size
        r.i16()  # obstacle type
        r.i16()  # collision type
        props.width = r.i32()
        props.height = r.i32()
        props.image = r.i16()
    except Exception:  # noqa: BLE001
        pass
    return props


def _read_object_props(data: bytes, object_type: int, two_five_plus: bool,
                       build: int) -> _ObjectProps:
    r = Reader(data)
    try:
        if object_type == 0:
            return _read_quickbackdrop(r)
        if object_type == 1:
            return _read_backdrop(r)
        return _read_object_common(r, two_five_plus, build)
    except Exception:  # noqa: BLE001
        return _ObjectProps()


def _read_chunked_object_info(data: bytes, two_five_plus: bool,
                              build: int) -> _ObjectInfo:
    """Old-style (chunks 8745/8767) frame item: a nested chunk list."""
    global _GLOBAL_UNICODE
    r = Reader(data)
    info = _ObjectInfo(handle=-1, object_type=2)
    try:
        for chunk in _read_chunk_list(r):
            if chunk.id == OBJINFO_HEADER:
                hr = Reader(chunk.raw)
                if hr.remaining() < 8:
                    continue
                info.handle = hr.i16()
                info.object_type = hr.i16()
                info.flags = hr.i16()
                hr.skip(2)
                ink = hr.u8()
                if ink != 1:
                    hr.skip(3 + 4)
                else:
                    hr.skip(1 + 2 + 1 + 3)
            elif chunk.id == OBJINFO_NAME:
                info.name = _cstring(Reader(chunk.raw), _GLOBAL_UNICODE)
            elif chunk.id == OBJINFO_PROPS:
                info.props = _read_object_props(
                    chunk.raw, info.object_type, two_five_plus, build)
    except Exception:  # noqa: BLE001
        pass
    return info


# --------------------------------------------------------------------------
# game data
# --------------------------------------------------------------------------

class _GameReader:
    def __init__(self):
        self.unicode = False
        self.build = 0
        self.runtime_version = 0
        self.product_version = 0
        self.two_five_plus = False
        self.name = ""
        self.author = ""
        self.copyright = ""
        self.editor_filename = ""
        self.target_filename = ""
        self.window_x = 0
        self.window_y = 0
        self.frame_rate = 50
        self.border_color = (0, 0, 0, 0)
        self.initial_score = 0
        self.initial_lives = 0
        self.images: Dict[int, ImageItem] = {}
        self.sounds: List[SoundItem] = []
        self.fonts: List[FontItem] = []
        self.global_values: List[ValueItem] = []
        self.global_strings: List[ValueItem] = []
        self.extensions: List[dict] = []
        self.objects: Dict[int, ObjectData] = {}
        self.frame_handles: List[int] = []
        self.frames: List[Frame] = []
        self.event_sizes: List[int] = []
        self.warnings: List[str] = []
        self.decryptor: Optional[_Decryptor] = None
        self.missing_object_notes: List[str] = []
        self.progress = None  # cts2.progress.Reporter (optional)

    def warning(self, msg: str) -> None:
        """Record a parser warning and stream it into the progress report."""
        self.warnings.append(msg)
        if self.progress is not None:
            self.progress.warn(msg)

    # -- chunk interpreters -------------------------------------------------

    def _decoded(self, chunk: _Chunk, decryptor: _Decryptor) -> Optional[bytes]:
        return _decode_chunk(chunk, decryptor, self.name, self.copyright,
                             self.editor_filename, self.warnings)

    def _decoded_chunks(self, r: Reader) -> List[_Chunk]:
        """Walk a *nested* chunk list, decoding every payload.

        The chunk *headers* (id/flags/size) are always stored plain, but the
        payloads of the sub-chunks inside frames (name, instances, layers,
        events, ...) and inside old-style object infos are usually zlib
        compressed and/or RC4 encrypted by the game.  The reference readers
        (CTFAK / Anaconda) run every chunk through the same flag handling
        recursively; skipping it yields the raw stream, which parses as
        garbage instances and "missing object" warnings (the classic MMF2
        symptom).  Undecodable payloads fall back to the raw bytes.
        """
        out: List[_Chunk] = []
        while r.remaining() >= 8:
            cid = r.i16()
            flags = r.i16()
            size = r.i32()
            if cid == CHUNK_LAST:
                break
            if size < 0 or size > r.remaining():
                break
            raw = r.read(size)
            chunk = _Chunk(cid, flags, size, raw)
            if flags == 0:
                out.append(chunk)
                continue
            data = None
            if self.decryptor is not None:
                data = self._decoded(chunk, self.decryptor)
            if data is None:
                data = raw  # best effort: try to interpret undecoded bytes
            out.append(_Chunk(cid, 0, len(data), data))
        return out

    def _note_missing_object(self, frame_name: str, inst_handle: int,
                             object_info: int) -> None:
        self.missing_object_notes.append(
            f"instance {inst_handle} in {frame_name or '(unnamed frame)'} "
            f"references missing object {object_info}"
        )

    def _fold_missing_object_notes(self) -> None:
        """Cap the per-instance noise: keep the first few, then summarize."""
        notes = self.missing_object_notes
        if not notes:
            return
        cap = 8
        self.warnings.extend(notes[:cap])
        if len(notes) > cap:
            self.warning(
                f"... and {len(notes) - cap} more instance(s) reference "
                "missing objects (frame items were not readable)"
            )

    def _read_app_header(self, data: bytes) -> None:
        r = Reader(data)
        try:
            r.i32()  # size
            r.u16()  # flags
            r.u16()  # new flags
            r.i16()  # graphics mode
            r.u16()  # other flags
            self.window_x = r.u16()
            self.window_y = r.u16()
            self.initial_score = r.u32() ^ 0xFFFFFFFF
            self.initial_lives = r.u32() ^ 0xFFFFFFFF
            r.skip(4 * 18)  # 4 player controls
            self.border_color = r.color()
            r.i32()  # number of frames (also available from FrameHandles)
            self.frame_rate = r.i32()
        except Exception as exc:  # noqa: BLE001
            self.warning(f"app header unreadable: {exc}")

    def _read_app_name(self, data: bytes) -> None:
        r = Reader(data)
        # Older builds store a plain ASCII name; newer ones use the
        # universal (unicode-aware) string.
        try:
            ascii_name = ""
            pos = r.tell()
            while r.remaining() >= 1:
                b = r.u8()
                if b == 0:
                    break
                ascii_name += chr(b)
            if pos + len(ascii_name) + 1 >= len(data):
                self.name = ascii_name
                return
            r.seek(0)
            self.name = _cstring(r, self.unicode)
        except Exception:  # noqa: BLE001
            pass

    def _read_globals(self, data: bytes, strings: bool) -> None:
        r = Reader(data)
        try:
            if strings:
                count = r.i32()
                if count < 0 or count > 65536:
                    return
                for _ in range(count):
                    self.global_strings.append(
                        ValueItem("", _wstring(r), 2))
            else:
                count = r.i16()
                if count < 0 or count > 65536:
                    return
                raw_values = [r.read(4) for _ in range(count)]
                for i, raw in enumerate(raw_values):
                    typ = r.u8() if r.remaining() >= 1 else 0
                    if typ == 2 and len(raw) == 4:
                        value: object = struct.unpack("<f", raw)[0]
                    elif len(raw) == 4:
                        value = struct.unpack("<i", raw)[0]
                    else:
                        value = 0
                    self.global_values.append(ValueItem("", value, typ))
        except Exception as exc:  # noqa: BLE001
            self.warning(f"global values unreadable: {exc}")

    def _read_extensions(self, data: bytes) -> None:
        r = Reader(data)
        try:
            count = r.u16()
            r.u16()  # preload count
            for _ in range(min(count, 4096)):
                start = r.tell()
                size = r.i16()
                size = -size if size < 0 else size
                handle = r.i16()
                magic = r.i32()
                version_ls = r.i32()
                version_ms = r.i32()
                name = _cstring(r, self.unicode)
                sub_type = _cstring(r, self.unicode) if name else ""
                self.extensions.append({
                    "handle": handle,
                    "name": name,
                    "sub_type": sub_type,
                    "magic": magic,
                    "version": [version_ls, version_ms],
                })
                r.seek(start + size)
        except Exception:  # noqa: BLE001
            pass

    def _read_frame_handles(self, data: bytes) -> None:
        self.frame_handles = [
            struct.unpack_from("<h", data, i)[0]
            for i in range(0, len(data) - 1, 2)
        ]

    # -- frame items --------------------------------------------------------

    def _apply_object_props(self, obj: ObjectData, props: _ObjectProps) -> None:
        obj.values = [ValueItem(f"Alterable Value {i}", v, 0)
                      for i, v in enumerate(props.values)]
        obj.strings = [ValueItem(f"Alterable String {i}", s, 2)
                       for i, s in enumerate(props.strings)]
        obj.movements = props.movements
        obj.animations = props.animations
        obj.width = props.width
        obj.height = props.height
        if props.counter is not None:
            obj.extra["value"], obj.extra["minimum"], obj.extra["maximum"] = \
                props.counter
        first_image = None
        for anim in props.animations:
            for d in anim.directions:
                for h in d.frames:
                    if first_image is None:
                        first_image = h
        if first_image is not None:
            obj.image_handle = first_image
        elif props.image is not None and props.image != -1:
            obj.image_handle = props.image
        elif props.fill_color is not None:
            # Quick backdrop with a solid fill: synthesize a tiny PNG so the
            # object still appears in Scratch.
            rgba = bytes(props.fill_color) * 4  # 2x2 px
            png = encode_png(2, 2, rgba)
            if png:
                synth = -1000000 - obj.handle
                self.images[synth] = ImageItem(
                    synth, 0, 0, len(png), 2, 2, 4, 0, 1, 1, 1, 1,
                    props.fill_color, png, png)
                obj.image_handle = synth

    def _finalize_object(self, info: _ObjectInfo) -> None:
        if info.handle < 0:
            return
        obj = ObjectData(
            object_type=info.object_type if info.object_type >= 0 else 2,
            handle=info.handle,
            name=info.name or f"Object {info.handle}",
            flags=info.flags,
        )
        if info.props is not None:
            self._apply_object_props(obj, info.props)
        self.objects[obj.handle] = obj

    def _read_frame_items_chunked(self, data: bytes) -> None:
        """Old-style frame items (chunk ids 8745 / 8767)."""
        r = Reader(data)
        try:
            count = r.i32()
            if count < 0 or count > 65536:
                self.warning(
                    f"frame items chunk has implausible count {count}; "
                    "objects skipped"
                )
                return
            for _ in range(count):
                info = _read_chunked_object_info(
                    self._read_object_block(r), self.two_five_plus, self.build)
                if info.handle < 0:
                    self.warning(
                        "a frame item could not be read (no object header "
                        "chunk found); it is skipped"
                    )
                    continue
                self._finalize_object(info)
        except Exception as exc:  # noqa: BLE001
            self.warning(f"frame items unreadable: {exc}")

    def _read_object_block(self, r: Reader) -> bytes:
        """Read one old-style chunked ObjectInfo from ``r``.

        The inner chunks (object header 17476, name 17477, properties
        17478) may be zlib compressed and/or RC4 encrypted like any other
        chunk, so every payload goes through the normal flag handling and
        is re-serialized plain for :func:`_read_chunked_object_info`.
        """
        out = bytearray()
        while r.remaining() >= 8:
            cid = r.i16()
            flags = r.i16()
            size = r.i32()
            if cid == CHUNK_LAST:
                break
            if size < 0 or size > r.remaining():
                break
            chunk = _Chunk(cid, flags, size, r.read(size))
            payload = chunk.raw
            if flags != 0:
                decoded = None
                if self.decryptor is not None:
                    decoded = self._decoded(chunk, self.decryptor)
                if decoded is not None:
                    payload = decoded
            out += struct.pack("<hhi", cid, 0, len(payload))
            out += payload
        return bytes(out)

    def _read_frame_items_25(self, header: bytes, names: Optional[bytes],
                             props_data: Optional[bytes]) -> None:
        """2.5+ flat frame items (chunk ids 8787/8788/8790)."""
        infos: List[_ObjectInfo] = []
        hr = Reader(header)
        while hr.remaining() >= 8:
            try:
                handle = hr.i16()
                object_type = hr.i16()
                flags = hr.i16()
                hr.skip(2)
                ink = hr.u8()
                if ink != 1:
                    hr.skip(3 + 4)
                else:
                    hr.skip(1 + 2 + 1 + 3)
                infos.append(_ObjectInfo(handle, object_type, flags))
            except Exception:  # noqa: BLE001
                break
        if names is not None:
            nr = Reader(names)
            for i, info in enumerate(infos):
                try:
                    info.name = _cstring(nr, self.unicode)
                except Exception:  # noqa: BLE001
                    break
        if props_data is not None:
            pr = Reader(props_data)
            try:
                pr.i32()  # count / header
                for i, info in enumerate(infos):
                    current = pr.tell()
                    if pr.remaining() < 8:
                        break
                    chunk_size = pr.i32()
                    if chunk_size < 0 or chunk_size > pr.remaining() - 4:
                        break
                    payload = pr.read(chunk_size)
                    pr.skip(4)  # trailing marker per record
                    try:
                        decoded = zlib.decompress(payload)
                    except zlib.error:
                        decoded = payload
                    info.props = _read_object_props(
                        decoded, info.object_type, True, self.build)
                    pr.seek(current + chunk_size + 8)
            except Exception as exc:  # noqa: BLE001
                self.warning(f"2.5+ object properties: {exc}")
        for info in infos:
            self._finalize_object(info)

    # -- banks --------------------------------------------------------------

    def _read_image_bank(self, data: bytes) -> None:
        r = Reader(data)
        try:
            count = r.i32()
            if count < 0 or count > 65536:
                return
            if self.progress is not None:
                self.progress.phase("images", total=count)
            for i in range(count):
                start = r.tell()
                if self.progress is not None:
                    self.progress.tick(i + 1, step=f"decoding image {i + 1}/{count} → PNG")
                try:
                    if self.two_five_plus:
                        self._read_image_25(r)
                    else:
                        self._read_image_normal(r)
                except GameDataError as exc:
                    # A few 2.5+ games still carry classic-format images;
                    # fall back instead of giving up on the whole bank.
                    if self.two_five_plus:
                        try:
                            r.seek(start)
                            self._read_image_normal(r)
                            continue
                        except GameDataError:
                            pass
                    self.warning(f"image at {start} unreadable: {exc}")
                if r.remaining() <= 0:
                    break
        except Exception as exc:  # noqa: BLE001
            self.warning(f"image bank unreadable: {exc}")

    def _image_to_item(self, handle: int, checksum: int, references: int,
                       size: int, width: int, height: int, gmode: int,
                       flags: int, hx: int, hy: int, ax: int, ay: int,
                       transparent: tuple, body: bytes,
                       force_lz4: bool = False) -> None:
        png = None
        try:
            if force_lz4:
                pixels = lz4_block_decompress(body, size)
                png = decode_bmp(width, height, gmode, flags & ~0x08, pixels,
                                 transparent)
            else:
                png = decode_bmp(width, height, gmode, flags & ~0x08, body,
                                 transparent)
        except Exception:  # noqa: BLE001
            png = None
        self.images[handle] = ImageItem(
            handle, checksum, references, size, width, height, gmode, flags,
            hx, hy, ax, ay, transparent, png, body)

    def _read_image_normal(self, r: Reader) -> None:
        """F2.5 EXE image: outer zlib block around the classic item header."""
        try:
            handle = r.i32()
        except EOFError as exc:
            raise GameDataError(f"truncated image header: {exc}") from exc
        if self.build >= 284:
            handle -= 1
        decomp_size = r.i32()
        comp_size = r.i32()
        if comp_size < 0 or comp_size > r.remaining():
            raise GameDataError("bad image payload size")
        try:
            inner = zlib.decompress(r.read(comp_size))
        except zlib.error as exc:
            raise GameDataError(f"image {handle} payload: {exc}") from exc
        ir = Reader(inner)
        try:
            checksum = ir.i32()
            references = ir.i32()
            size = ir.i32()
            width = ir.i16()
            height = ir.i16()
            gmode = ir.u8()
            flags = ir.u8()
            ir.skip(2)
            hx = ir.i16()
            hy = ir.i16()
            ax = ir.i16()
            ay = ir.i16()
            transparent = ir.color()
        except EOFError as exc:
            raise GameDataError(
                f"image {handle} inner header truncated: {exc}") from exc
        body = b""
        if flags & 0x08:  # LZX: one zlib stream for the rest of the item
            ir.i32()  # decompressed size
            try:
                body = zlib.decompress(ir.read(-1))
            except zlib.error:
                body = b""
        else:
            body = ir.read(max(size, 0))
        self._image_to_item(handle, checksum, references, size, width, height,
                            gmode, flags, hx, hy, ax, ay, transparent, body)

    def _read_image_25(self, r: Reader) -> None:
        """2.5+ image: LZ4 block payload, no outer compression."""
        try:
            handle = r.i32() - 1
            checksum = r.i32()
            references = r.i32()
            r.i32()  # unknown
            data_size = r.i32()
            width = r.i16()
            height = r.i16()
            gmode = r.u8()
            flags = r.u8()
            r.skip(2)
            hx = r.i16()
            hy = r.i16()
            ax = r.i16()
            ay = r.i16()
            transparent = r.color()
            decomp_size = r.i32()
        except EOFError as exc:
            raise GameDataError(f"truncated 2.5+ image header: {exc}") from exc
        if data_size < 4 or decomp_size < 0:
            raise GameDataError(
                f"implausible 2.5+ image sizes (data={data_size}, "
                f"decompressed={decomp_size})"
            )
        raw = r.read(max(data_size - 4, 0))
        try:
            pixels = lz4_block_decompress(raw, decomp_size)
        except Exception as exc:  # noqa: BLE001
            raise GameDataError(f"image {handle} LZ4: {exc}") from exc
        try:
            png = decode_bmp(width, height, gmode, flags & ~0x08, pixels,
                             transparent)
        except Exception as exc:  # noqa: BLE001
            raise GameDataError(f"image {handle} pixels: {exc}") from exc
        self.images[handle] = ImageItem(
            handle, checksum, references, decomp_size, width, height, gmode,
            flags, hx, hy, ax, ay, transparent, png, raw)

    def _read_sound_bank(self, data: bytes) -> None:
        if self.progress is not None:
            self.progress.phase("sounds", total=1)
            self.progress.step("extracting sound bank")
        r = Reader(data)
        try:
            count = r.i32()
            if count < 0 or count > 65536:
                return
            for _ in range(count):
                handle = r.u32() - 1
                checksum = r.i32()
                references = r.u32()
                decomp_size = r.i32()
                flags = r.u8()
                r.skip(3)
                r.i32()  # reserved
                name_len = r.i32()
                if flags != 33:
                    comp_size = r.i32()
                    if comp_size < 0 or comp_size > r.remaining():
                        break
                    try:
                        payload = zlib.decompress(r.read(comp_size))
                    except zlib.error:
                        payload = b""
                else:
                    payload = r.read(max(decomp_size, 0))
                name = ""
                if len(payload) >= max(name_len, 0) * 2:
                    name = payload[: name_len * 2].decode(
                        "utf-16-le", "replace").strip("\x00")
                audio = payload[name_len * 2:] if flags != 33 else payload
                if not audio:
                    audio = payload
                self.sounds.append(SoundItem(
                    handle, checksum, references, flags, name, audio,
                    decomp_size))
        except Exception as exc:  # noqa: BLE001
            self.warning(f"sound bank unreadable: {exc}")

    def _read_font_bank(self, data: bytes) -> None:
        r = Reader(data)
        try:
            count = r.i32()
            if count < 0 or count > 4096:
                return
            for _ in range(count):
                handle = r.u32()
                if self.build > 284:
                    handle -= 1
                decomp_size = r.i32()
                comp_size = r.i32()
                if comp_size < 0 or comp_size > r.remaining():
                    break
                try:
                    payload = zlib.decompress(r.read(comp_size))
                except zlib.error:
                    payload = b""
                self.fonts.append(FontItem(handle, payload))
        except Exception as exc:  # noqa: BLE001
            self.warning(f"font bank unreadable: {exc}")

    # -- frames -------------------------------------------------------------

    def _read_frame(self, data: bytes, handle: int) -> None:
        r = Reader(data)
        frame = Frame(handle, f"Frame {len(self.frames) + 1}", 0, 0,
                      (0, 0, 0, 0), 0, 0, "")
        try:
            for chunk in self._decoded_chunks(r):
                if chunk.id == FRAME_HEADER:
                    hr = Reader(chunk.raw)
                    if hr.remaining() >= 16:
                        frame.size_x = hr.i32()
                        frame.size_y = hr.i32()
                        frame.background = hr.color()
                        frame.flags = hr.u32()
                elif chunk.id == FRAME_NAME:
                    frame.name = _cstring(Reader(chunk.raw), self.unicode)
                elif chunk.id == FRAME_INSTANCES:
                    ir = Reader(chunk.raw)
                    count = ir.i32()
                    if count < 0 or count > 65536:
                        self.warning(
                            f"{frame.name or 'A frame'}: implausible "
                            f"instance count {count}; instances skipped"
                        )
                        continue
                    for _ in range(count):
                        if ir.remaining() < 20:
                            break
                        inst_handle = ir.u16()
                        object_info = ir.u16()
                        x = ir.i32()
                        y = ir.i32()
                        parent_type = ir.i16()
                        parent_handle = ir.i16()
                        layer = ir.i16()
                        ir.i16()  # instance number
                        if object_info not in self.objects:
                            self._note_missing_object(
                                frame.name, inst_handle, object_info)
                            continue
                        frame.instances.append(FrameInstance(
                            x, y, layer, inst_handle, 0, parent_type,
                            object_info, parent_handle))
                elif chunk.id == FRAME_LAYERS:
                    lr = Reader(chunk.raw)
                    count = lr.u32()
                    if count > 4096:
                        continue
                    for _ in range(count):
                        if lr.remaining() < 20:
                            break
                        flags = lr.u32()
                        xc = lr.f32()
                        yc = lr.f32()
                        lr.i32()  # number of backgrounds
                        lr.i32()  # background index
                        name = _cstring(lr, self.unicode)
                        frame.layers.append(Layer(name, flags, xc, yc))
                elif chunk.id == FRAME_EVENTS:
                    self.event_sizes.append(len(chunk.raw))
                # palette (13111), transitions (13113-13116), virtual size
                # (13122), random seed (13124), layer effects (13125) are
                # not needed for the SB3 export.
        except Exception as exc:  # noqa: BLE001
            self.warning(f"frame {frame.name} unreadable: {exc}")
        frame.items = list(self.objects.values())
        self.frames.append(frame)

    # -- top level ----------------------------------------------------------

    def interpret(self, chunks: List[_Chunk]) -> None:
        decryptor = _Decryptor(self.build)
        self.decryptor = decryptor

        # Pre-pass: pull the key materials out of the (rarely encrypted)
        # string chunks so encrypted chunks can be decoded immediately.
        for chunk in chunks:
            if chunk.id not in (CHUNK_APP_NAME, CHUNK_COPYRIGHT,
                                CHUNK_EDITOR_FILENAME, CHUNK_TARGET_FILENAME):
                continue
            data = _decode_chunk(chunk, decryptor, self.name, self.copyright,
                                 self.editor_filename, self.warnings)
            if data is None:
                continue
            if chunk.id == CHUNK_APP_NAME:
                self._read_app_name(data)
            elif chunk.id == CHUNK_COPYRIGHT:
                self.copyright = _cstring(Reader(data), self.unicode)
            elif chunk.id == CHUNK_EDITOR_FILENAME:
                self.editor_filename = _cstring(Reader(data), self.unicode)
            elif chunk.id == CHUNK_TARGET_FILENAME:
                self.target_filename = _cstring(Reader(data), self.unicode)

        header_25 = names_25 = props_25 = None
        frame_chunks = []
        if self.progress is not None:
            self.progress.phase("chunks", total=len(chunks))
            self.progress.step("decrypting game-data chunks")
        for idx, chunk in enumerate(chunks, start=1):
            if self.progress is not None:
                self.progress.tick(idx, step=f"decrypting chunk {idx}/{len(chunks)}")
            if chunk.id == CHUNK_FRAME:
                # Frames reference objects/banks, so interpret them after
                # everything else has been assembled.
                frame_chunks.append(chunk)
                continue
            data = self._decoded(chunk, decryptor)
            if data is None:
                continue
            cid = chunk.id
            if cid == CHUNK_APP_HEADER:
                self._read_app_header(data)
            elif cid == CHUNK_APP_NAME:
                if not self.name:
                    self._read_app_name(data)
            elif cid == CHUNK_APP_AUTHOR:
                self.author = _cstring(Reader(data), self.unicode)
            elif cid == CHUNK_COPYRIGHT:
                if not self.copyright:
                    self.copyright = _cstring(Reader(data), self.unicode)
            elif cid == CHUNK_EDITOR_FILENAME:
                if not self.editor_filename:
                    self.editor_filename = _cstring(Reader(data), self.unicode)
            elif cid == CHUNK_TARGET_FILENAME:
                if not self.target_filename:
                    self.target_filename = _cstring(Reader(data), self.unicode)
            elif cid in (CHUNK_FRAME_ITEMS_OLD1, CHUNK_FRAME_ITEMS_OLD2):
                self._read_frame_items_chunked(data)
            elif cid == CHUNK_FRAME_ITEMS_25:
                header_25 = data
            elif cid == CHUNK_FRAME_ITEM_NAMES_25:
                names_25 = data
            elif cid == CHUNK_FRAME_ITEM_PROPS_25:
                props_25 = data
            elif cid == CHUNK_FRAME_HANDLES:
                self._read_frame_handles(data)
            elif cid == CHUNK_GLOBAL_VALUES:
                self._read_globals(data, strings=False)
            elif cid == CHUNK_GLOBAL_STRINGS:
                self._read_globals(data, strings=True)
            elif cid == CHUNK_EXTENSIONS:
                self._read_extensions(data)
            elif cid == CHUNK_IMAGE_BANK:
                self._read_image_bank(data)
            elif cid == CHUNK_SOUND_BANK:
                self._read_sound_bank(data)
            elif cid == CHUNK_FONT_BANK:
                self._read_font_bank(data)
            # Extension data (8748), app icon (8757), binary files (8760),
            # exe-only flag (8768), shaders (8771), extended header (8773),
            # 2.5+ object shaders (8789), TTF fonts (8793): not needed for
            # the SB3 export.

        # The 2.5+ object header/names/properties come as three separate
        # chunks; assemble them once all are known.
        if header_25 is not None:
            self._read_frame_items_25(header_25, names_25, props_25)

        # Now that objects, globals and banks exist, interpret the frames.
        if self.progress is not None and frame_chunks:
            self.progress.phase("frames", total=len(frame_chunks))
        for idx, chunk in enumerate(frame_chunks, start=1):
            data = self._decoded(chunk, decryptor)
            if data is None:
                continue
            if self.progress is not None:
                self.progress.step(f"parsing frame {idx}/{len(frame_chunks)}")
            handle = (
                self.frame_handles[len(self.frames)]
                if len(self.frames) < len(self.frame_handles)
                else len(self.frames)
            )
            self._read_frame(data, handle)
            if self.progress is not None:
                self.progress.tick(idx)

        self._fold_missing_object_notes()


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

PACK_HEADER = 0x77777777
PACK_MAGIC = 0x12478749


def _looks_like_game_header(data: bytes, off: int) -> bool:
    """True when ``off`` is a real PAME/PAMU game-data header, not a pack trailer."""
    if off < 0 or off + 16 > len(data):
        return False
    if data[off : off + 4] not in GAME_HEADERS:
        return False
    runtime = struct.unpack_from("<H", data, off + 4)[0]
    build = struct.unpack_from("<i", data, off + 12)[0]
    # Fusion 2.0 / 2.5 sit around 0x300; MMF 1.5 CNC is 0x207 (rejected later
    # with a specific error).  Pack trailers have zeros here.
    if not (0x200 <= runtime <= 0x500):
        return False
    if build <= 0 or build > 9999:
        return False
    return True


def _offset_after_pack(data: bytes, start: int) -> Optional[int]:
    if start + 16 > len(data):
        return None
    header_word, magic_word = struct.unpack_from("<II", data, start)
    if header_word != PACK_HEADER or magic_word != PACK_MAGIC:
        return None
    data_size = struct.unpack_from("<I", data, start + 12)[0]
    game = start + data_size
    if game + 4 <= len(data):
        return game
    return None


def _scan_for_game_data(data: bytes, from_off: int = 0) -> Optional[int]:
    """Search for a valid PAME/PAMU header, skipping pack payloads when found."""
    start = max(from_off, 0)
    # PackData magic as little-endian bytes (0x77777777, 0x12478749).
    pack_sig = struct.pack("<II", PACK_HEADER, PACK_MAGIC)
    pos = start
    while True:
        pame = data.find(b"PAME", pos)
        pamu = data.find(b"PAMU", pos)
        pack = data.find(pack_sig, pos)
        candidates = [n for n in (pame, pamu, pack) if n >= 0]
        if not candidates:
            return None
        first = min(candidates)
        if first == pack:
            after = _offset_after_pack(data, pack)
            if after is not None and _looks_like_game_header(data, after):
                return after
            pos = pack + 1
            continue
        if _looks_like_game_header(data, first):
            return first
        pos = first + 1


def find_game_data_offset(data: bytes) -> Optional[int]:
    """Return the offset where the PAME/PAMU game data starts, if any.

    A file may hold the game data in three shapes:

    * a raw game-data file that *starts* with PAME/PAMU (offset 0),
    * an EXE whose game data follows the PE sections directly,
    * an EXE with a Fusion "pack" first, game data after the pack.

    Real Clickteam EXEs (FNaF included) often have a PE optional header
    whose size is not the 224-byte constant older readers assumed, or a
    few bytes of padding before the pack.  After the PE overlay we also
    *scan* for PackData / PAME/PAMU so those layouts still convert.
    """
    if _looks_like_game_header(data, 0):
        return 0
    start = None
    try:
        start = exe_pack.pe_overlay_offset(data)
    except exe_pack.PackError:
        start = None
    if start is not None:
        after_pack = _offset_after_pack(data, start)
        if after_pack is not None and _looks_like_game_header(data, after_pack):
            return after_pack
        if _looks_like_game_header(data, start):
            return start
        found = _scan_for_game_data(data, start)
        if found is not None:
            return found
    return _scan_for_game_data(data, 0)


def load_game_data_from_exe(source, progress=None) -> Tuple[MFA, List[str]]:
    """Read an F2.5 EXE's PAME/PAMU game data and rebuild an MFA object.

    ``source`` may be a file path or the raw EXE bytes.  Returns
    ``(mfa, notes)``.  Raises :class:`GameDataError` when the file does
    not contain readable F2.5 game data (old MMF 1.5 builds, protected or
    encrypted-by-unknown-means games, etc.).
    """
    global _GLOBAL_UNICODE
    from .progress import NULL as _NULL
    if progress is None:
        progress = _NULL
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        with open(source, "rb") as fh:
            data = fh.read()

    offset = find_game_data_offset(data)
    if offset is None:
        raise GameDataError(
            "no PAME/PAMU game data found after the PE sections "
            "(not a Fusion 2.5 executable, or an unsupported layout)"
        )

    r = Reader(data)
    r.seek(offset)
    magic = r.read(4)
    if magic not in GAME_HEADERS:
        raise GameDataError(f"expected PAME/PAMU game header, found {magic!r}")

    reader = _GameReader()
    reader.unicode = magic == b"PAMU"
    _GLOBAL_UNICODE = reader.unicode
    reader.progress = progress

    runtime_version = r.u16()
    if runtime_version == CNCV1_VERSION:
        raise GameDataError(
            "MMF 1.5-era CNC game data is not supported by the built-in "
            "reader (this build predates Fusion 2.0)"
        )
    reader.runtime_version = runtime_version
    r.u16()  # runtime subversion
    reader.product_version = r.i32()
    reader.build = r.i32()
    if reader.build <= 0 or reader.build > 9999:
        raise GameDataError(f"implausible Fusion build number {reader.build}")

    chunks = _read_chunk_list(r)
    reader.two_five_plus = any(c.id == CHUNK_FRAME_ITEMS_25 for c in chunks)
    reader.interpret(chunks)

    if not reader.frames:
        raise GameDataError("game data contains no frames")

    mfa = MFA(
        name=reader.name or "Untitled",
        description="",
        path=reader.target_filename or reader.editor_filename or "",
        author=reader.author or "",
        copyright=reader.copyright or "",
        company="",
        version=str(reader.product_version) or "2.5",
        window_x=reader.window_x,
        window_y=reader.window_y,
        frame_rate=reader.frame_rate or 50,
        mfa_build=0,
        build_version=reader.build,
        build_type=0,
        images=reader.images,
        sounds=reader.sounds,
        fonts=reader.fonts,
        global_values=reader.global_values,
        global_strings=reader.global_strings,
        frames=reader.frames,
        file_data=data,
    )

    notes: List[str] = []
    if reader.name:
        notes.append(f"game name: {reader.name}")
    if reader.author:
        notes.append(f"author: {reader.author}")
    notes.append(
        f"Fusion build {reader.build} (runtime version "
        f"{runtime_version:#x})"
    )
    if reader.two_five_plus:
        notes.append("Fusion 2.5+ object/image layout detected")
    if reader.event_sizes:
        total = sum(reader.event_sizes)
        notes.append(
            f"{len(reader.event_sizes)} frame(s) carry compiled event "
            f"programs ({total} bytes total); compiled events are not "
            "decoded into Scratch blocks"
        )
    for w in reader.warnings:
        notes.append(f"warning: {w}")
    return mfa, notes
