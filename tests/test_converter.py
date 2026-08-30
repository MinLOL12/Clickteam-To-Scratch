from __future__ import annotations

import io
import json
import os
import unittest
import zipfile

from cts2.converter import convert_file
from cts2.mfa import load_mfa
from cts2.png import encode_png
from cts2.scratch import build_project

HERE = os.path.dirname(__file__)
FIXTURES = os.path.join(HERE, "fixtures")


def _validate_project(pj: dict) -> None:
    assert pj["meta"]["semver"].startswith("3.0")
    assert "targets" in pj
    for target in pj["targets"]:
        blocks = target["blocks"]
        ids = set(blocks)
        for bid, block in blocks.items():
            assert isinstance(block, dict)
            nb = block.get("next")
            if nb:
                assert nb in ids, f"{target['name']} {bid} next {nb} missing"
            pb = block.get("parent")
            if pb:
                assert pb in ids
            for inp in block.get("inputs", {}).values():
                if isinstance(inp, list) and len(inp) == 2 and isinstance(inp[1], list):
                    # shadow/ref blocks reference other block ids in some forms
                    for sub in inp[1]:
                        if isinstance(sub, str) and sub in ids:
                            pass
                if isinstance(inp, list) and len(inp) == 2 and isinstance(inp[1], str):
                    assert inp[1] in ids, f"input ref missing {inp[1]}"


class TestConverter(unittest.TestCase):
    def test_minimal_mfa_parses(self):
        mfa = load_mfa(os.path.join(FIXTURES, "minimal.mfa"))
        self.assertEqual(mfa.name, "Test")
        self.assertEqual(len(mfa.frames), 1)
        frame = mfa.frames[0]
        self.assertEqual(frame.name, "Frame 1")
        self.assertEqual(len(frame.instances), 1)

    def test_events_mfa_parses(self):
        mfa = load_mfa(os.path.join(FIXTURES, "events.mfa"))
        self.assertEqual(len(mfa.frames), 1)
        groups = mfa.frames[0].event_groups
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].conditions), 1)
        self.assertEqual(len(groups[0].actions), 1)
        self.assertEqual(groups[0].conditions[0].num, -1)

    def test_build_valid_sb3(self):
        mfa = load_mfa(os.path.join(FIXTURES, "events.mfa"))
        sb3, report = build_project(mfa)
        self.assertTrue(sb3.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(sb3)) as z:
            pj = json.loads(z.read("project.json"))
            _validate_project(pj)
            self.assertTrue(any(t["name"] == "Logic-Notes" for t in pj["targets"]))
        self.assertEqual(report["warnings"], [])

    def test_template_build_if_present(self):
        # The community template is optional (not checked into git).
        template = os.path.join("/tmp", "template.mfa")
        if not os.path.exists(template):
            self.skipTest("template.mfa not present")
        sb3, report = build_project(load_mfa(template))
        with zipfile.ZipFile(io.BytesIO(sb3)) as z:
            self.assertIn("project.json", z.namelist())
            pj = json.loads(z.read("project.json"))
            _validate_project(pj)
        # The template has one Active object with an image.
        sprite_names = [t["name"] for t in pj["targets"]]
        self.assertTrue(any(n.startswith("Frame1-") for n in sprite_names))

    def test_png_encoder(self):
        png = encode_png(2, 2, bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 0, 0, 0, 255]))
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
