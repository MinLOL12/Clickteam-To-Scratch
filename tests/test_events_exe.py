"""Tests for the EXE frame-events decoder and end-to-end block emission."""
from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import unittest
import zipfile

from cts2.events_exe import read_exe_events
from cts2.converter import convert_file
from cts2.transpile import (
    COND_ALWAYS, COND_START_OF_FRAME, STORYBOARD, TIMER, MOUSE_KB, SYS,
    EXE_COND_START_OF_FRAME, EXE_COND_EVERY, EXE_COND_KEY_PRESSED,
    EXE_COND_ALWAYS, ACT_START_LOOP, EXE_ACT_START_LOOP,
    transpile_frame_blocks,
)
from cts2.mfa import Action, Condition, EventGroup, Frame, MFA, Parameter
from cts2.scratch import TargetBuilder, _transparent_png

try:
    from exebuilder import (
        build_exe, build_game_data, frame_chunk, frame_instance,
        image_item_normal, object_common_25,
    )
except ImportError:
    from tests.exebuilder import (
        build_exe, build_game_data, frame_chunk, frame_instance,
        image_item_normal, object_common_25,
    )


def _param(code: int, payload: bytes) -> bytes:
    # MFA/EXE parameter: size (including size field) = 2 + 2 + len(payload)
    size = 4 + len(payload)
    return struct.pack("<hh", size, code) + payload


def _condition(obj_type: int, num: int, params: bytes = b"",
               object_info: int = 0) -> bytes:
    # size covers the whole condition record including the size field.
    body = struct.pack("<hhHHbbBBH",
                       obj_type, num, object_info, 0, 0, 0,
                       0 if not params else 1, 0, 0)
    # num_params is set below once we know; rebuild properly.
    nparams = 1 if params else 0
    body = struct.pack("<hhHHbbBBH",
                       obj_type, num, object_info, 0, 0, 0,
                       nparams, 0, 0)
    body = body + params
    size = 2 + len(body)
    return struct.pack("<H", size) + body


def _action(obj_type: int, num: int, params: bytes = b"",
            object_info: int = 0) -> bytes:
    nparams = 1 if params else 0
    body = struct.pack("<hhHHbbBB",
                       obj_type, num, object_info, 0, 0, 0, nparams, 0)
    body = body + params
    size = 2 + len(body)
    return struct.pack("<H", size) + body


def _event_group(conds, acts, build: int = 294) -> bytes:
    body = bytearray()
    body.append(len(conds))
    body.append(len(acts))
    body += struct.pack("<H", 0)  # flags
    if build >= 284:
        body += struct.pack("<hii", 0, 0, 0)  # nop, isRestricted, restrictCpt
    else:
        body += struct.pack("<hhhh", 0, 0, 0, 0)
    for c in conds:
        body += c
    for a in acts:
        body += a
    size = 2 + len(body)
    return struct.pack("<h", -size) + bytes(body)


def _exe_events_blob(groups, build: int = 294) -> bytes:
    out = bytearray()
    out += b"ER>>"
    out += struct.pack("<hhh", 100, 100, 1)
    out += struct.pack("<" + "h" * 17, *([0] * 17))
    out += struct.pack("<h", 0)  # no qualifiers
    out += b"ERes" + struct.pack("<i", len(groups))
    body = b"".join(groups)
    out += b"ERev" + struct.pack("<i", len(body)) + body
    out += b"<<ER"
    return bytes(out)


class TestExeEventsReader(unittest.TestCase):
    def test_reads_start_of_frame_group(self):
        # Storyboard / Start of Frame + System Start loop
        cond = _condition(STORYBOARD, EXE_COND_START_OF_FRAME)
        act = _action(SYS, EXE_ACT_START_LOOP)
        eg = _event_group([cond], [act], build=294)
        blob = _exe_events_blob([eg], build=294)
        groups, warns = read_exe_events(blob, build=294)
        self.assertEqual(len(groups), 1, msg=f"warns={warns}")
        self.assertEqual(len(groups[0].conditions), 1)
        self.assertEqual(groups[0].conditions[0].object_type, STORYBOARD)
        self.assertEqual(groups[0].conditions[0].num, EXE_COND_START_OF_FRAME)
        self.assertEqual(len(groups[0].actions), 1)
        self.assertEqual(groups[0].actions[0].num, EXE_ACT_START_LOOP)

    def test_reads_every_and_key(self):
        every_param = _param(13, struct.pack("<ii", 500, 0))  # 500 ms
        cond_every = _condition(TIMER, EXE_COND_EVERY, every_param)
        key_param = _param(14, struct.pack("<H", 32))  # space
        cond_key = _condition(MOUSE_KB, EXE_COND_KEY_PRESSED, key_param)
        act = _action(SYS, EXE_ACT_START_LOOP)
        g1 = _event_group([cond_every], [act], build=294)
        g2 = _event_group([cond_key], [act], build=294)
        blob = _exe_events_blob([g1, g2], build=294)
        groups, warns = read_exe_events(blob, build=294)
        self.assertEqual(len(groups), 2, msg=f"warns={warns} groups={groups}")
        self.assertEqual(groups[0].conditions[0].object_type, TIMER)
        self.assertEqual(groups[1].conditions[0].object_type, MOUSE_KB)


class TestExeEventsTranspile(unittest.TestCase):
    def setUp(self):
        self.events = TargetBuilder("Frame 1-Events")
        self.events.add_costume(_transparent_png(), "runner")
        self.warnings = []
        self.notes = []
        self.mfa = MFA("Game", "", "", "", "", "", "1.0", 640, 480, 50, 0, 0, 0,
                       {}, [], [], [], [], [], b"")

    def test_storyboard_start_of_frame(self):
        frame = Frame(0, "Frame 1", 640, 480, (0, 0, 0, 0), 0, 0, "",
                      event_groups=[EventGroup(
                          0, 1, 1, 0, 0, 0, 0, 0,
                          [Condition(STORYBOARD, EXE_COND_START_OF_FRAME,
                                     0, 0, 0, 0, 0, 0, 0)],
                          [Action(SYS, EXE_ACT_START_LOOP, 0, 0, 0, 0, 0, 0)])])
        stats = transpile_frame_blocks(
            frame, self.mfa, self.events, {}, self.warnings, self.notes)
        self.assertEqual(stats["mapped"], 1, msg=self.notes)
        opcodes = [b["opcode"] for b in self.events.blocks.values()]
        self.assertIn("event_whenflagclicked", opcodes)
        self.assertIn("event_broadcast", opcodes)

    def test_exe_end_to_end_emits_blocks(self):
        cond = _condition(STORYBOARD, EXE_COND_START_OF_FRAME)
        act = _action(SYS, EXE_ACT_START_LOOP)
        eg = _event_group([cond], [act], build=294)
        events = _exe_events_blob([eg], build=294)
        pixels = bytes([0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255])
        game = build_game_data(
            name="My Game", unicode=True, build=294,
            images=[image_item_normal(0, 2, 2, pixels)],
            objects_25=[object_common_25(frames_per_anim=((0,),))],
            object_names=("Player",),
            frames=[frame_chunk(name="Frame 1",
                                instances=(frame_instance(0, 0, 320, 240),),
                                layers=(("Layer 1", 1.0, 1.0),),
                                events=events)],
            frame_handles=(0,),
        )
        exe = build_exe(game, pack_files=[], unicode=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.exe")
            with open(path, "wb") as fh:
                fh.write(exe)
            r = convert_file(path, os.path.join(tmp, "out.sb3"))
        report = r["report"]
        self.assertGreater(report.get("events_mapped", 0), 0, msg=report)
        self.assertGreater(report.get("blocks", 0), 0)
        with zipfile.ZipFile(io.BytesIO(r["project"])) as z:
            pj = json.loads(z.read("project.json"))
            events_sprites = [t for t in pj["targets"] if t["name"].endswith("-Events")]
            self.assertTrue(events_sprites)
            self.assertGreater(len(events_sprites[0]["blocks"]), 0)
            # costumes resolve
            for t in pj["targets"]:
                for c in t["costumes"]:
                    self.assertNotIn(".", c["assetId"])
                    self.assertIn(c["md5ext"], z.namelist())


if __name__ == "__main__":
    unittest.main()
