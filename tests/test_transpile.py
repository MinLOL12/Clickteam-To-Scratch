"""Tests for the event → Scratch block transpiler and the progress reporter."""
from __future__ import annotations

import unittest

from cts2.mfa import Action, Condition, EventGroup, Frame, MFA, ObjectData, Parameter
from cts2.progress import Reporter
from cts2.scratch import TargetBuilder, _transparent_png
from cts2.transpile import (
    ACT_START_LOOP,
    COND_ALWAYS,
    COND_EVERY,
    COND_ON_KEY_PRESSED,
    COND_START_OF_FRAME,
    describe_frame_events,
    transpile_frame_blocks,
)


def _frame(items=(), instances=(), groups=()) -> Frame:
    return Frame(0, "Frame 1", 640, 480, (0, 0, 0, 0), 0, 0, "",
                 items=list(items), instances=list(instances),
                 event_groups=list(groups))


def _cond(obj_type, num, params=None) -> Condition:
    return Condition(obj_type, num, 0, 0, 0, 0, len(params or []), 0, 1,
                     parameters=params or [])


def _act(obj_type, num, params=None) -> Action:
    return Action(obj_type, num, 0, 0, 0, 0, len(params or []), 0,
                  parameters=params or [])


def _param(**value) -> Parameter:
    return Parameter(0, 0, value)


class TranspileTest(unittest.TestCase):
    def setUp(self):
        self.events = TargetBuilder("Frame 1-Events")
        self.events.add_costume(_transparent_png(), "runner")
        self.warnings = []
        self.notes = []
        self.mfa = MFA("Game", "", "", "", "", "", "1.0", 640, 480, 50, 0, 0, 0,
                       {}, [], [], [], [], [], b"")

    def _run(self, frame):
        stats = transpile_frame_blocks(
            frame, self.mfa, self.events, {}, self.warnings, self.notes)
        return stats

    def test_start_of_frame_compiles_to_hat(self):
        frame = _frame(groups=[EventGroup(0, 1, 1, 0, 0, 0, 0, 0,
                                          [_cond(-1, COND_START_OF_FRAME)],
                                          [_act(-1, 2001)])])
        stats = self._run(frame)
        self.assertEqual(stats["mapped"], 1)
        self.assertEqual(stats["unmapped"], 0)
        opcodes = [b["opcode"] for b in self.events.blocks.values()]
        self.assertIn("event_whenflagclicked", opcodes)
        self.assertIn("control_repeat", opcodes)   # Start loop → repeat N
        self.assertIn("event_broadcast", opcodes)  # loop broadcast
        # every block must be reachable from a topLevel hat
        toplevel = [b for b in self.events.blocks.values() if b["topLevel"]]
        self.assertEqual(len(toplevel), 1)

    def test_every_uses_ticks_param(self):
        group = EventGroup(0, 1, 0, 0, 0, 0, 0, 0,
                           [_cond(-1, COND_EVERY, [_param(every=100)])], [])
        frame = _frame(groups=[group])
        self._run(frame)
        waits = [b for b in self.events.blocks.values() if b["opcode"] == "control_wait"]
        self.assertEqual(len(waits), 1)
        # 100 ticks @ 50/s = 2 seconds
        self.assertEqual(waits[0]["inputs"]["DURATION"][1][1], "2.0")

    def test_key_pressed_hat(self):
        group = EventGroup(0, 1, 0, 0, 0, 0, 0, 0,
                           [_cond(-1, COND_ON_KEY_PRESSED, [_param(keycode=32)])], [])
        frame = _frame(groups=[group])
        self._run(frame)
        hats = [b for b in self.events.blocks.values() if b["opcode"] == "event_whenkeypressed"]
        self.assertEqual(len(hats), 1)
        self.assertEqual(hats[0]["fields"]["KEY_OPTION"][0], "space")

    def test_unmapped_conditions_stay_notes(self):
        group = EventGroup(0, 1, 0, 0, 0, 0, 0, 0,
                           [_cond(-1, -9999)], [])
        frame = _frame(groups=[group])
        stats = self._run(frame)
        self.assertEqual(stats["unmapped"], 1)
        self.assertEqual(stats["mapped"], 0)
        self.assertTrue(any("note" in n for n in self.notes))

    def test_always_compiles_forever(self):
        group = EventGroup(0, 1, 0, 0, 0, 0, 0, 0, [_cond(-1, COND_ALWAYS)], [])
        frame = _frame(groups=[group])
        self._run(frame)
        opcodes = [b["opcode"] for b in self.events.blocks.values()]
        self.assertIn("control_forever", opcodes)

    def test_block_graph_is_consistent(self):
        groups = [
            EventGroup(0, 1, 1, 0, 0, 0, 0, 0,
                       [_cond(-1, COND_START_OF_FRAME)],
                       [_act(-1, ACT_START_LOOP, [_param(string="demo"), _param(int=5)])]),
            EventGroup(0, 1, 0, 0, 0, 0, 0, 0, [_cond(-1, COND_ALWAYS)], []),
            EventGroup(0, 1, 0, 0, 0, 0, 0, 0,
                       [_cond(-1, COND_EVERY, [_param(every=50)])], []),
        ]
        frame = _frame(groups=groups)
        self._run(frame)
        ids = set(self.events.blocks)
        for bid, b in self.events.blocks.items():
            self.assertNotIn(bid, b.get("inputs", {}).get("SUBSTACK", [None, None]) or [])
            if b["next"] is not None:
                self.assertIn(b["next"], ids)
            if b["parent"] is not None:
                self.assertIn(b["parent"], ids)
            if not b["topLevel"]:
                self.assertIsNotNone(b["parent"], f"orphan {bid} {b['opcode']}")
        toplevel = [b for b in self.events.blocks.values() if b["topLevel"]]
        self.assertEqual(len(toplevel), 3)

    def test_descriptions_still_exist(self):
        frame = _frame(groups=[EventGroup(0, 1, 1, 0, 0, 0, 0, 0,
                                          [_cond(-1, COND_START_OF_FRAME)],
                                          [_act(-1, ACT_START_LOOP)])])
        lines = describe_frame_events(frame)
        self.assertTrue(any("Start of Frame" in l for l in lines))
        self.assertTrue(any("2001" in l for l in lines))


class ProgressReporterTest(unittest.TestCase):
    def test_events_stream_and_finish(self):
        seen = []
        r = Reporter(sink=seen.append)
        r.phase("chunks", total=10)
        r.tick(3, step="chunk 3/10")
        self.assertEqual(seen[-1]["type"], "progress")
        self.assertEqual(seen[-1]["pct"], 30.0)
        r.warn("image 5 unreadable")
        self.assertEqual(seen[-1]["type"], "warn")
        self.assertIn("image 5 unreadable", seen[-1]["warnings"])
        r.finish({"blocks": 12})
        self.assertEqual(seen[-1]["type"], "done")
        self.assertEqual(seen[-1]["stats"]["blocks"], 12)
        self.assertEqual(seen[-1]["overall"], 100.0)

    def test_step_does_not_shadow_method(self):
        r = Reporter()
        r.phase("read")
        r.step("reading...")   # would crash if self.step were a string
        r.tick(1)
        r.step("still reading")
        self.assertEqual(r.snapshot()["step"], "still reading")

    def test_json_lines_roundtrip(self):
        r = Reporter()
        r.phase("images", total=5)
        r.tick(2)
        lines = r.as_json_lines().strip().split("\n")
        import json
        evs = [json.loads(l) for l in lines]
        self.assertEqual(evs[-1]["done"], 2)


if __name__ == "__main__":
    unittest.main()
