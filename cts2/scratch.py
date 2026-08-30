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
        self.current_costume = 0

    def add_variable(self, var_id: str, name: str, value: Any = 0) -> str:
        self.variables[var_id] = [name, value]
        return var_id

    def add_broadcast(self, name: str) -> str:
        bid = str(uuid.uuid4())
        self.broadcasts[bid] = [name, bid]
        return bid

    def add_costume(self, png: bytes, name: str, cx: int = 0, cy: int = 0) -> str:
        data_id = _asset_name(png, "png")
        self.costumes.append(
            {
                "name": name,
                "bitmapResolution": 1,
                "dataFormat": "png",
                "assetId": data_id,
                "md5ext": data_id,
                "rotationCenterX": cx,
                "rotationCenterY": cy,
            }
        )
        return data_id

    def add_costume_from_svg(self, svg: bytes, name: str,
                             cx: int = 0, cy: int = 0) -> str:
        data_id = _asset_name(svg, "svg")
        self.costumes.append(
            {
                "name": name,
                "bitmapResolution": 1,
                "dataFormat": "svg",
                "assetId": data_id,
                "md5ext": data_id,
                "rotationCenterX": cx,
                "rotationCenterY": cy,
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
    from .transpile import describe_frame_events

    lines: List[str] = []
    for frame in mfa.frames:
        ev = describe_frame_events(frame)
        if not ev:
            continue
        lines.append(f"── {frame.name} ──")
        lines.extend(ev[:60])
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


def solid_png(width: int, height: int, rgba: tuple) -> bytes:
    """A flat-colour PNG (Scratch renders PNGs more reliably than SVGs)."""
    from .png import encode_png
    w = max(int(width), 1)
    h = max(int(height), 1)
    r, g, b = rgba[0], rgba[1], rgba[2]
    a = rgba[3] if len(rgba) > 3 else 255
    pixels = bytearray(w * h * 4)
    for i in range(w * h):
        j = i * 4
        pixels[j] = r
        pixels[j + 1] = g
        pixels[j + 2] = b
        pixels[j + 3] = a
    return encode_png(w, h, bytes(pixels))


def _transparent_png() -> bytes:
    """1x1 fully transparent PNG for helper sprites (e.g. the Events runner)."""
    from .png import encode_png
    return encode_png(1, 1, bytes([0, 0, 0, 0]))


def build_project(mfa: MFA, progress=None) -> tuple:
    """Return (zip_bytes, report_dict)."""
    from .progress import NULL as _NULL
    from .transpile import transpile_frame_blocks

    progress = progress or _NULL
    assets: Dict[str, bytes] = {}
    report = {"warnings": [], "assets": 0, "sprites": 0,
              "blocks": 0, "events_mapped": 0, "events_total": 0,
              "approximations": 0, "unmapped_events": 0}

    progress.phase("build", total=1 + len(mfa.frames))
    progress.step("stage")
    stage = TargetBuilder("Stage", is_stage=True)
    if mfa.frames:
        f = mfa.frames[0]
        png = solid_png(480, 360, f.background)
        data = stage.add_costume(png, "backdrop1")
        assets[data] = png
    elif mfa.images:
        first = next(iter(mfa.images.values()))
        if first.png:
            data = stage.add_costume(first.png, "backdrop1")
            assets[data] = first.png
    else:
        png = solid_png(480, 360, (255, 255, 255, 255))
        data = stage.add_costume(png, "backdrop1")
        assets[data] = png

    sprites: List[TargetBuilder] = []
    for frame in mfa.frames:
        _add_frame_sprites(mfa, frame, sprites, assets, report, progress)
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
        png = solid_png(128, 128, (90, 120, 200))
        data = demo.add_costume(png, "blank")
        assets[data] = png
        sprites.append(demo)

    # Compile the Clickteam event lists into real Scratch blocks.
    progress.phase("transpile", total=sum(len(f.event_groups) for f in mfa.frames) or 1)
    done_groups = 0
    sprite_by_handle: Dict[int, TargetBuilder] = {}
    events_sprites: List[TargetBuilder] = []
    for frame in mfa.frames:
        _collect_sprite_handles(frame, sprites, sprite_by_handle)
        events_tb = TargetBuilder(f"{frame.name}-Events")
        data = events_tb.add_costume(_transparent_png(), "runner")
        assets[data] = _transparent_png()
        events_sprites.append(events_tb)
        ev_notes: List[str] = []
        if frame.event_groups:
            progress.step(
                f"frame '{frame.name}': {len(frame.event_groups)} event groups")
            stats = transpile_frame_blocks(
                frame, mfa, events_tb, sprite_by_handle,
                report["warnings"], ev_notes)
            report["blocks"] += stats["blocks"]
            report["events_mapped"] += stats["mapped"]
            report["events_total"] += stats["groups"]
            report["approximations"] += stats["approximations"]
            report["unmapped_events"] += stats["unmapped"]
            if stats["unmapped"]:
                report["warnings"].append(
                    f"frame '{frame.name}': {stats['unmapped']}/{stats['groups']} "
                    f"event groups kept as Logic-Notes (opcodes not mapped to "
                    f"Scratch blocks)")
            if stats["mapped"]:
                report["notes"] = report.get("notes", []) + [
                    f"frame '{frame.name}': compiled {stats['mapped']} event "
                    f"group(s) into {stats['blocks']} Scratch blocks"]
        done_groups += len(frame.event_groups)
        progress.tick(done_groups)
    sprites.extend(events_sprites)

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

    progress.phase("zip", total=1 + len(assets))
    progress.tick(0, step="writing project.json")
    zf = zipfile.ZipFile
    out = __import__("io").BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(project, separators=(",", ":")))
        zipped = 0
        for asset_id, data in assets.items():
            z.writestr(asset_id, data)
            zipped += 1
            progress.tick(zipped, step=f"packing asset {zipped}/{len(assets)}")
    report["assets"] = len(assets)
    report["sprites"] = len(sprites)
    return out.getvalue(), report


def _placeholder_png(width: int, height: int, name: str) -> bytes:
    """Return a visible labelled placeholder PNG for an undecodable image.

    The Clickteam image bank may hold images whose pixel format the decoder
    does not understand (compressed / exotic modes).  Rather than silently
    dropping the object (which is what made Scratch show the "?" placeholder
    costume), we render a magenta-bordered box *as a real PNG* so the object
    still appears at the correct position and the miss is reported.
    """
    from .png import encode_png
    w = max(int(width or 32), 1)
    h = max(int(height or 32), 1)
    pixels = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            edge = x < 2 or y < 2 or x >= w - 2 or y >= h - 2
            if edge:
                pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3] = 0x7A, 0x00, 0x40, 255
            else:
                pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3] = 0xD8, 0x00, 0x73, 255
    return encode_png(w, h, bytes(pixels))


def _collect_sprite_handles(frame: Frame, sprites: List[TargetBuilder],
                            sprite_by_handle: Dict[int, TargetBuilder]) -> None:
    """Map each frame item's handle to the sprite that represents it."""
    seen: dict = {}
    for inst in frame.instances:
        item = _find_item(frame, inst.item_handle)
        if item is None:
            continue
        if item.handle in seen:
            sprite_by_handle[item.handle] = seen[item.handle]
            continue
        name = _sprite_name(frame, item)
        for sb in sprites:
            if sb.name == name:
                seen[item.handle] = sb
                sprite_by_handle[item.handle] = sb
                break


def _add_frame_sprites(mfa: MFA, frame: Frame, sprites: List[TargetBuilder],
                       assets: Dict[str, bytes], report: dict, progress=None) -> None:
    from .progress import NULL as _NULL
    progress = progress or _NULL
    seen: dict = {}
    total = len(frame.instances) or 1
    for n, inst in enumerate(frame.instances, start=1):
        progress.tick(n, total, step=f"frame '{frame.name}': sprite {n}/{total}")
        item = _find_item(frame, inst.item_handle)
        if item is None:
            continue
        visual = _item_image(mfa, item)
        png = visual.png if visual is not None else None
        if png:
            hotspot = (visual.hotspot_x, visual.hotspot_y)
        else:
            # Image missing or its pixel data did not decode.  Emit a visible
            # PNG placeholder instead of a blank / "?" costume.
            ph = _placeholder_png(
                visual.width if visual else item.width,
                visual.height if visual else item.height,
                item.name)
            hotspot = (0, 0)
            report["warnings"].append(
                f"Object {item.name}: image missing/undecodable; used a "
                f"PNG placeholder costume"
            )
            png = None
        src_item = item.handle
        if src_item in seen:
            sb = seen[src_item]
        else:
            sb = TargetBuilder(_sprite_name(frame, item))
            if png:
                asset = sb.add_costume(png, "costume1", *hotspot)
                assets[asset] = png
                # add rest of animations as costumes
                for idx, h in enumerate(item.frames[1:], start=2):
                    img = mfa.images.get(h)
                    if img and img.png:
                        a = sb.add_costume(
                            img.png, f"costume{idx}", img.hotspot_x,
                            img.hotspot_y)
                        assets[a] = img.png
            else:
                asset = sb.add_costume(ph, "costume1", *hotspot)
                assets[asset] = ph
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
    # Honour the Clickteam object's initial visibility: an instance that
    # starts hidden must stay hidden (and appears only when the converted
    # events call `show`), instead of being force-shown at green flag.
    vis_id = _nid()
    sb.blocks[vis_id] = _block(
        "looks_show" if getattr(inst, "visible", True) else "looks_hide")
    gotoxy_id = _nid()
    sb.blocks[gotoxy_id] = _block(
        "motion_gotoxy", next_id=vis_id,
        inputs={"X": num(sx), "Y": num(sy)},
    )
    sb.blocks[vis_id]["parent"] = gotoxy_id
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
        sb.blocks[vis_id]["next"] = loop
        sb.blocks[loop]["parent"] = vis_id
    return hat
