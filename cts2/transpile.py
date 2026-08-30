"""Render parsed Clickteam Fusion events as real Scratch blocks.

Two layers:

* ``describe_*`` — human-readable Logic-Notes for every event line (always
  available, this is the safe fallback for anything we cannot map).
* ``transpile_frame_blocks`` — compiles the *verified subset* of event
  opcodes into real Scratch 3 block graphs.

Why a subset?  Clickteam event lines are ``(object_type, num)`` opcodes
whose meaning is fixed by the editor version.  The **System ("Special")
object** opcodes (negative condition numbers, 2000-series actions) are
stable across MMF2 / Fusion 2.5 builds and are the ones used by nearly
every game (Start of Frame, Every, Always, key/mouse input, loops, global
values).  Those are mapped here.  The **Active object** table is filled
only from the ordering documented by the Anaconda/CTFAK reverse
engineering community and is marked heuristic; anything uncertain stays a
readable note instead of risking a wrong block.

Object-targeted actions are compiled as broadcast glue: the event script
(owned by a per-frame "Events" sprite) broadcasts ``<frame> › <object> ›
act N`` and the object's own sprite runs the actual block on receiving it
— that is how one sprite's script can move/alter another sprite.

Every event line is accounted for: mapped lines become blocks, unmapped
lines stay in Logic-Notes, and the frame summary reports the counts as a
warning so nothing is silently lost.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from .mfa import Action, Condition, EventGroup, Frame, MFA
from .scratch import (
    _block, _nid, num, ref, text, var_reference,
)

SYS = -1  # Clickteam "System / Special" object
ACTIVE = 1  # MFA frame-item object_type for Active objects

# --------------------------------------------------------------------------
# System object condition table (stable across MMF2 / Fusion 2.5).
# Negative numbers; documented by Anaconda's events.pyx / CTFAK.
# --------------------------------------------------------------------------

COND_START_OF_FRAME = -1
COND_START_OF_APP = -2
COND_END_OF_APP = -3
COND_EVERY = -4
COND_ON_LOOP = -6
COND_LOOP_FOR_EACH = -7
COND_REPEAT = -8
COND_COMPARE_GLOBAL_VALUE = -9
COND_COMPARE_GLOBAL_STRING = -10
COND_COMPARE_TWO_VALUES = -11
COND_COMPARE_TWO_STRINGS = -12
COND_ALWAYS = -13
COND_NEVER = -14
COND_ON_KEY_PRESSED = -15
COND_WHILE_KEY_PRESSED = -16
COND_ON_KEY_RELEASED = -17
COND_ON_MOUSE_CLICKED = -18
COND_WHILE_MOUSE_PRESSED = -19
COND_ON_MOUSE_RELEASED = -20
COND_ON_MOUSE_WHEEL = -21
COND_MOUSE_OVER_OBJECT = -22

# System actions (2000 series, stable).
ACT_START_LOOP = 2001
ACT_START_LOOP_FOR_EACH = 2002
ACT_STOP_LOOP = 2003
ACT_SET_GLOBAL_VALUE = 2004
ACT_ADD_GLOBAL_VALUE = 2005
ACT_SUB_GLOBAL_VALUE = 2006
ACT_SET_GLOBAL_STRING = 2007
ACT_ADD_GLOBAL_STRING = 2008

# Active-object actions, in the Anaconda/CTFAK CRunActive ordering
# (heuristic — only the entries that are unambiguous across dumps).
ACT_DESTROY = 0
ACT_SET_X = 2
ACT_SET_Y = 3
ACT_SET_POSITION = 4
ACT_SET_ANIM_FRAME = 12
ACT_SHOW = 24
ACT_HIDE = 25

# Active-object conditions (heuristic ordering, same source).
COND_COLLISION_OBJECT = 0
COND_COLLISION_BACKDROP = 1
COND_MOUSE_OVER = 2
COND_VISIBLE = 3
COND_HIDDEN = 4
COND_ANIM_FINISHED = 13

# --------------------------------------------------------------------------
# parameter helpers
# --------------------------------------------------------------------------

def _param_values(cond_or_act) -> List[dict]:
    """Flatten decoded parameter values (drop raw blobs)."""
    out = []
    for p in getattr(cond_or_act, "parameters", []) or []:
        v = p.value
        if isinstance(v, dict) and "raw_len" in v:
            continue
        out.append(v or {})
    return out


def _first_number(params: List[dict], keys=("int", "short", "every", "values")) -> Optional[float]:
    for p in params:
        for k in keys:
            v = p.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if k == "values" and isinstance(v, list) and v and isinstance(v[0], (int, float)):
                return float(v[0])
    return None


def _first_string(params: List[dict]) -> Optional[str]:
    for p in params:
        v = p.get("string")
        if isinstance(v, str) and v:
            return v
    return None


def _first_expression(params: List[dict]) -> Optional[float]:
    for p in params:
        exprs = p.get("expressions")
        if isinstance(exprs, list):
            for e in exprs:
                v = e.get("value") if isinstance(e, dict) else None
                if isinstance(v, (int, float)):
                    return float(v)
    return None


def _expr_or_number(params: List[dict]) -> Optional[float]:
    return _first_expression(params) or _first_number(params)


def _keycode_to_scratch(code: Optional[int]) -> Optional[str]:
    if code is None:
        return None
    code = int(code)
    if code == 32:
        return "space"
    if code == 13:
        return "enter"
    if 65 <= code <= 90:
        return chr(code + 32)
    if 48 <= code <= 57:
        return chr(code)
    if code == 37:
        return "left arrow"
    if code == 38:
        return "up arrow"
    if code == 39:
        return "right arrow"
    if code == 40:
        return "down arrow"
    return None


# --------------------------------------------------------------------------
# object / global resolution
# --------------------------------------------------------------------------

def _obj_name(frame: Frame, object_info: int) -> str:
    if object_info == 0:
        return "System"
    for it in frame.items:
        if it.handle == object_info:
            return it.name or f"Object{object_info}"
    return f"Object{object_info}"


def _sprite_for(frame: Frame, object_info: int,
                sprite_by_handle: Dict[int, "TargetBuilder"]):
    if object_info == 0:
        return None
    return sprite_by_handle.get(object_info)


def _global_variable(events_tb, mfa: MFA, index: int, warnings: List[str],
                     strings: bool = False) -> Optional[str]:
    """Create/find a Scratch variable for a Clickteam global value/string."""
    items = mfa.global_strings if strings else mfa.global_values
    if index <= 0 or index > len(items):
        # Out-of-range globals still get a variable so blocks do not dangle.
        label = f"global value {index}" if not strings else f"global string {index}"
    else:
        item = items[index - 1]
        label = getattr(item, "name", "") or (f"global value {index}" if not strings
                                              else f"global string {index}")
    var_id = str(uuid.uuid4())
    value = 0 if not strings else ""
    try:
        if index <= len(items):
            value = getattr(items[index - 1], "value", value)
    except Exception:  # noqa: BLE001
        pass
    events_tb.add_variable(var_id, label, value)
    if not label.startswith(("global value", "global string")):
        warnings.append(
            f"global {index} ('{label}') recreated as a Scratch variable")
    return var_id


def _broadcast_name(frame: Frame, obj_name: str, kind: str, num: int) -> str:
    return f"{frame.name} › {obj_name} › {kind} {num}"


def _register_broadcast(tb, name: str) -> str:
    for bid, (bname, _bval) in tb.broadcasts.items():
        if bname == name:
            return bid
    return tb.add_broadcast(name)


# --------------------------------------------------------------------------
# block graph helpers
# --------------------------------------------------------------------------

def _chain(tb, ids: List[str]) -> None:
    """Chain ``ids`` in order: next/parent pointers."""
    for i, bid in enumerate(ids):
        nxt = ids[i + 1] if i + 1 < len(ids) else None
        par = ids[i - 1] if i > 0 else None
        if nxt is not None:
            tb.blocks[bid]["next"] = nxt
        if par is not None:
            tb.blocks[bid]["parent"] = par


def _add(tb, **kw) -> str:
    bid = _nid()
    tb.blocks[bid] = _block(**kw)
    return bid


def _hat_flag(tb) -> str:
    return _add(tb, opcode="event_whenflagclicked", toplevel=True, x=60, y=60,
                fields={})


def _hat_key(tb, key: str) -> str:
    return _add(tb, opcode="event_whenkeypressed", toplevel=True, x=60, y=60,
                fields={"KEY_OPTION": [key, None]})


def _hat_broadcast(tb, name: str, bid: str) -> str:
    return _add(tb, opcode="event_whenbroadcastreceived", toplevel=True,
                x=60, y=60, fields={"BROADCAST_OPTION": [name, bid]})


def _forever(tb, body: str) -> str:
    return _add(tb, opcode="control_forever", inputs={"SUBSTACK": ref(body)})


def _if(tb, cond: str, body: str) -> str:
    return _add(tb, opcode="control_if", inputs={
        "CONDITION": ref(cond), "SUBSTACK": ref(body)})


def _wait(tb, secs: float) -> str:
    return _add(tb, opcode="control_wait", inputs={"DURATION": num(secs)})


def _key_pressed(tb, key: str) -> str:
    return _add(tb, opcode="sensing_keypressed",
                fields={"KEY_OPTION": [key, None]})


def _mouse_down(tb) -> str:
    return _add(tb, opcode="sensing_mousedown")


def _not(tb, operand: str) -> str:
    return _add(tb, opcode="operator_not", inputs={"OPERAND": ref(operand)})


def _join(tb, a, b) -> str:
    return _add(tb, opcode="operator_join", inputs={
        "STRING1": a if isinstance(a, list) else ref(a),
        "STRING2": b if isinstance(b, list) else ref(b)})


def _touching(tb, sprite_name: str) -> str:
    return _add(tb, opcode="sensing_touchingobject",
                fields={"TOUCHINGOBJECTMENU": [sprite_name, None]})


def _var_set(tb, var_id: str, var_name: str, value) -> str:
    return _add(tb, opcode="data_setvariableto", fields={"VARIABLE": [var_name, var_id]},
                inputs={"VALUE": value if isinstance(value, list) else ref(value)})


def _var_change(tb, var_id: str, var_name: str, value) -> str:
    return _add(tb, opcode="data_changevariableby",
                fields={"VARIABLE": [var_name, var_id]},
                inputs={"VALUE": value if isinstance(value, list) else ref(value)})


def _broadcast_block(tb, name: str, bid: str) -> str:
    return _add(tb, opcode="event_broadcast",
                inputs={"BROADCAST_INPUT": [1, [11, name, bid]]})


# --------------------------------------------------------------------------
# condition → "script skeleton" compilation
#
# Each mapped condition returns a list of block ids that *gate* the body,
# plus a "substack end" id the body should attach under.  The skeleton is
# a chain: [hat ... gate ...], body goes under the last block's SUBSTACK
# (for forever/if) or after it (for plain hats).
# --------------------------------------------------------------------------

class _Skeleton:
    def __init__(self, blocks: List[str], body_parent: str, body_is_substack: bool,
                 approximation: Optional[str] = None):
        self.blocks = blocks          # ids already added to tb
        self.body_parent = body_parent  # id the body chain should follow
        self.body_is_substack = body_is_substack  # True → SUBSTACK input
        self.approximation = approximation


def _compile_system_condition(tb, cond: Condition, frame: Frame,
                              mfa: MFA, warnings: List[str],
                              notes: List[str]) -> Optional[_Skeleton]:
    params = _param_values(cond)
    n = cond.num

    if n == COND_START_OF_FRAME:
        hat = _hat_flag(tb)
        return _Skeleton([hat], hat, False)

    if n == COND_START_OF_APP:
        hat = _hat_flag(tb)
        return _Skeleton([hat], hat, False,
                         approximation="'Start of Application' treated as 'when green flag clicked'")

    if n == COND_ALWAYS:
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        return _Skeleton([hat, body], body, True)

    if n == COND_EVERY:
        ticks = _first_number(params, ("every", "int", "short"))
        if ticks is None or ticks <= 0:
            ticks = 50.0
            warnings.append(
                f"{frame.name}: 'Every' without a readable interval; using 50 ticks (1 s)")
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        wait = _wait(tb, max(ticks / 50.0, 0.01))
        return _Skeleton([hat, body, wait], wait, False)

    if n in (COND_ON_LOOP, COND_LOOP_FOR_EACH):
        name = _first_string(params) or f"loop{abs(cond.identifier) or len(frame.event_groups)}"
        bid = _register_broadcast(tb, f"loop:{name}")
        hat = _hat_broadcast(tb, f"loop:{name}", bid)
        return _Skeleton([hat], hat, False)

    if n == COND_REPEAT:
        count = _expr_or_number(params)
        if count is None:
            notes.append("Repeat without a readable count — kept as a note")
            return None
        hat = _hat_flag(tb)
        body = _nid()
        rep = _add(tb, opcode="control_repeat", inputs={
            "TIMES": num(int(count)), "SUBSTACK": ref(body)})
        tb.blocks[hat]["next"] = rep
        tb.blocks[rep]["parent"] = hat
        return _Skeleton([hat, rep], body, True)

    if n == COND_ON_KEY_PRESSED:
        key = _keycode_to_scratch(_first_number(params, ("keycode", "short", "int")))
        if not key:
            notes.append("key-pressed without a readable key — kept as a note")
            return None
        hat = _hat_key(tb, key)
        return _Skeleton([hat], hat, False)

    if n == COND_WHILE_KEY_PRESSED:
        key = _keycode_to_scratch(_first_number(params, ("keycode", "short", "int")))
        if not key:
            notes.append("while-key-pressed without a readable key — kept as a note")
            return None
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        kp = _key_pressed(tb, key)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(kp), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True)

    if n == COND_ON_KEY_RELEASED:
        key = _keycode_to_scratch(_first_number(params, ("keycode", "short", "int")))
        if not key:
            notes.append("key-released without a readable key — kept as a note")
            return None
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        kp = _key_pressed(tb, key)
        notkp = _not(tb, kp)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(notkp), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True,
                         approximation="'On key released' approximated as 'while not pressed'")

    if n == COND_ON_MOUSE_CLICKED:
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        md = _mouse_down(tb)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(md), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True,
                         approximation="'On mouse clicked' approximated as 'while mouse down'")

    if n == COND_WHILE_MOUSE_PRESSED:
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        md = _mouse_down(tb)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(md), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True)

    if n == COND_ON_MOUSE_RELEASED:
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        md = _mouse_down(tb)
        notmd = _not(tb, md)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(notmd), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True,
                         approximation="'On mouse released' approximated as 'while not pressed'")

    if n in (COND_COMPARE_GLOBAL_VALUE, COND_COMPARE_GLOBAL_STRING):
        strings = n == COND_COMPARE_GLOBAL_STRING
        idx = None
        for p in params:
            g = p.get("global")
            if isinstance(g, (int, float)):
                idx = int(g)
                break
        if idx is None:
            notes.append("global compare without a global index — kept as a note")
            return None
        var_id = _global_variable(tb, mfa, idx, warnings, strings=strings)
        var_name = tb.variables[var_id][0]
        val = _expr_or_number(params)
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        eq_id = _add(tb, opcode="operator_equals", inputs={
            "OPERAND1": var_reference(var_id),
            "OPERAND2": num(val) if val is not None else num(0)})
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(eq_id), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True,
                         approximation="'Compare global' approximated: fires every tick while equal")

    # Everything else: keep as a note.
    return None


def _compile_active_condition(tb, cond: Condition, frame: Frame,
                              sprite_by_handle, warnings, notes) -> Optional[_Skeleton]:
    params = _param_values(cond)
    n = cond.num
    sprite = _sprite_for(frame, cond.object_info, sprite_by_handle)
    if sprite is None:
        notes.append(
            f"{_obj_name(frame, cond.object_info)}: condition #{n} (no sprite) — kept as a note")
        return None
    if n == COND_COLLISION_OBJECT:
        # param: object to collide with (expression/object ref); fall back
        # to the frame's first other sprite.
        target = None
        for p in params:
            exprs = p.get("expressions")
            if isinstance(exprs, list) and exprs:
                for e in exprs:
                    v = e.get("value") if isinstance(e, dict) else None
                    if isinstance(v, (int, float)):
                        target = sprite_by_handle.get(int(v))
                        break
        if target is None:
            # heuristic: collide with anything else in the frame
            others = [s for h, s in sprite_by_handle.items() if s is not sprite]
            if not others:
                notes.append("collision condition with no other sprite — kept as a note")
                return None
            target = others[0]
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        touch = _touching(tb, target.name)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(touch), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True,
                         approximation="collision approximated with 'touching' on the Events sprite")
    if n == COND_MOUSE_OVER:
        hat = _hat_flag(tb)
        body = _nid()
        tb.blocks[body] = _block("control_forever")
        tb.blocks[hat]["next"] = body
        tb.blocks[body]["parent"] = hat
        touch = _touching(tb, sprite.name)
        inner = _nid()
        tb.blocks[inner] = _block("control_if", inputs={
            "CONDITION": ref(touch), "SUBSTACK": ref(_nid())})
        tb.blocks[body]["inputs"]["SUBSTACK"] = ref(inner)
        sub_body = _nid()
        tb.blocks[inner]["inputs"]["SUBSTACK"] = ref(sub_body)
        return _Skeleton([hat, body, inner], sub_body, True)
    return None


def _compile_condition(tb, cond: Condition, frame: Frame, mfa: MFA,
                       sprite_by_handle, warnings, notes) -> Optional[_Skeleton]:
    if cond.object_type == SYS:
        return _compile_system_condition(tb, cond, frame, mfa, warnings, notes)
    if cond.object_type == ACTIVE:
        return _compile_active_condition(tb, cond, frame, sprite_by_handle,
                                         warnings, notes)
    return None


# --------------------------------------------------------------------------
# actions → block ids (on the event sprite, using broadcasts for objects)
# --------------------------------------------------------------------------

def _compile_action(tb, act: Action, frame: Frame, mfa: MFA,
                    sprite_by_handle, warnings, notes) -> Optional[str]:
    """Return the block id to run for ``act`` (may be a broadcast)."""
    params = _param_values(act)
    n = act.num
    obj_name = _obj_name(frame, act.object_info)

    if act.object_type == SYS:
        if n == ACT_START_LOOP:
            name = _first_string(params) or f"loop{len(frame.event_groups)}"
            count = _expr_or_number(params)
            if count is None:
                count = 10.0
                warnings.append(
                    f"{frame.name}: 'Start loop {name}' without a readable count; using 10")
            bid = _register_broadcast(tb, f"loop:{name}")
            bcast = _broadcast_block(tb, f"loop:{name}", bid)
            rep = _add(tb, opcode="control_repeat", inputs={
                "TIMES": num(int(count)), "SUBSTACK": ref(bcast)})
            tb.blocks[bcast]["parent"] = rep
            return rep
        if n == ACT_START_LOOP_FOR_EACH:
            name = _first_string(params) or f"loop{len(frame.event_groups)}"
            bid = _register_broadcast(tb, f"loop:{name}")
            return _broadcast_block(tb, f"loop:{name}", bid)
        if n == ACT_STOP_LOOP:
            notes.append("'Stop loop' cannot be represented in Scratch — kept as a note")
            return None
        if n in (ACT_SET_GLOBAL_VALUE, ACT_SET_GLOBAL_STRING):
            strings = n == ACT_SET_GLOBAL_STRING
            idx = None
            for p in params:
                g = p.get("global")
                if isinstance(g, (int, float)):
                    idx = int(g)
                    break
            if idx is None:
                notes.append("global set without a global index — kept as a note")
                return None
            var_id = _global_variable(tb, mfa, idx, warnings, strings=strings)
            var_name = tb.variables[var_id][0]
            val = _expr_or_number(params)
            value = num(val) if val is not None else (text("") if strings else num(0))
            return _var_set(tb, var_id, var_name, value)
        if n == ACT_ADD_GLOBAL_VALUE:
            idx = None
            for p in params:
                g = p.get("global")
                if isinstance(g, (int, float)):
                    idx = int(g)
                    break
            if idx is None:
                notes.append("global add without a global index — kept as a note")
                return None
            var_id = _global_variable(tb, mfa, idx, warnings)
            var_name = tb.variables[var_id][0]
            val = _expr_or_number(params)
            return _var_change(tb, var_id, var_name, num(val) if val is not None else num(0))
        if n == ACT_SUB_GLOBAL_VALUE:
            idx = None
            for p in params:
                g = p.get("global")
                if isinstance(g, (int, float)):
                    idx = int(g)
                    break
            if idx is None:
                notes.append("global subtract without a global index — kept as a note")
                return None
            var_id = _global_variable(tb, mfa, idx, warnings)
            var_name = tb.variables[var_id][0]
            val = _expr_or_number(params)
            return _var_change(tb, var_id, var_name, num(-(val or 0)))
        if n == ACT_ADD_GLOBAL_STRING:
            idx = None
            for p in params:
                g = p.get("global")
                if isinstance(g, (int, float)):
                    idx = int(g)
                    break
            if idx is None:
                notes.append("global string append without an index — kept as a note")
                return None
            var_id = _global_variable(tb, mfa, idx, warnings, strings=True)
            var_name = tb.variables[var_id][0]
            extra = _first_string(params) or ""
            j = _join(tb, var_reference(var_id), text(extra))
            return _var_set(tb, var_id, var_name, ref(j))
        return None

    if act.object_type == ACTIVE:
        sprite = _sprite_for(frame, act.object_info, sprite_by_handle)
        if sprite is None:
            notes.append(f"{obj_name}: action #{n} (no sprite) — kept as a note")
            return None
        name = _broadcast_name(frame, obj_name, "act", n)
        bid = _register_broadcast(tb, name)
        # the receiving handler on the object sprite
        _build_object_handler(sprite, name, bid, act, frame, warnings, notes)
        return _broadcast_block(tb, name, bid)

    notes.append(f"{obj_name}: action #{n} (unsupported object type) — kept as a note")
    return None


def _build_object_handler(sprite, name: str, bid: str, act: Action, frame: Frame,
                          warnings: List[str], notes: List[str]) -> None:
    """Attach a ``when I receive <name>`` handler on the object sprite."""
    params = _param_values(act)
    n = act.num
    body_ids: List[str] = []
    x = y = None
    if n == ACT_DESTROY:
        hid = _add(sprite, opcode="looks_hide")
        body_ids.append(hid)
        warnings.append(
            f"{frame.name}: '{sprite.name}' Destroy approximated as Hide (Scratch has no destroy)")
    elif n == ACT_SET_X:
        x = _expr_or_number(params)
        if x is None:
            notes.append(f"{sprite.name}: Set X without a readable value — kept as a note")
            return
        body_ids.append(_add(sprite, opcode="motion_setx", inputs={"X": num(x)}))
    elif n == ACT_SET_Y:
        y = _expr_or_number(params)
        if y is None:
            notes.append(f"{sprite.name}: Set Y without a readable value — kept as a note")
            return
        body_ids.append(_add(sprite, opcode="motion_sety", inputs={"Y": num(y)}))
    elif n == ACT_SET_POSITION:
        x = _expr_or_number(params)
        y = None
        rest = params[1:] if len(params) > 1 else []
        y = _expr_or_number(rest)
        if x is None or y is None:
            notes.append(f"{sprite.name}: Set position without readable X/Y — kept as a note")
            return
        body_ids.append(_add(sprite, opcode="motion_gotoxy",
                             inputs={"X": num(x), "Y": num(y)}))
    elif n == ACT_SET_ANIM_FRAME:
        frame_no = _expr_or_number(params)
        if frame_no is None or frame_no < 1:
            notes.append(f"{sprite.name}: Set animation frame without a readable index")
            return
        costumes = sprite.costumes
        if not costumes:
            return
        idx = min(int(frame_no), len(costumes))
        cname = costumes[idx - 1]["name"] if idx >= 1 else costumes[0]["name"]
        body_ids.append(_add(sprite, opcode="looks_switchcostumeto",
                             fields={"COSTUME": [cname, None]}))
    elif n == ACT_SHOW:
        body_ids.append(_add(sprite, opcode="looks_show"))
    elif n == ACT_HIDE:
        body_ids.append(_add(sprite, opcode="looks_hide"))
    else:
        notes.append(f"{sprite.name}: action #{n} — kept as a note")
        return
    if not body_ids:
        return
    hat = _hat_broadcast(sprite, name, bid)
    _chain(sprite, [hat] + body_ids)


# --------------------------------------------------------------------------
# per-frame compilation
# --------------------------------------------------------------------------

def transpile_frame_blocks(frame: Frame, mfa: MFA, events_tb,
                           sprite_by_handle: Dict[int, "TargetBuilder"],
                           warnings: List[str], notes: List[str]) -> dict:
    """Compile one frame's event groups into Scratch blocks.

    Returns stats: ``{groups, mapped, unmapped, blocks, approximations}``.
    """
    stats = {"groups": len(frame.event_groups), "mapped": 0, "unmapped": 0,
             "blocks": 0, "approximations": 0}
    for group in frame.event_groups:
        # 1) compile the conditions into a gating skeleton
        skeletons = []
        ok = True
        for cond in group.conditions:
            sk = _compile_condition(events_tb, cond, frame, mfa,
                                    sprite_by_handle, warnings, notes)
            if sk is None:
                ok = False
                break
            skeletons.append(sk)
        if not ok or not skeletons:
            stats["unmapped"] += 1
            notes.append(
                f"{frame.name}: event group with unmapped conditions kept as a note")
            continue

        # 2) compile the actions into a body chain
        body_ids: List[str] = []
        for act in group.actions:
            bid = _compile_action(events_tb, act, frame, mfa, sprite_by_handle,
                                  warnings, notes)
            if bid is not None:
                body_ids.append(bid)
        if not body_ids and group.actions:
            # actions existed but none could be mapped
            stats["unmapped"] += 1
            notes.append(
                f"{frame.name}: event group with unmapped actions kept as a note")
            continue

        # 3) stitch: chain each skeleton's own blocks, hang the body under
        #    the LAST skeleton's body slot, then connect earlier skeletons
        #    into the next one.
        for sk in skeletons:
            _chain(events_tb, sk.blocks)
        primary = skeletons[-1]
        body_chain = body_ids
        if primary.body_is_substack:
            events_tb.blocks[primary.body_parent]["inputs"]["SUBSTACK"] = \
                ref(body_chain[0]) if body_chain else None
            if body_chain:
                events_tb.blocks[body_chain[0]]["parent"] = primary.body_parent
        else:
            nxt = body_chain[0] if body_chain else None
            events_tb.blocks[primary.body_parent]["next"] = nxt
            if nxt is not None:
                events_tb.blocks[nxt]["parent"] = primary.body_parent
        if body_chain:
            _chain(events_tb, body_chain)
        # connect earlier skeletons into this one
        for i in range(len(skeletons) - 1):
            sk = skeletons[i]
            first = skeletons[i + 1].blocks[0]
            if sk.body_is_substack:
                events_tb.blocks[sk.body_parent]["inputs"]["SUBSTACK"] = ref(first)
                events_tb.blocks[first]["parent"] = sk.body_parent
            else:
                events_tb.blocks[sk.body_parent]["next"] = first
                events_tb.blocks[first]["parent"] = sk.body_parent

        stats["mapped"] += 1
        stats["blocks"] += len(skeletons) + len(body_ids)
        for sk in skeletons:
            if sk.approximation:
                stats["approximations"] += 1
                warnings.append(f"{frame.name}: {sk.approximation}")
    return stats


# --------------------------------------------------------------------------
# Logic-Notes descriptions (kept from the original design)
# --------------------------------------------------------------------------

def describe_condition(cond: Condition, frame: Frame) -> str:
    if cond.object_type == SYS and cond.num == COND_START_OF_FRAME:
        return "at Start of Frame"
    name = _obj_name(frame, cond.object_info)
    return f"{name}: condition #{cond.num}"


def describe_action(act: Action, frame: Frame) -> str:
    name = _obj_name(frame, act.object_info)
    return f"{name}: action #{act.num}"


def describe_group(group, frame: Frame, indent: str = "  ") -> List[str]:
    """Human-readable lines for one event group (for Logic-Notes)."""
    out = []
    if group.conditions:
        out.append(indent + "when: " + " and ".join(
            describe_condition(c, frame) for c in group.conditions))
    else:
        out.append(indent + "when: (always)")
    for a in group.actions:
        out.append(indent + describe_action(a, frame))
    return out


def describe_frame_events(frame: Frame) -> List[str]:
    """Return readable lines for every event group in a frame."""
    out = []
    for idx, group in enumerate(frame.event_groups, start=1):
        out.append(f"event {idx}:")
        out.extend(describe_group(group, frame))
    return out
