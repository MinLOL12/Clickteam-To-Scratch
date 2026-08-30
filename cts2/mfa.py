"""Independent Python reader for Clickteam Fusion (MMF2 / Fusion 2.5) .mfa files.

The binary layout was reverse-engineered by the community (CTFAK/Anaconda).
This module re-implements a useful subset in Python so we can export
projects to Scratch / PenguinMod SB3 files. It does not ship or depend on
CTFAK's C# code.
"""
from __future__ import annotations

import hashlib
import logging
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .bin import Reader
from .ctimage import decode_bmp

log = logging.getLogger("cts2.mfa")

OBJECT_TYPES = {
    0: "QuickBackdrop",
    1: "Backdrop",
    2: "Active",
    3: "Text",
    4: "Question",
    5: "Score",
    6: "Lives",
    7: "Counter",
    32: "Extension",
}


@dataclass
class ImageItem:
    handle: int
    checksum: int = 0
    references: int = 0
    size: int = 0
    width: int = 0
    height: int = 0
    graphic_mode: int = 0
    flags: int = 0
    hotspot_x: int = 0
    hotspot_y: int = 0
    action_x: int = 0
    action_y: int = 0
    transparent: Tuple[int, int, int, int] = (0, 0, 0, 0)
    png: Optional[bytes] = None
    raw: bytes = b""

    @property
    def sha(self) -> str:
        if self.png is None:
            return ""
        return hashlib.sha1(self.png).hexdigest()

    def as_dict(self) -> dict:
        return {
            "handle": self.handle,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "mode": self.graphic_mode,
            "flags": self.flags,
            "hotspot": [self.hotspot_x, self.hotspot_y],
            "action": [self.action_x, self.action_y],
            "transparent": list(self.transparent),
        }


@dataclass
class SoundItem:
    handle: int
    checksum: int = 0
    references: int = 0
    flags: int = 0
    name: str = ""
    data: bytes = b""
    decompressed_size: int = 0

    @property
    def sha(self) -> str:
        return hashlib.sha1(self.data).hexdigest()


@dataclass
class FontItem:
    handle: int
    data: bytes = b""


@dataclass
class ValueItem:
    name: str
    value: Any = 0
    type: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "value": self.value}


@dataclass
class FrameInstance:
    x: int
    y: int
    layer: int
    handle: int
    flags: int
    parent_type: int
    item_handle: int
    parent_handle: int = 0


@dataclass
class Layer:
    name: str
    flags: int
    x_coeff: float
    y_coeff: float


@dataclass
class Movement:
    name: str
    extension: str
    identifier: int
    player: int
    type: int
    moving_at_start: int
    direction_at_start: int
    data: bytes = b""


@dataclass
class AnimationDirection:
    index: int
    min_speed: int
    max_speed: int
    repeat: int
    back_to: int
    frames: List[int] = field(default_factory=list)


@dataclass
class Animation:
    name: str
    directions: List[AnimationDirection] = field(default_factory=list)


@dataclass
class ObjectData:
    object_type: int
    handle: int
    name: str
    flags: int = 0
    values: List[ValueItem] = field(default_factory=list)
    strings: List[ValueItem] = field(default_factory=list)
    movements: List[Movement] = field(default_factory=list)
    animations: List[Animation] = field(default_factory=list)
    image_handle: int = -1
    images: List[int] = field(default_factory=list)
    width: int = 0
    height: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def frames(self) -> List[int]:
        """All image handles referenced by this object, in display order."""
        result: List[int] = []
        for anim in self.animations:
            for d in anim.directions:
                for f in d.frames:
                    if f not in result:
                        result.append(f)
        for img in self.images:
            if img not in result:
                result.append(img)
        if self.image_handle != -1 and self.image_handle not in result:
            result.append(self.image_handle)
        if not result:
            result = [self.image_handle]
        return result


@dataclass
class Expression:
    object_type: int
    num: int
    size: int
    value: Any = None
    raw: bytes = b""

    def as_dict(self) -> dict:
        return {"object_type": self.object_type, "num": self.num, "value": self.value}


@dataclass
class Parameter:
    code: int
    size: int
    value: Any = None
    raw: bytes = b""

    def as_dict(self) -> dict:
        return {"code": self.code, "value": self.value}


@dataclass
class Condition:
    object_type: int
    num: int
    object_info: int
    object_info_list: int
    flags: int
    other_flags: int
    num_params: int
    def_type: int
    identifier: int
    size: int = 0
    parameters: List[Parameter] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "num": self.num,
            "object_info": self.object_info,
            "parameters": [p.as_dict() for p in self.parameters],
        }


@dataclass
class Action:
    object_type: int
    num: int
    object_info: int
    object_info_list: int
    flags: int
    other_flags: int
    num_params: int
    def_type: int
    size: int = 0
    parameters: List[Parameter] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "num": self.num,
            "object_info": self.object_info,
            "parameters": [p.as_dict() for p in self.parameters],
        }


@dataclass
class EventGroup:
    size: int
    num_conditions: int
    num_actions: int
    flags: int
    is_restricted: int
    restrict_cpt: int
    identifier: int
    undo: int
    conditions: List[Condition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "flags": self.flags,
            "conditions": [c.as_dict() for c in self.conditions],
            "actions": [a.as_dict() for a in self.actions],
        }


@dataclass
class Frame:
    handle: int
    name: str
    size_x: int
    size_y: int
    background: Tuple[int, int, int, int]
    flags: int
    max_objects: int
    password: str
    layers: List[Layer] = field(default_factory=list)
    items: List[ObjectData] = field(default_factory=list)
    instances: List[FrameInstance] = field(default_factory=list)
    event_groups: List[EventGroup] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "handle": self.handle,
            "name": self.name,
            "size": [self.size_x, self.size_y],
            "background": list(self.background),
            "items": [
                {
                    "type": obj.object_type,
                    "type_name": OBJECT_TYPES.get(obj.object_type, "Unknown"),
                    "name": obj.name,
                    "images": obj.frames,
                    "values": [v.as_dict() for v in obj.values],
                }
                for obj in self.items
            ],
            "instances": [
                {
                    "x": i.x,
                    "y": i.y,
                    "layer": i.layer,
                    "item": i.item_handle,
                    "name": self._instance_name(i),
                }
                for i in self.instances
            ],
            "events": [g.as_dict() for g in self.event_groups],
        }

    def _instance_name(self, inst: FrameInstance) -> str:
        for obj in self.items:
            if obj.handle == inst.item_handle:
                return obj.name
        return f"Object {inst.item_handle}"


@dataclass
class MFA:
    name: str
    description: str
    path: str
    author: str
    copyright: str
    company: str
    version: str
    window_x: int
    window_y: int
    frame_rate: int
    mfa_build: int
    build_version: int
    build_type: int
    images: Dict[int, ImageItem]
    sounds: List[SoundItem]
    fonts: List[FontItem]
    global_values: List[ValueItem]
    global_strings: List[ValueItem]
    frames: List[Frame]
    file_data: bytes

    def image_png(self, handle: int) -> Optional[bytes]:
        img = self.images.get(handle)
        return img.png if img else None

    def report(self) -> dict:
        return {
            "format": "Clickteam Fusion MFA",
            "name": self.name,
            "author": self.author,
            "version": self.version,
            "window": [self.window_x, self.window_y],
            "frame_rate": self.frame_rate,
            "images": {str(k): v.as_dict() for k, v in self.images.items()},
            "sounds": [
                {"handle": s.handle, "name": s.name, "size": len(s.data)} for s in self.sounds
            ],
            "global_values": [v.as_dict() for v in self.global_values],
            "global_strings": [v.as_dict() for v in self.global_strings],
            "frames": [f.as_dict() for f in self.frames],
        }


# --------------------------------------------------------------------------
# low-level readers
# --------------------------------------------------------------------------

def _skip_chunklist(r: Reader) -> None:
    while r.remaining() >= 5:
        cid = r.u8()
        if cid == 0:
            break
        size = r.u32()
        r.read(size)
        if size == 0:
            break


def _read_value_list(r: Reader) -> List[ValueItem]:
    count = r.i32()
    out = []
    for _ in range(max(count, 0)):
        name = r.autounicode()
        typ = r.i32()
        if typ == 2:
            value = r.autounicode()
        elif typ == 1:
            value = r.f32()
        else:
            value = r.i32()
        out.append(ValueItem(name, value, typ))
    return out


def _read_font_bank(r: Reader) -> List[FontItem]:
    count = r.i32()
    out = []
    for _ in range(max(count, 0)):
        handle = r.u32()
        decomp_size = r.i32()
        comp_size = r.i32()
        if comp_size > 0 and comp_size < r.remaining():
            data = zlib.decompress(r.read(comp_size), -15) if comp_size else b""
        else:
            data = r.read(max(decomp_size, 0))
        out.append(FontItem(handle, data))
    return out


def _read_sound_bank(r: Reader) -> List[SoundItem]:
    count = r.i32()
    out = []
    for _ in range(max(count, 0)):
        handle = r.u32()
        checksum = r.i32()
        references = r.u32()
        decomp_size = r.i32()
        flags = r.u32()
        reserved = r.i32()
        name_len = r.i32()
        data = r.read(max(decomp_size, 0))
        nr = Reader(data)
        try:
            name = nr.wide(max(name_len, 0))
        except Exception:
            name = ""
        audio = nr.read(-1) if nr.remaining() > 0 else b""
        if not audio:
            audio = data
        out.append(SoundItem(handle, checksum, references, flags, name.strip(), audio, decomp_size))
    return out


def _read_music_bank(r: Reader) -> int:
    # CTFAK writes a single zero for MFA music bank.
    return r.i32()


def _read_image_bank(r: Reader) -> Dict[int, ImageItem]:
    graphic_mode = r.i32()
    palette_version = r.i16()
    palette_entries = r.i16()
    for _ in range(256):
        r.color()
    count = r.i32()
    images: Dict[int, ImageItem] = {}
    for _ in range(max(count, 0)):
        item = _read_image_item(r)
        images[item.handle] = item
    return images


def _read_image_item(r: Reader) -> ImageItem:
    handle = r.i32()
    checksum = r.i32()
    references = r.i32()
    size = r.i32()
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
    body = b""
    if flags & 0x08:  # LZX compressed
        decompressed_size = r.u32()
        comp = r.read(size - 4) if size >= 4 else b""
        body = _lzx_decompress(comp, decompressed_size)
    else:
        body = r.read(max(size, 0))
    png = None
    if body:
        # The LZX bit means the pixels came from an LZ4 block; the decoder just
        # produced the plain pixel/alpha stream, so clear it before decoding.
        png = decode_bmp(width, height, gmode, flags & ~0x08, body, transparent)
    return ImageItem(
        handle, checksum, references, size, width, height, gmode, flags, hx, hy, ax, ay, transparent, png, body
    )


def _lzx_decompress(data: bytes, expected: int) -> bytes:
    """Decompress a Clickteam image block (Fusion marks it as 'LZX').

    Fusion 2.5 MFA files use a standard zlib stream here (the 0x78 0x9c header
    is what saves the PNG-in-MFA images). A small LZ4 fallback is kept for
    export files created by CTFAK 2.5+.
    """
    try:
        out = zlib.decompress(data)
        return out[:expected]
    except Exception:
        pass
    try:
        import lz4.block  # type: ignore

        return lz4.block.decompress(data, uncompressed_size=expected)
    except Exception:
        pass

    # Pure-python LZ4 block fallback.
    out = bytearray()
    i = 0
    n = len(data)
    try:
        while i < n:
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
                return bytes(out)
            out.extend(data[i : i + lit_len])
            i += lit_len
            if i >= n:
                break
            if i + 2 > n:
                break
            offset = data[i] | (data[i + 1] << 8)
            i += 2
            match_len = (token & 0x0F) + 4
            if (token & 0x0F) == 15:
                while i < n:
                    extra = data[i]
                    i += 1
                    match_len += extra
                    if extra != 255:
                        break
            if offset <= 0 or offset > len(out):
                return bytes(out)
            start = len(out) - offset
            for k in range(match_len):
                out.append(out[start + k])
            if len(out) >= expected:
                break
    except Exception:
        pass
    return bytes(out[:expected])


def _read_transition(r: Reader) -> Optional[dict]:
    if r.u8() != 1:
        return None
    module = r.autounicode()
    name = r.autounicode()
    ident = r.ascii(4)
    trans_id = r.ascii(4)
    duration = r.i32()
    flags = r.i32()
    color = r.color()
    psize = r.i32()
    params = r.read(max(psize, 0))
    return {"module": module, "name": name, "id": ident, "transition": trans_id,
            "duration": duration, "flags": flags, "color": color, "params": params}


def _read_object_loader(r: Reader, object_type: int) -> ObjectData:
    if object_type >= 32:
        # Extension objects share the AnimationObject layout.
        obj = _read_animation_object(r)
        obj.object_type = object_type
        # extension header (best effort)
        try:
            ext_type = r.i32()
            if ext_type == -1:
                r.autounicode()
                r.autounicode()
                r.u32()
                r.autounicode()
            block_size = r.u32()
            r.read(max(block_size, 0))
        except Exception:
            pass
        return obj
    if object_type == 0:
        obj = _read_object_loader_base(r)
        obj.object_type = object_type
        r.i32(); r.i32()  # width/height
        r.i32()  # shape
        r.i32()  # border size
        r.color()
        r.i32()  # fill type
        r.color()
        r.color()
        r.i32()
        obj.image_handle = r.i32()
        obj.width = obj.extra.get("", 0)
        return obj
    if object_type == 1:
        obj = _read_object_loader_base(r)
        obj.object_type = object_type
        r.u32(); r.u32()  # obstacle/collision
        obj.image_handle = r.i32()
        return obj
    if object_type == 2:
        obj = _read_animation_object(r)
        obj.object_type = object_type
        return obj
    if object_type in (5, 6, 7):
        obj = _read_object_loader_base(r)
        obj.object_type = object_type
        try:
            obj.extra["value"] = r.i32()
            obj.extra["minimum"] = r.i32()
            obj.extra["maximum"] = r.i32()
            r.u32()
            r.u32()
            r.color()
            r.color()
            r.u32()
            r.i32()
            obj.width = r.i32()
            obj.height = r.i32()
            obj.images = [r.i32() for _ in range(max(r.i32(), 0))]
            r.u32()
        except Exception:
            pass
        return obj
    if object_type == 3:
        obj = _read_object_loader_base(r)
        obj.object_type = object_type
        try:
            obj.width = r.u32()
            obj.height = r.u32()
            r.u32()
            r.color()
            r.u32()
            r.u32()
            count = r.u32()
            for _ in range(count):
                r.autounicode()
                r.u32()
        except Exception:
            pass
        return obj
    raise NotImplementedError(f"Unsupported object type {object_type}")


def _read_object_loader_base(r: Reader) -> ObjectData:
    flags = r.i32()
    new_flags = r.i32()
    background = r.color()
    r.read(9 * 2)  # qualifiers + end
    values = _read_value_list(r)
    strings = _read_value_list(r)
    movements = _read_movements(r)
    behaviours_count = r.i32()
    for _ in range(max(behaviours_count, 0)):
        r.autounicode()
        n = r.u32()
        r.read(n)
    r.read(2)  # transitions
    return ObjectData(-1, -1, "", flags, values, strings, movements)


def _read_movements(r: Reader) -> List[Movement]:
    count = r.u32()
    out = []
    for _ in range(max(count, 0)):
        name = r.autounicode()
        ext = r.autounicode()
        ident = r.u32()
        data_size = r.u32()
        if ext:
            out.append(Movement(name, ext, ident, 0, 0, 0, 0, r.read(max(data_size, 0))))
            continue
        player = r.u16()
        typ = r.u16()
        moving = r.u8()
        r.skip(3)
        direction = r.i32()
        rest = max(data_size - 12, 0)
        data = r.read(rest)
        out.append(Movement(name, ext, ident, player, typ, moving, direction, data))
    return out


def _read_animation_object(r: Reader) -> ObjectData:
    obj = _read_object_loader_base(r)
    try:
        if r.u8() != 0:
            anim_count = r.u32()
            for _ in range(max(anim_count, 0)):
                name = r.autounicode()
                dir_count = r.i32()
                anim = Animation(name)
                for _ in range(max(dir_count, 0)):
                    d = AnimationDirection(r.i32(), r.i32(), r.i32(), r.i32(), r.i32())
                    n = r.i32()
                    d.frames = [r.i32() for _ in range(max(n, 0))]
                    anim.directions.append(d)
                obj.animations.append(anim)
    except Exception:
        pass
    return obj


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

EVENT_PARAM_NAMES = {
    1: "object", 2: "time", 3: "short", 4: "short", 5: "int", 6: "sample",
    7: "sample", 9: "create", 10: "short", 11: "short", 12: "short", 13: "every",
    14: "key", 15: "expression", 16: "position", 17: "short", 18: "shoot",
    19: "zone", 21: "create", 22: "expression", 23: "expression", 24: "colour",
    25: "int", 26: "short", 27: "expression", 28: "expression", 29: "int",
    31: "short", 32: "click", 33: "program", 34: "int", 35: "sample",
    36: "sample", 38: "group", 39: "grouppointer", 40: "filename", 41: "string",
    43: "short", 44: "key", 45: "expression", 46: "expression", 47: "twoshorts",
    48: "int", 49: "globalvalue", 50: "alterable", 51: "twoshorts", 52: "expression",
    53: "expression", 54: "expression", 55: "extension", 56: "int", 57: "short",
    58: "short", 59: "expression", 60: "short", 61: "short", 62: "expression",
    64: "string", 68: "alterable",
}


def read_expression(r: Reader) -> Optional[Expression]:
    start = r.tell()
    if r.remaining() < 4:
        return None
    obj_type = r.i16()
    num = r.i16()
    if obj_type == 0 and num == 0:
        return None
    if r.remaining() < 2:
        return None
    size = r.i16()
    payload_start = r.tell()
    value: Any = None
    # decode simple payloads
    if size - 6 >= 4 and obj_type == -1:
        if num == 0:
            value = r.i32()
        elif num == 3:
            value = r.ascii()
        elif num == 23:
            value = r.f64()
            r.f32()
        elif num in (24, 50):
            r.i32()
            value = r.i32()
        else:
            r.read(max(size - 6, 0))
    elif obj_type >= 2 or obj_type == -7:
        r.i16()  # object info
        r.i16()  # object info list
        if num in (16, 19):
            value = r.i16()
        else:
            r.i32()
        r.read(max(size - 6 - (6 if num in (16, 19) else 8), 0))
    else:
        r.read(max(size - 6, 0))
    r.seek(start + size)
    return Expression(obj_type, num, size, value)


def _decode_parameter_payload(code: int, payload: bytes) -> Any:
    if not payload:
        return None
    p = Reader(payload)
    try:
        if code in (3, 4, 10, 11, 12, 17, 26, 31, 43, 57, 58, 60, 61, 47, 51):
            return {"short": p.i16()}
        if code in (5, 25, 29, 34, 48, 56):
            return {"int": p.i32()}
        if code in (41, 64):
            return {"string": p.ascii()}
        if code in (14, 44):
            return {"keycode": p.i16()}
        if code in (15, 22, 23, 27, 28, 45, 46, 52, 53, 54, 59, 62):
            vals = []
            while p.remaining() >= 6:
                expr = read_expression(p)
                if expr is None:
                    break
                vals.append(expr.as_dict())
            return {"expressions": vals}
        if code in (6, 7, 35, 36):
            return {"sample": p.i16()}
        if code == 24:
            return {"colour": p.color()}
        if code == 49:
            r2 = Reader(payload)
            r2.i32()
            return {"global": r2.i32()}
        if code == 13:
            return {"every": p.i32()}
        if code == 32:
            return {"click": p.i16()}
        if code in (1, 9, 21, 16, 18, 19, 40, 50, 55):
            # keep raw, decoding is uncertain on real projects
            return {"raw_len": len(payload)}
        # generic fallback: return as numbers when possible
        if len(payload) in (2, 4, 8):
            vals = list(struct.unpack("<" + "H" * (len(payload) // 2), payload))
            return {"values": vals}
        return {"raw_len": len(payload)}
    except Exception:
        return {"raw_len": len(payload)}


def _read_condition(r: Reader) -> Optional[Condition]:
    start = r.tell()
    if r.remaining() < 12:
        return None
    size = r.u16()
    obj_type = r.i16()
    num = r.i16()
    object_info = r.u16()
    object_info_list = r.i16()
    flags = r.i8()
    other_flags = r.i8()
    num_params = r.u8()
    def_type = r.u8()
    identifier = r.i16()
    params = []
    for _ in range(num_params):
        if r.remaining() < 2:
            break
        p_start = r.tell()
        p_size = r.i16()
        code = r.i16()
        payload_len = max(p_size - 2, 0)
        payload = r.read(payload_len)
        r.seek(p_start + p_size)
        params.append(Parameter(code, p_size, _decode_parameter_payload(code, payload), payload))
    r.seek(start + size)
    return Condition(obj_type, num, object_info, object_info_list, flags, other_flags,
                     num_params, def_type, identifier, size, params)


def _read_action(r: Reader) -> Optional[Action]:
    start = r.tell()
    if r.remaining() < 12:
        return None
    size = r.u16()
    obj_type = r.i16()
    num = r.i16()
    object_info = r.u16()
    object_info_list = r.i16()
    flags = r.i8()
    other_flags = r.i8()
    num_params = r.u8()
    def_type = r.u8()
    params = []
    for _ in range(num_params):
        if r.remaining() < 2:
            break
        p_start = r.tell()
        p_size = r.i16()
        code = r.i16()
        payload_len = max(p_size - 2, 0)
        payload = r.read(payload_len)
        r.seek(p_start + p_size)
        params.append(Parameter(code, p_size, _decode_parameter_payload(code, payload), payload))
    r.seek(start + size)
    return Action(obj_type, num, object_info, object_info_list, flags, other_flags,
                  num_params, def_type, size, params)


def _read_event_group(r: Reader) -> Optional[EventGroup]:
    start = r.tell()
    if r.remaining() < 12:
        return None
    size = -r.i16()
    num_cond = r.u8()
    num_act = r.u8()
    flags = r.u16()
    is_restricted = r.i16()
    restrict_cpt = r.i16()
    identifier = r.i16()
    undo = r.i16()
    conds = []
    for _ in range(num_cond):
        c = _read_condition(r)
        if c is not None:
            conds.append(c)
    acts = []
    for _ in range(num_act):
        a = _read_action(r)
        if a is not None:
            acts.append(a)
    r.seek(start + size)
    return EventGroup(size, num_cond, num_act, flags, is_restricted, restrict_cpt,
                      identifier, undo, conds, acts)


def _read_frame_events(r: Reader) -> List[EventGroup]:
    groups: List[EventGroup] = []
    version = r.u16()
    frame_type = r.u16()
    guard = 0
    while r.remaining() >= 4 and guard < 100000:
        guard += 1
        name = r.ascii(4)
        if name == "Evts":
            length = r.u32()
            end = r.tell() + length
            while r.tell() < end and r.remaining() > 0:
                g = _read_event_group(r)
                if g is None:
                    break
                groups.append(g)
        elif name == "Rems":
            length = r.u32()
            try:
                r.read(length)
            except Exception:
                break
        elif name == "EvOb":
            count = r.u32()
            for _ in range(count):
                # EventObject: handle, object type, item type, names, flags...
                try:
                    r.u32()
                    otype = r.u16()
                    r.u16()
                    r.autounicode()
                    r.autounicode()
                    r.u16()
                    if otype == 1:
                        r.u32(); r.u32()
                    elif otype == 2:
                        code = r.ascii(4)
                        if code == "OIC2":
                            r.autounicode()
                    elif otype == 3:
                        r.u16()
                except Exception:
                    break
        elif name == "EvCs":
            try:
                r.i32(); r.u16(); r.u16(); r.read(12)
            except Exception:
                break
        elif name == "EvEd":
            try:
                short_count = r.i16()
                real = r.u16() if short_count == -1 else short_count
                r.read(real * 6)
                if short_count == -1:
                    n = r.u16()
                    for _ in range(n):
                        r.autounicode()
            except Exception:
                break
        elif name in ("EvTs", "EvLs"):
            try:
                if r.u16() != 1:
                    pass
                for _ in range(5):
                    r.u32()
            except Exception:
                break
        elif name == "E2Ts":
            r.read(12)
        elif name == "!DNE":
            break
        else:
            log.debug("Unknown event chunk %r", name)
            break
    return groups


# --------------------------------------------------------------------------
# frame reading
# --------------------------------------------------------------------------

def _read_frame(r: Reader) -> Frame:
    handle = r.i32()
    name = r.autounicode()
    size_x = r.i32()
    size_y = r.i32()
    background = r.color()
    flags = r.u32()
    max_objects = r.i32()
    password = r.autounicode()
    unk = r.autounicode()
    last_x = r.i32()
    last_y = r.i32()
    palette_size = r.i32()
    for _ in range(256):
        r.color()
    r.i32()  # stamp handle
    active_layer = r.i32()
    layers = []
    layer_count = r.i32()
    for _ in range(max(layer_count, 0)):
        lay_name = r.autounicode()
        lay_flags = r.u32()
        xc = r.f32()
        yc = r.f32()
        layers.append(Layer(lay_name, lay_flags, xc, yc))
    _read_transition(r)
    _read_transition(r)
    frame = Frame(handle, name, size_x, size_y, background, flags, max_objects, password, layers)
    item_count = r.i32()
    items = []
    for _ in range(max(item_count, 0)):
        try:
            obj = _read_frame_item(r)
            items.append(obj)
        except Exception as e:
            log.debug("frame item failed: %s", e)
            break
    frame.items = items
    folder_count = r.i32()
    for _ in range(max(folder_count, 0)):
        hdr = r.u32()
        if hdr == 0x70000004:
            r.autounicode()
            n = r.u32()
            r.read(n * 4)
        else:
            r.u32()
    instance_count = r.i32()
    for _ in range(max(instance_count, 0)):
        x = r.i32()
        y = r.i32()
        layer = r.u32()
        h = r.i32()
        fl = r.u32()
        pt = r.u32()
        ih = r.u32()
        ph = r.i32()
        frame.instances.append(FrameInstance(x, y, layer, h, fl, pt, ih, ph))
    frame.event_groups = _read_frame_events(r)
    _skip_chunklist(r)
    return frame


def _read_frame_item(r: Reader) -> ObjectData:
    obj_type = r.i32()
    handle = r.i32()
    name = r.autounicode()
    transparent = r.i32()
    ink = r.i32()
    ink_param = r.u32()
    aa = r.i32()
    flags = r.i32()
    icon_type = r.i32()
    icon_handle = r.i32()
    _skip_chunklist(r)
    obj = _read_object_loader(r, obj_type)
    obj.object_type = obj_type
    obj.handle = handle
    obj.name = name
    return obj


# --------------------------------------------------------------------------
# top-level MFA
# --------------------------------------------------------------------------

def _read_mfa(r: Reader) -> MFA:
    magic = r.ascii(4)
    if magic != "MFU2":
        raise ValueError(f"Not a Fusion MFA (found {magic!r})")
    mfa_build = r.i32()
    product = r.i32()
    build_version = r.i32()
    lang_id = r.i32()
    name = r.autounicode()
    description = r.autounicode()
    path = r.autounicode()
    stamp_len = r.i32()
    stamp = r.read(max(stamp_len, 0))
    r.ascii(4)  # ATNF
    fonts = _read_font_bank(r)
    r.ascii(4)  # APMS
    sounds = _read_sound_bank(r)
    r.ascii(4)  # ASUM
    _read_music_bank(r)
    r.ascii(4)  # AGMI icons
    _read_image_bank(r)
    r.ascii(4)  # AGMI images
    images = _read_image_bank(r)
    r.autounicode()  # name again
    author = r.autounicode()
    r.autounicode()  # description again
    copyright = r.autounicode()
    company = r.autounicode()
    version = r.autounicode()
    window_x = r.i32()
    window_y = r.i32()
    r.color()  # border color
    r.u32()  # display flags
    r.u32()  # graphic flags
    r.autounicode()  # help file
    r.autounicode()  # unknown string
    r.i32()  # initial score
    r.i32()  # initial lives
    frame_rate = r.i32()
    build_type = r.i32()
    r.autounicode()  # build path
    r.autounicode()  # unknown string 2
    r.autounicode()  # command line
    r.autounicode()  # about box
    r.u32()
    binary_count = r.i32()
    for _ in range(max(binary_count, 0)):
        n = r.i32()
        r.read(max(n, 0))
    # controls
    control_count = r.i32()
    for _ in range(max(control_count, 0)):
        r.i32()
        n = r.i32()
        r.read(max(n, 0) * 4)
    menu_size = r.u32()
    r.read(menu_size)
    r.i32()  # window menu index
    menu_img_count = r.i32()
    for _ in range(max(menu_img_count, 0)):
        r.i32()
        r.i32()
    global_values = _read_value_list(r)
    global_strings = _read_value_list(r)
    ge_len = r.i32()
    r.read(max(ge_len, 0))
    r.i32()  # graphic mode
    icon_count = r.i32()
    for _ in range(max(icon_count, 0)):
        r.i32()
    qual_count = r.i32()
    for _ in range(max(qual_count, 0)):
        n = r.i32()
        r.ascii(n)
        r.i32()
    ext_count = r.i32()
    for _ in range(max(ext_count, 0)):
        r.i32()
        r.autounicode()
        r.autounicode()
        r.i32()
        slen = r.i32()
        r.wide(slen)
    frame_offset_count = r.i32()
    offsets = [r.i32() for _ in range(max(frame_offset_count, 0))]
    next_offset = r.i32()
    frames = []
    for off in offsets:
        r.seek(off)
        frames.append(_read_frame(r))
    r.seek(next_offset)
    _skip_chunklist(r)
    return MFA(name, description, path, author, copyright, company, version,
               window_x, window_y, frame_rate, mfa_build, build_version,
               build_type, images, sounds, fonts, global_values, global_strings,
               frames, b"")


def load_mfa(path: str) -> MFA:
    with open(path, "rb") as fh:
        data = fh.read()
    r = Reader(data)
    mfa = _read_mfa(r)
    mfa.file_data = data
    return mfa


def load_mfa_bytes(data: bytes) -> MFA:
    r = Reader(data)
    mfa = _read_mfa(r)
    mfa.file_data = data
    return mfa
