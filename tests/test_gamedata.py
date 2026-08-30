"""Tests for the native Fusion 2.5 EXE game-data reader (no CTFAK).

Synthesizes F2.5 executables following the PAME/PAMU chunk layout and
checks that the built-in reader rebuilds the project, including
compressed, encrypted and 2.5+ (LZ4) variants.
"""
from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
import zlib

from cts2 import gamedata
from cts2.converter import convert_file

try:  # tests/ on sys.path when running `unittest discover -s tests`
    from exebuilder import (
        app_header_chunk,
        build_exe,
        build_game_data,
        build_pack,
        chunk,
        frame_chunk,
        frame_data,
        frame_instance,
        image_bank,
        image_item_25,
        image_item_normal,
        last_chunk,
        object_common_25,
        object_common_284,
        object_common_old,
        pe_header,
        wstring,
    )
except ImportError:  # running from the repo root
    from tests.exebuilder import (
        app_header_chunk,
        build_exe,
        build_game_data,
        build_pack,
        chunk,
        frame_chunk,
        frame_data,
        frame_instance,
        image_bank,
        image_item_25,
        image_item_normal,
        last_chunk,
        object_common_25,
        object_common_284,
        object_common_old,
        pe_header,
        wstring,
    )

HERE = os.path.dirname(__file__)


def _png_pixels_2x2() -> bytes:
    """2x2 24bpp (mode 4) BGR rows: red/green then blue/white."""
    return bytes([
        0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00,   # red, green
        0xFF, 0x00, 0x00, 0xFF, 0xFF, 0xFF,   # blue, white
    ])


def _sound_bank() -> bytes:
    """One sound: handle 0, name 'Beep', 4 audio bytes."""
    payload = "Beep".encode("utf-16-le") + b"\x12\x34\x56\x78"
    comp = zlib.compress(payload)
    data = bytearray()
    data += struct.pack("<i", 1)              # count
    data += struct.pack("<I", 1)              # handle (stored +1)
    data += struct.pack("<i", 0)              # checksum
    data += struct.pack("<I", 0)              # references
    data += struct.pack("<i", len(payload))   # decompressed size
    data += b"\x00" + b"\x00\x00\x00"         # flags + padding
    data += struct.pack("<i", 0)              # reserved
    data += struct.pack("<i", 4)              # name length
    data += struct.pack("<i", len(comp))      # compressed size
    data += comp
    return chunk(26216, bytes(data))


def _standard_exe(unicode=True, with_pack=True, pack_files=None,
                  build=294, extra_chunks=(), code=b"") -> bytes:
    pixels = _png_pixels_2x2()
    images = [image_item_normal(0, 2, 2, pixels, build=build)]
    game = build_game_data(
        name="My Game",
        unicode=unicode,
        build=build,
        images=images,
        objects_25=[object_common_25(frames_per_anim=((0,),))],
        object_names=("Player",),
        frames=[frame_chunk(name="Frame 1", instances=(frame_instance(),),
                            layers=(("Layer 1", 1.0, 1.0),))],
        frame_handles=(0,),
        extra_chunks=(_sound_bank(),) + tuple(extra_chunks),
    )
    pack = pack_files if with_pack else None
    if pack is None and with_pack:
        pack = []  # empty pack, no raw MFA
    return build_exe(game, pack_files=pack, unicode=unicode, code=code)


class TestGameData(unittest.TestCase):
    def _load(self, exe: bytes):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "game.exe")
            with open(path, "wb") as fh:
                fh.write(exe)
            return gamedata.load_game_data_from_exe(path)

    def test_basic_exe_rebuild(self):
        exe = _standard_exe()
        mfa, notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")
        self.assertEqual(mfa.author, "Tester")
        self.assertEqual(len(mfa.frames), 1)
        self.assertEqual(mfa.frames[0].name, "Frame 1")
        self.assertEqual(mfa.frames[0].size_x, 640)
        self.assertEqual(len(mfa.frames[0].instances), 1)
        self.assertEqual(len(mfa.frames[0].layers), 1)
        self.assertEqual(mfa.frames[0].layers[0].name, "Layer 1")
        # image decoded
        self.assertIn(0, mfa.images)
        img = mfa.images[0]
        self.assertEqual((img.width, img.height), (2, 2))
        self.assertIsNotNone(img.png)
        self.assertTrue(img.png.startswith(b"\x89PNG"))
        # sound decoded
        self.assertEqual(len(mfa.sounds), 1)
        self.assertEqual(mfa.sounds[0].name, "Beep")
        self.assertEqual(mfa.sounds[0].data, b"\x12\x34\x56\x78")
        # globals
        self.assertEqual([v.value for v in mfa.global_values], [0, 3])
        self.assertEqual([v.value for v in mfa.global_strings], ["Hello"])
        self.assertTrue(any("Fusion build 294" in n for n in notes))

    def test_convert_exe_to_sb3(self):
        exe = _standard_exe()
        with tempfile.TemporaryDirectory() as tmp:
            exe_path = os.path.join(tmp, "game.exe")
            with open(exe_path, "wb") as fh:
                fh.write(exe)
            out = os.path.join(tmp, "game.sb3")
            result = convert_file(exe_path, out)
            self.assertTrue(os.path.exists(out))
            with zipfile.ZipFile(io.BytesIO(result["project"])) as z:
                names = z.namelist()
                self.assertIn("project.json", names)
                pj = json.loads(z.read("project.json"))
                sprite_names = [t["name"] for t in pj["targets"]]
                self.assertTrue(any(n == "Frame1-Player" for n in sprite_names))
                pngs = [n for n in names if n.endswith(".png")]
                # Backdrop + sprites are all real PNGs now (no SVG costumes).
                self.assertGreaterEqual(len(pngs), 1)
            notes = result["report"].get("notes", [])
            self.assertTrue(any("no CTFAK needed" in n for n in notes))
            # The conversion should never have touched CTFAK.
            self.assertFalse(any("CTFAK produced" in n for n in notes))

    def test_exe_with_empty_pack(self):
        exe = _standard_exe(with_pack=True, pack_files=[])
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")
        self.assertEqual(len(mfa.frames), 1)

    def test_exe_with_pack_and_other_files(self):
        exe = _standard_exe(with_pack=True, pack_files=[
            ("readme.txt", b"hello", False),
        ])
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")

    def test_exe_without_pack(self):
        exe = _standard_exe(with_pack=False)
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")

    def test_exe_without_extra_section(self):
        # No .extra section: the pack sits after the last section's raw
        # data, like real single-section EXEs.
        game = build_game_data(
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2())],
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[frame_chunk(instances=(frame_instance(),))],
        )
        code = b"\xCC" * 64
        pack = build_pack([], unicode=True)
        appended = code + pack + game
        raw_ptr = 0x200
        exe = pe_header(b".text", raw_ptr, len(code))
        exe += b"\x00" * (raw_ptr - len(exe)) + appended
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")

    def test_two_five_plus_image_lz4(self):
        # 2.5+ image: 2x2 mode 8 RGBA pixels compressed as an LZ4 block.
        pixels = bytes([
            255, 0, 0, 255,   0, 255, 0, 128,
            0, 0, 255, 255, 255, 255, 255, 64,
        ])
        game = build_game_data(
            images=[image_item_25(0, 2, 2, pixels)],
            objects_25=[object_common_25(frames_per_anim=((0,),))],
            object_names=("Player",),
            frames=[frame_chunk(instances=(frame_instance(),))],
        )
        exe = build_exe(game)
        mfa, _notes = self._load(exe)
        img = mfa.images.get(0)
        self.assertIsNotNone(img)
        self.assertEqual((img.width, img.height), (2, 2))
        self.assertEqual(img.graphic_mode, 8)
        self.assertIsNotNone(img.png)

    def test_old_style_frame_items(self):
        # Pre-2.5 layout: chunked ObjectInfo (8745) + classic ObjectCommon.
        props = object_common_old(frames_per_anim=((0,),))
        game = build_game_data(
            build=250,
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2(), build=250)],
            old_objects=[(0, 2, "Player", props)],
            frames=[frame_chunk(instances=(frame_instance(),))],
        )
        exe = build_exe(game)
        mfa, _notes = self._load(exe)
        self.assertEqual(len(mfa.frames[0].items), 1)
        obj = mfa.frames[0].items[0]
        self.assertEqual(obj.name, "Player")
        self.assertEqual(obj.frames, [0])

    # -- MMF2-era builds (the FNaF 1 shape) ----------------------------------
    #
    # FNaF 1 is a PAME (ASCII, non-2.5+) MMF2 build 284 game.  Real games of
    # that era zlib-compress and/or RC4-encrypt the sub-chunk payloads
    # *inside* frames and object infos.  A reader that only handles top-level
    # chunk flags parses the raw streams as garbage instances that all
    # "reference missing objects", and the SB3 ends up as one empty sprite.

    def test_old_style_compressed_inner_chunks(self):
        props = object_common_284(frames_per_anim=((0,),))
        game = build_game_data(
            name="Five Nights at Freddy's",
            unicode=False,
            build=284,
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2(), build=284)],
            old_objects=[(0, 2, "Freddy", props)],
            frames=[frame_chunk(name="Menu",
                                instances=(frame_instance(0, 0, 320, 240),),
                                layers=(("Layer 1", 1.0, 1.0),),
                                compress=True, unicode=False)],
        )
        exe = build_exe(game, unicode=False)
        mfa, notes = self._load(exe)
        self.assertEqual(len(mfa.frames), 1)
        self.assertEqual(len(mfa.frames[0].items), 1)
        obj = mfa.frames[0].items[0]
        self.assertEqual(obj.name, "Freddy")
        self.assertEqual(obj.frames, [0])
        self.assertEqual(len(mfa.frames[0].instances), 1)
        self.assertEqual(mfa.frames[0].name, "Menu")
        self.assertFalse(any("missing object" in n for n in notes))

    def test_old_style_encrypted_inner_chunks(self):
        # Build 284 keys are derived (editor, name, copyright) — the
        # pre-2.5 ordering — and the inner chunks are RC4-encrypted.
        # The key strings must match what build_game_data embeds: its name,
        # its copyright and its editor filename ("game.mfa").
        name = "Five Nights at Freddy's"
        copyright_ = "(c) tests"
        editor = "game.mfa"
        build = 284
        # build 284 keys are derived (editor, name, copyright) — the
        # pre-2.5 ordering.
        key = gamedata._make_key(editor, name, copyright_)
        table = gamedata._init_decryption_table(key)

        def rc4_wrap(payload: bytes):
            return 2, bytes(gamedata._transform_chunk(bytearray(payload),
                                                      table))

        props = object_common_284(frames_per_anim=((0,),))
        game = build_game_data(
            name=name,
            unicode=False,
            build=build,
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2(), build=build)],
            old_objects=[(0, 2, "Freddy", props)],
            frames=[frame_chunk(name="Menu",
                                instances=(frame_instance(0, 0, 320, 240),),
                                transform=rc4_wrap, unicode=False)],
        )
        exe = build_exe(game, unicode=False)
        mfa, notes = self._load(exe)
        self.assertEqual(len(mfa.frames[0].items), 1)
        self.assertEqual(mfa.frames[0].items[0].name, "Freddy")
        self.assertEqual(len(mfa.frames[0].instances), 1)
        self.assertFalse(any("missing object" in n for n in notes))

    def test_mmf2_exe_builds_sprites_sb3(self):
        # End-to-end: an MMF2-shaped EXE must produce a sprite that carries
        # a real PNG costume (not the empty "?" placeholder project).
        props = object_common_284(frames_per_anim=((0, 1),))
        game = build_game_data(
            name="Five Nights at Freddy's",
            unicode=False,
            build=284,
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2(), build=284),
                    image_item_normal(1, 2, 2, _png_pixels_2x2(), build=284)],
            old_objects=[(0, 2, "Freddy", props)],
            frames=[frame_chunk(name="Menu",
                                instances=(frame_instance(0, 0, 320, 240),),
                                compress=True, unicode=False)],
        )
        exe = build_exe(game, unicode=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "FiveNightsatFreddys.exe")
            with open(path, "wb") as fh:
                fh.write(exe)
            result = convert_file(path, os.path.join(tmp, "out.sb3"))
        report = result["report"]
        self.assertFalse(any("missing object" in n for n in report["notes"]))
        with zipfile.ZipFile(io.BytesIO(result["project"])) as zf:
            project = json.loads(zf.read("project.json"))
        sprites = [t for t in project["targets"] if not t["isStage"]]
        freddy = [s for s in sprites if s["name"] == "Menu-Freddy"]
        self.assertEqual(len(freddy), 1)
        costumes = freddy[0]["costumes"]
        self.assertEqual(len(costumes), 2)  # animation loop -> 2 costumes
        for costume in costumes:
            self.assertEqual(costume["dataFormat"], "png")
            self.assertIn(costume["md5ext"], zf.namelist())
        # every target's currentCostume must be a valid 0-based index
        for target in project["targets"]:
            self.assertLess(target["currentCostume"], len(target["costumes"]))

    def test_compressed_frame_chunk(self):
        frame = frame_data(instances=(frame_instance(),))
        comp = zlib.compress(frame)
        packed = struct.pack("<II", len(frame), len(comp)) + comp
        game = build_game_data(
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2())],
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[chunk(13107, packed, flags=1)],
        )
        exe = build_exe(game)
        mfa, _notes = self._load(exe)
        self.assertEqual(len(mfa.frames), 1)
        self.assertEqual(len(mfa.frames[0].instances), 1)

    # -- encryption ---------------------------------------------------------

    def test_decryptor_round_trip(self):
        key = gamedata._make_key("My Game", "(c) tests", "game.mfa")
        self.assertEqual(len(key), 256)
        table = gamedata._init_decryption_table(key)
        payload = bytes(range(256)) * 3
        enc = gamedata._transform_chunk(bytearray(payload), table)
        self.assertNotEqual(bytes(enc), payload)
        dec = gamedata._transform_chunk(enc, table)
        self.assertEqual(bytes(dec), payload)

    def test_encrypted_chunks(self):
        name = "My Game"
        copyright_ = "(c) tests"
        editor = "game.mfa"
        build = 294
        key = gamedata._make_key(name, copyright_, editor)
        table = gamedata._init_decryption_table(key)

        def encrypt(payload: bytes, cid: int, mode3: bool) -> bytes:
            if mode3:
                comp = zlib.compress(payload)
                raw = bytearray(struct.pack("<I", len(comp)) + comp)
                if (cid & 1) == 1 and build > 284:
                    raw[0] ^= (cid & 0xFF) ^ (cid >> 8)
                gamedata._transform_chunk(raw, table)
                return struct.pack("<I", len(payload)) + bytes(raw)
            return bytes(gamedata._transform_chunk(bytearray(payload), table))

        pixels = _png_pixels_2x2()
        images = [image_item_normal(0, 2, 2, pixels, build=build)]
        game = build_game_data(
            name=name,
            build=build,
            images=images,
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[frame_chunk(instances=(frame_instance(),))],
            extra_chunks=(
                chunk(8741, encrypt(wstring("Tester"), 8741, mode3=False),
                      flags=2),                                # author, flag 2
                chunk(8751, encrypt(wstring("game.exe"), 8751, mode3=True),
                      flags=3),                                # target, flag 3
                chunk(26214, encrypt(
                    image_bank(images)[8:], 26214, mode3=True),
                    flags=3),                                  # image bank
            ),
        )
        exe = build_exe(game)
        mfa, notes = self._load(exe)
        self.assertEqual(mfa.author, "Tester")
        self.assertEqual(mfa.path, "game.exe")
        self.assertIn(0, mfa.images)
        self.assertIsNotNone(mfa.images[0].png)
        self.assertFalse(any("could not decode" in n for n in notes))

    # -- errors -------------------------------------------------------------

    def test_not_an_exe(self):
        with self.assertRaises(gamedata.GameDataError):
            gamedata.load_game_data_from_exe(b"just some bytes, not MZ")

    def test_old_mmf15_layout(self):
        # MMF 1.5 EXEs start the appended data with a raw chunk list
        # (first short 0x222C) instead of a PAME header.
        appended = struct.pack("<H", 0x222C) + b"\x00" * 64
        exe = build_exe(appended)
        with self.assertRaises(gamedata.GameDataError) as ctx:
            gamedata.load_game_data_from_exe(exe)
        self.assertIn("no PAME/PAMU", str(ctx.exception))

    def test_cnc_version_rejected(self):
        header = b"PAME" + struct.pack("<HHii", 0x207, 0, 2, 250)
        exe = build_exe(header + last_chunk())
        with self.assertRaises(gamedata.GameDataError) as ctx:
            gamedata.load_game_data_from_exe(exe)
        self.assertIn("MMF 1.5", str(ctx.exception))

    def test_find_game_data_offset(self):
        game = build_game_data()
        exe = build_exe(game, pack_files=[("x.txt", b"x", False)])
        offset = gamedata.find_game_data_offset(exe)
        self.assertIsNotNone(offset)
        self.assertEqual(exe[offset : offset + 4], b"PAMU")
        exe_no_pack = build_exe(game)
        self.assertEqual(gamedata.find_game_data_offset(exe_no_pack), 0x200)
        self.assertIsNone(gamedata.find_game_data_offset(b"MZ" + b"\x00" * 512))

    def test_pe32plus_optional_header_still_converts(self):
        # Real Fusion EXEs (FNaF included) use SizeOfOptionalHeader from
        # the COFF header, which is not always 224.  A PE32+ 240-byte
        # optional header used to make the overlay miss the game data.
        game = build_game_data(
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2())],
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[frame_chunk(instances=(frame_instance(),))],
        )
        pack = build_pack([], unicode=True)
        appended = pack + game
        raw_ptr = 0x300
        dos = bytearray(64)
        dos[0:2] = b"MZ"
        struct.pack_into("<I", dos, 60, 0x40)
        pe = bytearray()
        pe += b"PE\x00\x00"
        pe += struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x102)
        pe += b"\x00" * 240
        pe += b".text".ljust(8, b"\x00")
        pe += struct.pack("<IIII", 0, 0, len(appended), raw_ptr)
        pe += b"\x00" * 24
        exe = bytes(dos) + bytes(pe)
        exe += b"\x00" * (raw_ptr - len(exe)) + appended
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")

    def test_padding_before_pame_is_scanned(self):
        game = build_game_data(
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2())],
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[frame_chunk(instances=(frame_instance(),))],
        )
        padded = b"\x00" * 17 + game
        exe = build_exe(padded)
        mfa, _notes = self._load(exe)
        self.assertEqual(mfa.name, "My Game")

    def test_unconvertible_exe_error_is_ctfak_free(self):
        # A file that is neither a readable pack nor game data must fail
        # WITHOUT demanding the CTFAK setup.
        exe = build_exe(b"\x00" * 64)
        with tempfile.TemporaryDirectory() as tmp:
            exe_path = os.path.join(tmp, "weird.exe")
            with open(exe_path, "wb") as fh:
                fh.write(exe)
            with self.assertRaises(RuntimeError) as ctx:
                convert_file(exe_path)
            msg = str(ctx.exception)
            self.assertIn("built-in readers", msg)
            self.assertIn("--ctfak", msg)
            self.assertNotIn(".NET 6 Desktop Runtime", msg)

    def test_events_chunk_recorded(self):
        frame = frame_chunk(instances=(frame_instance(),),
                            events=b"COMPILED_EVENTS")
        game = build_game_data(
            images=[image_item_normal(0, 2, 2, _png_pixels_2x2())],
            objects_25=[object_common_25()],
            object_names=("Player",),
            frames=[frame],
        )
        exe = build_exe(game)
        _mfa, notes = self._load(exe)
        self.assertTrue(any("compiled event" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
