"""Build a Scratch 3 / PenguinMod .sb3 project from a parsed Clickteam MFA."""
from __future__ import annotations

import hashlib
import json
import posixpath
import uuid
import zipfile
from typing import Any, Dict, List, Optional

from .mfa import MFA, Frame, FrameInstance, ImageItem, ObjectData


def _nid() -> str:
    return str(uuid.uuid4())


def num(v: float) -> list:
    return [1, [4, str(v)]]


def text(v: str) -> list:
    return [1, [10, v]]


def ref(block_id: str) -> list:
    return [2, block_id]


def var_reference(var_id: str) -> list:
    return [3, [12, "value", var_id], [10, ""]]


def _block(opcode: str, next_id: Optional[str] = None, parent: Optional[str] = None,
           inputs: Optional[dict] = None, fields: Optional[dict] = None,
           shadow=False, toplevel=False, x=40, y=40) -> dict:
    return {
        "opcode": opcode,
        "next": next_id,
        "parent": parent,
        "inputs": inputs or {},
        "fields": fields or {},
        "shadow": shadow,
        "topLevel": toplevel,
        "x": x,
        "y": y,
    }


def script_when_green_flag(blocks: dict, body_next: Optional[str] = None,
                           x: int = 40, y: int = 40) -> str:
    hat = _nid()
    blocks[hat] = _block(
        "event_whenflagclicked", next_id=body_next, fields={}, shadow=False,
        toplevel=True, x=x, y=y,
    )
    return hat


def block_gotoxy(blocks: dict, sx: float, sy: float, next_id: Optional[str]) -> str:
    bid = _nid()
    blocks[bid] = _block(
        "motion_gotoxy", next_id=next_id,
        inputs={"X": num(sx), "Y": num(sy)},
    )
    return bid


def block_show(blocks: dict, next_id: Optional[str]) -> str:
    bid = _nid()
    blocks[bid] = _block("looks_show", next_id=next_id)
    return bid


def block_forever(blocks: dict, substack: str, next_id: Optional[str] = None) -> str:
    bid = _nid()
    blocks[bid] = _block(
        "control_forever", next_id=next_id,
        inputs={"SUBSTACK": ref(substack)},
    )
    return bid


def block_nextcostume(blocks: dict, next_id: Optional[str]) -> str:
    bid = _nid()
    blocks[bid] = _block("looks_nextcostume", next_id=next_id)
    return bid


def block_broadcast(blocks: dict, broadcast: str, next_id: Optional[str]) -> str:
    bid = _nid()
    blocks[bid] = _block(
        "event_broadcast", next_id=next_id,
        inputs={"BROADCAST_OPTION": [1, [11, broadcast, broadcast]]},
    )
    return bid


def _asset_name(data: bytes, ext: str) -> str:
    return hashlib.md5(data).hexdigest() + "." + ext


class TargetBuilder:
    def __init__(self, name: str, is_stage: bool = False):
        self.is_stage = is_stage
        self.name = name
        self.variables: Dict[str, List[Any]] = {}
        self.lists: Dict[str, List[Any]] = {}
        self.broadcasts: Dict[str, List[Any]] = {}
        self.blocks: Dict[str, dict] = {}
        self.costumes: List[dict] = []
        self.sounds: List[dict] = []
        self.comments: List[dict] = {}
        self.current_costume = 1

    def add_variable(self, var_id: str, name: str, value: Any = 0) -> str:
        self.variables[var_id] = [name, value]
        return var_id

    def add_broadcast(self, name: str) -> str:
        bid = str(uuid.uuid4())
        self.broadcasts[bid] = [name, bid]
        return bid

    def add_costume(self, png: bytes, name: str) -> str:
        data_id = _asset_name(png, "png")
        self.costumes.append(
            {
                "name": name,
                "bitmapResolution": 1,
                "dataFormat": "png",
                "assetId": data_id,
                "md5ext": data_id,
                "rotationCenterX": 0,
                "rotationCenterY": 0,
            }
        )
        return data_id

    def add_costume_from_svg(self, svg: bytes, name: str) -> str:
        data_id = _asset_name(svg, "svg")
        self.costumes.append(
            {
                "name": name,
                "bitmapResolution": 1,
                "dataFormat": "svg",
                "assetId": data_id,
                "md5ext": data_id,
                "rotationCenterX": 0,
                "rotationCenterY": 0,
            }
        )
        return data_id

    def to_json(self) -> dict:
        return {
            "isStage": self.is_stage,
            "name": self.name,
            "variables": self.variables,
            "lists": self.lists,
            "broadcasts": self.broadcasts,
            "blocks": self.blocks,
            "comments": self.comments,
            # Scratch's currentCostume is a 0-based index; an out-of-range
            # value makes editors render the "?" placeholder costume.
            "currentCostume": max(0, min(self.current_costume,
                                         len(self.costumes) - 1)),
            "costumes": self.costumes,
            "sounds": self.sounds,
            "volume": 100,
            "layerOrder": 0,
        }


def _event_notes_svg(mfa: MFA) -> Optional[bytes]:
    lines: List[str] = []
    for frame in mfa.frames:
        for idx, group in enumerate(frame.event_groups[:30], start=1):
            cond = ", ".join(
                (f"cond({c.object_type},{c.num})" for c in group.conditions[:5])
            )
            act = ", ".join(
                (f"act({a.object_type},{a.num})" for a in group.actions[:5])
            )
            lines.append(f"{frame.name} #{idx}: {cond} => {act}")
    if not lines:
        return None
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    items = "".join(
        f'<text x="10" y="{20 + i * 18}" font-size="12" fill="#e8eaef">{esc(ln)}</text>'
        for i, ln in enumerate(lines)
    ) or '<text x="10" y="20">No events</text>'
    h = min(360, 40 + len(lines) * 18)
    svg = (
        f'<svg width="480" height="{h}" xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="100%" height="100%" fill="#10131d"/>'
        f'{items}</svg>'
    )
    return svg.encode()


def solid_svg(width: int, height: int, rgb: tuple) -> bytes:
    w = max(width, 1)
    h = max(height, 1)
    r, g, b, *_ = rgb
    return (
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="100%" height="100%" fill="rgb({r},{g},{b})"/></svg>'
    ).encode()


def build_project(mfa: MFA) -> tuple:
    """Return (zip_bytes, report_dict)."""
    assets: Dict[str, bytes] = {}
    report = {"warnings": [], "assets": 0, "sprites": 0}

    stage = TargetBuilder("Stage", is_stage=True)
    if mfa.frames:
        f = mfa.frames[0]
        svg = solid_svg(480, 360, f.background)
        data = stage.add_costume_from_svg(svg, "backdrop1")
        assets[data] = svg
    elif mfa.images:
        first = next(iter(mfa.images.values()))
        if first.png:
            data = stage.add_costume(first.png, "backdrop1")
            assets[data] = first.png
    else:
        svg = solid_svg(480, 360, (255, 255, 255, 255))
        data = stage.add_costume_from_svg(svg, "backdrop1")
        assets[data] = svg

    sprites: List[TargetBuilder] = []
    for frame in mfa.frames:
        _add_frame_sprites(mfa, frame, sprites, assets, report)
    # make sure assets referenced are present
    for target in [stage] + sprites:
        for c in target.costumes:
            asset_id = c["md5ext"]
            if asset_id not in assets:
                report["warnings"].append(f"Missing asset {asset_id} for {target.name}")

    if not sprites:
        # Always have at least one sprite so the project is openable.
        demo = TargetBuilder("GameLog")
        demo.add_variable(str(uuid.uuid4()), "converted")
        svg = solid_svg(128, 128, (90, 120, 200))
        data = demo.add_costume_from_svg(svg, "blank")
        assets[data] = svg
        sprites.append(demo)

    notes = _event_notes_svg(mfa)
    if notes:
        log_sprite = TargetBuilder("Logic-Notes")
        data = log_sprite.add_costume_from_svg(notes, "events")
        assets[data] = notes
        sprites.append(log_sprite)

    targets = [stage] + sprites
    # layer order
    for i, t in enumerate(targets):
        t.layerOrder = i

    project = {
        "targets": [t.to_json() for t in targets],
        "monitors": [],
        "extensions": [],
        "meta": {
            "semver": "3.0.0",
            "vm": "0.3.0",
            "agent": "Clickteam-to-Scratch",
        },
    }

    zf = zipfile.ZipFile
    out = __import__("io").BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(project, separators=(",", ":")))
        for asset_id, data in assets.items():
            z.writestr(asset_id, data)
    return out.getvalue(), report


def _add_frame_sprites(mfa: MFA, frame: Frame, sprites: List[TargetBuilder],
                       assets: Dict[str, bytes], report: dict) -> None:
    seen: dict = {}
    for inst in frame.instances:
        item = _find_item(frame, inst.item_handle)
        if item is None:
            continue
        visual = _item_image(mfa, item)
        if visual is None:
            report["warnings"].append(f"No image for object {item.name}")
            continue
        png = visual.png
        if not png:
            continue
        src_item = item.handle
        if src_item in seen:
            sb = seen[src_item]
        else:
            sb = TargetBuilder(_sprite_name(frame, item))
            asset = sb.add_costume(png, "costume1")
            assets[asset] = png
            # add rest of animations as costumes
            for idx, h in enumerate(item.frames[1:], start=2):
                img = mfa.images.get(h)
                if img and img.png:
                    a = sb.add_costume(img.png, f"costume{idx}")
                    assets[a] = img.png
            _add_sprite_scripts(sb, frame, inst, item, len(item.frames))
            seen[src_item] = sb
            sprites.append(sb)
            report["sprites"] += 1


def _find_item(frame: Frame, handle: int) -> Optional[ObjectData]:
    for item in frame.items:
        if item.handle == handle:
            return item
    return None


def _item_image(mfa: MFA, item: ObjectData) -> Optional[ImageItem]:
    for h in item.frames:
        img = mfa.images.get(h)
        if img is not None and img.png:
            return img
    return None


def _sprite_name(frame: Frame, item: ObjectData) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in item.name) or "Object"
    return f"{frame.name}-{safe}".replace(" ", "")


def _add_sprite_scripts(sb: TargetBuilder, frame: Frame, inst: FrameInstance,
                        item: ObjectData, costumes: int) -> None:
    sx = inst.x - frame.size_x / 2
    sy = frame.size_y / 2 - inst.y
    show_id = _nid()
    sb.blocks[show_id] = _block("looks_show")
    gotoxy_id = _nid()
    sb.blocks[gotoxy_id] = _block(
        "motion_gotoxy", next_id=show_id,
        inputs={"X": num(sx), "Y": num(sy)},
    )
    sb.blocks[show_id]["parent"] = gotoxy_id
    hat = script_when_green_flag(sb.blocks, gotoxy_id)
    sb.blocks[gotoxy_id]["parent"] = hat
    # animate if multiple costumes
    if costumes > 1:
        nxt = _nid()
        sb.blocks[nxt] = _block("looks_nextcostume")
        loop = _nid()
        sb.blocks[loop] = _block(
            "control_forever",
            inputs={"SUBSTACK": ref(nxt)},
        )
        sb.blocks[nxt]["parent"] = loop
        sb.blocks[show_id]["next"] = loop
        sb.blocks[loop]["parent"] = show_id
    return hat
