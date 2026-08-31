from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import unittest
import zipfile

from cts2.bin import Reader
from cts2.converter import convert_file
from cts2.mfa import Frame, FrameInstance, _read_sound_bank, load_mfa
from cts2.png import encode_png
from cts2.scratch import _add_sprite_scripts, TargetBuilder, build_project
from tests.exebuilder import (build_exe, build_game_data, frame_chunk,
                              frame_instance, image_item_normal,
                              object_common_25, object_common_284,
                              object_names_25, object_props_25,
                              object_header_25)

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

    def test_mfa_sound_payload_is_skipped_without_reading(self):
        # MFA sound entries are inline, so the parser must advance over the
        # payload to reach the image bank. A guarded reader verifies it uses
        # skip() rather than creating a copy of the 4 KiB audio payload.
        payload = b"audio" * 819
        entry = struct.pack("<IiIiIii", 1, 0, 0, len(payload), 0, 0, 0)

        class PayloadGuardReader(Reader):
            def read(self, size):
                if size == len(payload):
                    raise AssertionError("audio payload was read")
                return super().read(size)

        reader = PayloadGuardReader(struct.pack("<i", 1) + entry + payload)
        self.assertEqual(_read_sound_bank(reader), [])
        self.assertEqual(reader.remaining(), 0)

    def test_build_valid_sb3(self):
        mfa = load_mfa(os.path.join(FIXTURES, "events.mfa"))
        sb3, report = build_project(mfa)
        self.assertTrue(sb3.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(sb3)) as z:
            pj = json.loads(z.read("project.json"))
            _validate_project(pj)
            self.assertTrue(any(t["name"] == "Logic-Notes" for t in pj["targets"]))
            # Start-of-frame + Start loop (action 2001) must compile to blocks.
            events = [t for t in pj["targets"] if t["name"] == "Frame 1-Events"]
            self.assertEqual(len(events), 1)
            self.assertGreater(len(events[0]["blocks"]), 0)
            self.assertGreater(report["blocks"], 0)
            self.assertEqual(report["events_mapped"], 1)
        # Start loop has no readable count in the fixture: it warns and
        # falls back to a default count instead of dropping the event.
        self.assertTrue(any("Start loop" in w for w in report["warnings"]))

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

    def _undecodable_exe(self) -> bytes:
        # A sprite whose image carries the RLE flag (0x01); decode_bmp cannot
        # decode it, so png stays None.  The sprite must still exist with a
        # visible placeholder costume instead of being skipped / showing "?".
        pixels = bytes([0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255])
        img = image_item_normal(0, 2, 2, pixels, mode=4, flags=0x01)
        game = build_game_data(
            name="My Game", unicode=True, build=294, images=[img],
            objects_25=[object_common_25(frames_per_anim=((0,),))],
            object_names=("Player",),
            frames=[frame_chunk(name="Frame 1",
                                instances=(frame_instance(0, 0, 320, 240),),
                                layers=(("Layer 1", 1.0, 1.0),))],
            frame_handles=(0,),
        )
        return build_exe(game, pack_files=[], unicode=True)

    def test_undecodable_image_gets_placeholder_costume(self):
        import io
        import json
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.exe")
            with open(path, "wb") as fh:
                fh.write(self._undecodable_exe())
            r = convert_file(path, os.path.join(tmp, "out.sb3"))
        self.assertTrue(any("placeholder" in w for w in r["report"]["warnings"]))
        with zipfile.ZipFile(io.BytesIO(r["project"])) as z:
            pj = json.loads(z.read("project.json"))
            sprites = [t for t in pj["targets"] if not t["isStage"]]
            player = [s for s in sprites if s["name"] == "Frame1-Player"]
            self.assertEqual(len(player), 1)
            self.assertEqual(len(player[0]["costumes"]), 1)
            self.assertEqual(player[0]["costumes"][0]["dataFormat"], "png")
            self.assertIn(player[0]["costumes"][0]["md5ext"], z.namelist())
            self.assertLess(player[0]["currentCostume"], len(player[0]["costumes"]))

    def test_hidden_instance_emits_hide(self):
        frame = Frame(0, "F", 640, 480, (0, 0, 0, 0), 0, 0, "")
        hidden = FrameInstance(100, 100, 0, 0, 1, -1, 0, 0, visible=False)
        sb = TargetBuilder("Player")
        _add_sprite_scripts(sb, frame, hidden, None, 1)
        ops = [b["opcode"] for b in sb.blocks.values()]
        self.assertIn("looks_hide", ops)
        self.assertNotIn("looks_show", ops)

        visible = FrameInstance(100, 100, 0, 0, 1, -1, 0, 0, visible=True)
        sb2 = TargetBuilder("Player")
        _add_sprite_scripts(sb2, frame, visible, None, 1)
        ops2 = [b["opcode"] for b in sb2.blocks.values()]
        self.assertIn("looks_show", ops2)
        self.assertNotIn("looks_hide", ops2)

    def test_costume_asset_id_is_bare_md5(self):
        # Scratch 3 requires assetId = md5 hex (no extension) and
        # md5ext = md5 + ".png".  Putting the extension into assetId made
        # every costume render as the empty "?" placeholder.
        mfa = load_mfa(os.path.join(FIXTURES, "events.mfa"))
        sb3, _report = build_project(mfa)
        with zipfile.ZipFile(io.BytesIO(sb3)) as z:
            pj = json.loads(z.read("project.json"))
            for target in pj["targets"]:
                if target["isStage"]:
                    self.assertIn("tempo", target)
                else:
                    for key in ("x", "y", "size", "direction", "visible",
                                "draggable", "rotationStyle"):
                        self.assertIn(key, target)
                for costume in target["costumes"]:
                    asset_id = costume["assetId"]
                    md5ext = costume["md5ext"]
                    self.assertNotIn(
                        ".", asset_id,
                        msg=f"assetId must be bare md5, got {asset_id}")
                    self.assertEqual(len(asset_id), 32)
                    self.assertTrue(md5ext.startswith(asset_id + "."))
                    self.assertIn(md5ext, z.namelist())
                # broadcasts dict must be {id: name}, not {id: [name, id]}
                for _bid, bval in target.get("broadcasts", {}).items():
                    self.assertIsInstance(bval, str)

    def test_exe_sprite_costumes_have_valid_asset_ids(self):
        pixels = bytes([0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255])
        img = image_item_normal(0, 2, 2, pixels, mode=4, flags=0)
        game = build_game_data(
            name="My Game", unicode=True, build=294, images=[img],
            objects_25=[object_common_25(frames_per_anim=((0,),))],
            object_names=("Player",),
            frames=[frame_chunk(name="Frame 1",
                                instances=(frame_instance(0, 0, 320, 240),),
                                layers=(("Layer 1", 1.0, 1.0),))],
            frame_handles=(0,),
        )
        exe = build_exe(game, pack_files=[], unicode=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.exe")
            with open(path, "wb") as fh:
                fh.write(exe)
            r = convert_file(path, os.path.join(tmp, "out.sb3"))
        with zipfile.ZipFile(io.BytesIO(r["project"])) as z:
            pj = json.loads(z.read("project.json"))
            player = [t for t in pj["targets"] if t["name"] == "Frame1-Player"]
            self.assertEqual(len(player), 1)
            c = player[0]["costumes"][0]
            self.assertNotIn(".", c["assetId"])
            self.assertEqual(c["md5ext"], c["assetId"] + ".png")
            self.assertIn(c["md5ext"], z.namelist())
            png = z.read(c["md5ext"])
            self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
