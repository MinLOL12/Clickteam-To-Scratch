"""Helpers to synthesize minimal Clickteam Fusion 2.5 EXEs for tests.

Builds the same binary layout the converter expects:

    [ PE header + one section ] [ optional pack (PackData) ] [ PAME/PAMU game data ]
"""
from __future__ import annotations

import struct
import zlib

LAST_CHUNK_ID = 32639


# --------------------------------------------------------------------------
# PE
# --------------------------------------------------------------------------

def pe_header(section_name: bytes, raw_ptr: int, raw_size: int) -> bytes:
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 0x40)  # e_lfanew
    pe = bytearray()
    pe += b"PE\x00\x00"
    pe += struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x102)
    pe += b"\x00" * 224  # SizeOfOptionalHeader (PE32 + 16 data directories)
    pe += section_name.ljust(8, b"\x00")
    pe += struct.pack("<IIII", 0, 0, raw_size, raw_ptr)
    pe += b"\x00" * 24
    return bytes(dos) + bytes(pe)


def wrap_exe(appended: bytes, section_name: bytes = b".extra",
             raw_ptr: int = 0x200) -> bytes:
    hdr = pe_header(section_name, raw_ptr, len(appended))
    return hdr + b"\x00" * (raw_ptr - len(hdr)) + appended


# --------------------------------------------------------------------------
# pack (PackData)
# --------------------------------------------------------------------------

def pack_item(name: str, data: bytes, compressed: bool, unicode: bool) -> bytes:
    raw = name.encode("utf-16-le") if unicode else name.encode("latin-1")
    out = struct.pack("<H", len(name))
    out += raw
    payload = zlib.compress(data) if compressed else data
    out += struct.pack("<ii", 0x1234, len(payload))
    if compressed:
        out += struct.pack("<H", 0xD9F8)  # bingo marker
    out += payload
    return out


def build_pack(pack_files, unicode: bool) -> bytes:
    """pack_files: list of (name, data, compressed)."""
    items = b"".join(pack_item(n, d, c, unicode) for n, d, c in pack_files)
    data_size = 64 + len(items)
    pack = bytearray()
    pack += struct.pack("<II", 0x77777777, 0x12478749)  # PackData header
    pack += struct.pack("<II", 32, data_size)
    pack += struct.pack("<IiiI", 2, 0, 0, len(pack_files))
    pack += items
    pack += (b"PAMU" if unicode else b"PAME") + b"\x00" * 28  # trailer
    assert len(pack) == data_size
    return bytes(pack)


# --------------------------------------------------------------------------
# game-data chunks
# --------------------------------------------------------------------------

def chunk(cid: int, data: bytes, flags: int = 0) -> bytes:
    return struct.pack("<hhi", cid, flags, len(data)) + data


def last_chunk() -> bytes:
    return struct.pack("<hhi", LAST_CHUNK_ID, 0, 0)


def wstring(s: str) -> bytes:
    """Null-terminated UTF-16 string (PAMU builds)."""
    return s.encode("utf-16-le") + b"\x00\x00"


def game_string(s: str, unicode: bool = True) -> bytes:
    """Null-terminated string in the game's own encoding (PAME = ASCII)."""
    if unicode:
        return wstring(s)
    return s.encode("latin-1", "replace") + b"\x00"


def app_header_chunk(width=640, height=480, score=0, lives=3,
                     frames=1, rate=60) -> bytes:
    data = bytearray()
    data += struct.pack("<i", 0)          # size
    data += struct.pack("<HHhH", 0, 0, 0, 0)
    data += struct.pack("<HH", width, height)
    data += struct.pack("<I", score ^ 0xFFFFFFFF)
    data += struct.pack("<I", lives ^ 0xFFFFFFFF)
    data += b"\x00" * 72                  # 4 player controls
    data += b"\x00\x00\x00\x00"           # border color
    data += struct.pack("<i", frames)
    data += struct.pack("<i", rate)
    data += struct.pack("<i", 0)          # window menu index
    return chunk(8739, bytes(data))


def global_values_chunk(values) -> bytes:
    data = struct.pack("<h", len(values))
    data += b"".join(struct.pack("<i", v) for v in values)
    data += b"\x00" * len(values)         # type bytes (0 = int)
    return chunk(8754, data)


def global_strings_chunk(strings) -> bytes:
    data = struct.pack("<i", len(strings))
    data += b"".join(wstring(s) for s in strings)
    return chunk(8755, data)


# -- 2.5+ frame items (chunks 8787/8788/8790) -------------------------------

def object_header_25(handle=0, object_type=2, flags=0) -> bytes:
    data = struct.pack("<hhh", handle, object_type, flags) + b"\x00\x00"
    data += b"\x01"                       # ink effect 1
    data += b"\x00" + b"\x00\x00" + b"\x00" + b"\x00\x00\x00"
    return data


def object_names_25(names) -> bytes:
    return b"".join(wstring(n) for n in names)


def object_props_25(props_list) -> bytes:
    data = struct.pack("<i", 0)
    for p in props_list:
        comp = zlib.compress(p)
        data += struct.pack("<i", len(comp)) + comp + b"\x00\x00\x00\x00"
    return data


def _animations_block(frames_per_anim):
    """Animations block (ObjectCommon layout) referencing image handles."""
    block = bytearray(struct.pack("<hh", 0, len(frames_per_anim)))
    offsets_pos = len(block)
    block += b"\x00" * (2 * len(frames_per_anim))
    for i, frames in enumerate(frames_per_anim):
        # Animation: 32 direction offsets, direction 0 = first
        anim = bytearray(struct.pack("<h", 64) + b"\x00" * (31 * 2))
        direction = bytearray(struct.pack("<bbhh", 30, 30, 0, 0))
        direction += struct.pack("<H", len(frames))
        direction += b"".join(struct.pack("<h", f) for f in frames)
        anim += direction
        struct.pack_into("<h", block, offsets_pos + 2 * i, len(block))
        block += anim
    struct.pack_into("<h", block, 0, len(block))
    return bytes(block)


def object_common_25(frames_per_anim=((0,),), values=(), strings=()) -> bytes:
    """ObjectCommon for a 2.5+ Active object."""
    header = bytearray()
    header += struct.pack("<i", 0)        # size (patched below)
    header += struct.pack("<hh", 0, 0)    # animations offset, movements offset
    header += struct.pack("<H", 0)        # version
    header += b"\x00\x00"
    header += struct.pack("<hh", 0, 0)    # extension offset, counter offset
    header += struct.pack("<Hh", 0, 0)    # flags, marker
    header += b"\x00" * 16                # 8 qualifiers
    header += struct.pack("<hhh", 0, 0, 0)  # system, values, strings offsets
    header += struct.pack("<HH", 0, 0)    # new flags, preferences
    header += b"SPRX"                     # identifier
    header += b"\xff\xff\xff\xff"         # background color
    header += struct.pack("<II", 0, 0)    # fade in/out offsets
    header_len = len(header)

    anim = _animations_block(frames_per_anim)
    values_block = struct.pack("<h", len(values))
    values_block += b"".join(struct.pack("<i", v) for v in values)
    values_block += struct.pack("<i", 0)
    strings_block = struct.pack("<h", len(strings))
    strings_block += b"".join(wstring(s) for s in strings)

    anim_off = header_len
    values_off = header_len + len(anim)
    strings_off = values_off + len(values_block)
    struct.pack_into("<h", header, 4, anim_off)
    struct.pack_into("<h", header, 38, values_off)
    struct.pack_into("<h", header, 40, strings_off)

    block = header + anim + values_block + strings_block
    struct.pack_into("<i", block, 0, len(block))
    return bytes(block)


def object_common_old(frames_per_anim=((0,),), values=(), strings=()) -> bytes:
    """ObjectCommon for pre-2.5 layouts (the classic branch)."""
    header = bytearray()
    header += struct.pack("<i", 0)        # size (patched below)
    header += struct.pack("<hh", 0, 0)    # movements offset, animations offset
    header += struct.pack("<h", 0)        # version
    header += struct.pack("<hh", 0, 0)    # counter offset, system offset
    header += b"\x00\x00"
    header += struct.pack("<Hh", 0, 0)    # flags, marker
    header += b"\x00" * 16                # 8 qualifiers
    header += struct.pack("<h", 0)        # extension offset
    header += struct.pack("<hh", 0, 0)    # values offset, strings offset
    header += struct.pack("<HH", 0, 0)    # new flags, preferences
    header += b"SP"                       # identifier (2 bytes)
    header += b"\xff\xff\xff\xff"         # background color
    header += struct.pack("<II", 0, 0)    # fade in/out offsets
    header_len = len(header)

    anim = _animations_block(frames_per_anim)
    values_block = struct.pack("<h", len(values))
    values_block += b"".join(struct.pack("<i", v) for v in values)
    values_block += struct.pack("<i", 0)
    strings_block = struct.pack("<h", len(strings))
    strings_block += b"".join(wstring(s) for s in strings)

    anim_off = header_len
    values_off = header_len + len(anim)
    strings_off = values_off + len(values_block)
    struct.pack_into("<h", header, 6, anim_off)   # animations offset field
    struct.pack_into("<h", header, 38, values_off)
    struct.pack_into("<h", header, 40, strings_off)

    block = header + anim + values_block + strings_block
    struct.pack_into("<i", block, 0, len(block))
    return bytes(block)


def object_common_284(frames_per_anim=((0,),), values=(), strings=(),
                      check0=False) -> bytes:
    """ObjectCommon in the MMF2 build-284 (FNaF 1 era) layout.

    ``check0=True`` selects the variant field order CTFAK treats specially
    for build 284 (counter offset first, 32-bit version).
    """
    header = bytearray()
    header += struct.pack("<i", 0)          # size (patched below)
    if check0:
        header += struct.pack("<h", 0)      # counter offset
        header += struct.pack("<i", 0)      # version (0 -> check == 0)
        header += struct.pack("<hh", 0, 0)  # movements offset, extension
        anim_at = len(header)
        header += struct.pack("<h", 0)      # animations offset
    else:
        anim_at = len(header)
        header += struct.pack("<hh", 0, 0)  # animations offset, movements
        header += struct.pack("<i", 1)      # version (non-zero -> check != 0)
        header += struct.pack("<hh", 0, 0)  # extension, counter offsets
    header += struct.pack("<Hh", 0, 0)      # flags, marker
    header += b"\x00" * 16                  # 8 qualifiers
    header += struct.pack("<hhh", 0, 0, 0)  # system, values, strings offsets
    header += struct.pack("<HH", 0, 0)      # new flags, preferences
    header += b"SPRX"                       # identifier (4 bytes)
    header += b"\xff\xff\xff\xff"           # background color
    header += struct.pack("<II", 0, 0)      # fade in/out offsets
    header_len = len(header)

    anim = _animations_block(frames_per_anim)
    values_block = struct.pack("<h", len(values))
    values_block += b"".join(struct.pack("<i", v) for v in values)
    values_block += struct.pack("<i", 0)
    strings_block = struct.pack("<h", len(strings))
    strings_block += b"".join(wstring(s) for s in strings)

    anim_off = header_len
    values_off = header_len + len(anim)
    strings_off = values_off + len(values_block)
    struct.pack_into("<h", header, anim_at, anim_off)
    struct.pack_into("<h", header, 38, values_off)
    struct.pack_into("<h", header, 40, strings_off)

    block = header + anim + values_block + strings_block
    struct.pack_into("<i", block, 0, len(block))
    return bytes(block)


# -- old-style chunked frame items (chunks 8745/8767) -----------------------

def object_info_header_payload(handle=0, object_type=2, flags=0) -> bytes:
    data = struct.pack("<hhh", handle, object_type, flags) + b"\x00\x00"
    data += b"\x01" + b"\x00" + b"\x00\x00" + b"\x00" + b"\x00\x00\x00"
    return data


def object_info_header(handle=0, object_type=2, flags=0) -> bytes:
    return chunk(17476, object_info_header_payload(handle, object_type, flags))


def zlib_wrap(payload: bytes):
    """Inner-chunk transform: zlib-compress the payload (chunk flag 1)."""
    comp = zlib.compress(payload)
    return 1, struct.pack("<II", len(payload), len(comp)) + comp


def frame_items_old(objects, compress=False, transform=None,
                    unicode=True) -> bytes:
    """objects: list of (handle, type, name, props_bytes).

    ``transform`` maps payload -> (flags, stored_bytes) and is applied to
    every inner ObjectInfo chunk; real MMF2 games store these chunks
    compressed and/or encrypted.  ``compress=True`` is shorthand for
    ``transform=zlib_wrap``.
    """
    if compress:
        transform = zlib_wrap
    data = struct.pack("<i", len(objects))
    for handle, object_type, name, props in objects:
        for cid, payload in (
                (17476, object_info_header_payload(handle, object_type)),
                (17477, game_string(name, unicode)),
                (17478, props)):
            if transform is None:
                data += chunk(cid, payload)
            else:
                flags, stored = transform(payload)
                data += struct.pack("<hhi", cid, flags, len(stored)) + stored
        data += last_chunk()
    return chunk(8745, data)


# -- image bank -------------------------------------------------------------

def image_item_normal(handle, width, height, pixels, mode=4, flags=0,
                      transparent=(255, 0, 255, 0), build=294) -> bytes:
    inner = bytearray()
    inner += struct.pack("<iii", 0, 0, len(pixels))   # checksum, refs, size
    inner += struct.pack("<hh", width, height)
    inner += struct.pack("<BB", mode, flags)
    inner += b"\x00\x00"
    inner += struct.pack("<hhhh", 0, 0, 0, 0)         # hotspot/action
    inner += struct.pack("<BBBB", *transparent)
    inner += pixels
    payload = zlib.compress(bytes(inner))
    stored_handle = handle + 1 if build >= 284 else handle
    data = struct.pack("<iii", stored_handle, len(inner), len(payload))
    data += payload
    return data


def image_bank(items) -> bytes:
    data = struct.pack("<i", len(items)) + b"".join(items)
    return chunk(26214, data)


def lz4_literals(data: bytes) -> bytes:
    """Encode ``data`` as an LZ4 block made of one literal-only sequence.

    Only the final sequence of an LZ4 block may omit its match, so the
    whole payload is encoded as a single (possibly extended) literal run.
    """
    if len(data) < 15:
        return bytes([len(data) << 4]) + data
    out = bytearray()
    out.append(0xF0)
    extra = len(data) - 15
    while extra >= 255:
        out.append(255)
        extra -= 255
    out.append(extra)
    out += data
    return bytes(out)


def image_item_25(handle, width, height, pixels, mode=8, flags=0x80,
                  transparent=(0, 0, 0, 0)) -> bytes:
    raw = lz4_literals(pixels)
    data = bytearray()
    data += struct.pack("<i", handle + 1)
    data += struct.pack("<ii", 0, 0)                  # checksum, references
    data += struct.pack("<i", 0)                      # unknown
    data += struct.pack("<i", len(raw) + 4)           # data size
    data += struct.pack("<hh", width, height)
    data += struct.pack("<BB", mode, flags)
    data += b"\x00\x00"
    data += struct.pack("<hhhh", 0, 0, 0, 0)
    data += struct.pack("<BBBB", *transparent)
    data += struct.pack("<i", len(pixels))            # decompressed size
    data += raw
    return bytes(data)


# -- frames ----------------------------------------------------------------

def frame_instance(handle=0, object_info=0, x=320, y=240, parent_type=0,
                   parent_handle=0, layer=0) -> bytes:
    return struct.pack("<HHiiHHhh", handle, object_info, x, y, parent_type,
                       parent_handle, layer, 0)


def frame_data(name="Frame 1", width=640, height=480, instances=(),
               layers=(), events=b"", transform=None, unicode=True) -> bytes:
    """Raw nested frame chunks (without the outer 13107 wrapper).

    ``transform`` maps payload -> (flags, stored_bytes) and is applied to
    every inner chunk; real games compress and/or encrypt these payloads.
    """
    def put(cid, payload):
        if transform is None:
            return chunk(cid, payload)
        flags, stored = transform(payload)
        return struct.pack("<hhi", cid, flags, len(stored)) + stored

    data = b""
    hdr = struct.pack("<ii", width, height)
    hdr += b"\x00\x00\x00\x00"            # background
    hdr += struct.pack("<I", 0)           # flags
    data += put(13108, hdr)
    data += put(13109, game_string(name, unicode))
    inst = struct.pack("<i", len(instances))
    inst += b"".join(instances)
    data += put(13112, inst)
    if events:
        data += put(13117, events)
    lay = struct.pack("<I", len(layers))
    for lname, xc, yc in layers:
        lay += struct.pack("<I", 0) + struct.pack("<ff", xc, yc)
        lay += struct.pack("<ii", 0, 0) + game_string(lname, unicode)
    data += put(13121, lay)
    data += last_chunk()
    return data


def frame_chunk(name="Frame 1", width=640, height=480, instances=(),
                layers=(), events=b"", compress=False, transform=None,
                unicode=True) -> bytes:
    if compress:
        transform = zlib_wrap
    return chunk(13107, frame_data(name, width, height, instances, layers,
                                   events, transform=transform,
                                   unicode=unicode))


# -- whole game data / exe --------------------------------------------------

def build_game_data(name="My Game", unicode=True, build=294, images=(),
                    objects_25=None, object_names=(), old_objects=None,
                    frames=(), frame_handles=(0,), extra_chunks=()):
    """objects_25: list of ObjectCommon blocks (2.5+ layout)."""
    header = (b"PAMU" if unicode else b"PAME")
    header += struct.pack("<HHii", 0x302, 0, 2, build)
    parts = [app_header_chunk(frames=len(frames) or len(frame_handles))]
    parts.append(chunk(8740, game_string(name, unicode)))
    parts.append(chunk(8741, game_string("Tester", unicode)))
    parts.append(chunk(8763, game_string("(c) tests", unicode)))
    parts.append(chunk(8750, game_string("game.mfa", unicode)))
    parts.append(chunk(8751, game_string("game.exe", unicode)))
    parts.append(global_values_chunk((0, 3)))
    parts.append(global_strings_chunk(("Hello",)))
    if objects_25 is not None:
        parts.append(chunk(8787, b"".join(
            object_header_25(i, 2) for i in range(len(objects_25)))))
        parts.append(chunk(8788, object_names_25(object_names)))
        parts.append(chunk(8790, object_props_25(objects_25)))
    if old_objects is not None:
        parts.append(frame_items_old(old_objects, unicode=unicode))
    parts.append(chunk(8747, b"".join(
        struct.pack("<h", h) for h in frame_handles)))
    parts.extend(frames)
    parts.append(image_bank(images))
    parts.extend(extra_chunks)
    parts.append(last_chunk())
    return header + b"".join(parts)


def build_exe(game_data: bytes, pack_files=None, unicode=True,
              section=b".extra", code=b"") -> bytes:
    """pack_files: list of (name, data, compressed) or None for no pack."""
    appended = bytearray()
    appended += code
    if pack_files is not None:
        appended += build_pack(pack_files, unicode)
    appended += game_data
    return wrap_exe(bytes(appended), section)
