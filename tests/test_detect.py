"""Tests for content-based input detection and the no-.mfa-needed workflow.

The user-facing promise: point the tool at the game's .exe (or an extracted
game *folder*) and get an .sb3 — an .mfa file is never required.  These
tests pin that promise: extension-based routing is gone, files are routed
by their first bytes, and folders are scanned for the game automatically.
"""
from __future__ import annotations

import io
import os
import struct
import tempfile
import unittest
import zipfile

from cts2 import detect
from cts2.converter import convert_bytes, convert_file, iter_folder_candidates

try:  # tests/ on sys.path when running `unittest discover -s tests`
    from exebuilder import (
        build_exe,
        build_game_data,
        frame_chunk,
        frame_instance,
        image_item_normal,
        object_common_25,
    )
except ImportError:  # running from the repo root
    from tests.exebuilder import (
        build_exe,
        build_game_data,
        frame_chunk,
        frame_instance,
        image_item_normal,
        object_common_25,
    )


def _pixels() -> bytes:
    """2x2 24bpp (mode 4) BGR rows: red/green then blue/white."""
    return bytes([
        0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00,
        0xFF, 0x00, 0x00, 0xFF, 0xFF, 0xFF,
    ])


def _game_data(name="My Game", unicode=True, build=294) -> bytes:
    images = [image_item_normal(0, 2, 2, _pixels(), build=build)]
    return build_game_data(
        name=name,
        unicode=unicode,
        build=build,
        images=images,
        objects_25=[object_common_25(frames_per_anim=((0,),))],
        object_names=("Player",),
        frames=[frame_chunk(name="Frame 1", instances=(frame_instance(),),
                            layers=(("Layer 1", 1.0, 1.0),))],
        frame_handles=(0,),
    )


def _exe(name="My Game", unicode=True, build=294) -> bytes:
    return build_exe(_game_data(name, unicode, build), pack_files=[])


def _sb3_names(result) -> set:
    with zipfile.ZipFile(io.BytesIO(result["project"])) as zf:
        return set(zf.namelist())


class TestDetect(unittest.TestCase):
    def test_detect_exe(self):
        self.assertEqual(detect.detect_bytes(b"MZ\x90\x00junk"), detect.KIND_EXE)

    def test_detect_mfa(self):
        self.assertEqual(detect.detect_bytes(b"MFU2...."), detect.KIND_MFA)
        self.assertEqual(detect.detect_bytes(b"MFA2...."), detect.KIND_MFA)

    def test_detect_raw_game_data(self):
        self.assertEqual(detect.detect_bytes(b"PAME...."), detect.KIND_GAMEDATA)
        self.assertEqual(detect.detect_bytes(b"PAMU...."), detect.KIND_GAMEDATA)

    def test_detect_unknown(self):
        self.assertEqual(detect.detect_bytes(b"RIFF...."), detect.KIND_UNKNOWN)
        self.assertEqual(detect.detect_bytes(b""), detect.KIND_UNKNOWN)

    def test_detect_file_ignores_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mystery.dat")
            with open(path, "wb") as fh:
                fh.write(_exe())
            self.assertEqual(detect.detect_file(path), detect.KIND_EXE)


class TestConvertWithoutMfa(unittest.TestCase):
    def test_exe_with_wrong_extension_still_converts(self):
        """A renamed/extension-less game .exe converts by content."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "FiveNightsatFreddys")  # no extension
            with open(path, "wb") as fh:
                fh.write(_exe())
            out = os.path.join(tmp, "out.sb3")
            result = convert_file(path, out)
            self.assertIn("project.json", _sb3_names(result))
            self.assertTrue(os.path.exists(out))

    def test_dat_named_file_with_exe_content_converts(self):
        """A .dat that is really a game .exe converts (no CTFAK)."""
        result = convert_bytes(_exe(), "fnafer.dat")
        self.assertIn("project.json", _sb3_names(result))

    def test_raw_gamedata_file_converts(self):
        """A bare PAME/PAMU data file (no PE wrapper) converts directly."""
        result = convert_bytes(_game_data(), "gamedata.dat")
        self.assertIn("project.json", _sb3_names(result))
        notes = result["report"].get("notes", [])
        self.assertTrue(any("PAME/PAMU" in n for n in notes))

    def test_unknown_bytes_get_helpful_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            convert_bytes(b"just some junk bytes here", "mystery.bin")
        msg = str(ctx.exception)
        self.assertIn(".exe", msg)
        self.assertIn("no .mfa", msg)


class TestFolderInput(unittest.TestCase):
    def test_folder_scan_picks_the_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Decoys that ship with a real game folder.
            with open(os.path.join(tmp, "readme.txt"), "wb") as fh:
                fh.write(b"hello")
            with open(os.path.join(tmp, "sound.wav"), "wb") as fh:
                fh.write(b"RIFF....")
            # A subfolder, like Steam's engineering/ folders.
            os.mkdir(os.path.join(tmp, "extras"))
            with open(os.path.join(tmp, "extras", "notes"), "wb") as fh:
                fh.write(b"junk")
            game = os.path.join(tmp, "FiveNightsatFreddys.exe")
            with open(game, "wb") as fh:
                fh.write(_exe())

            self.assertEqual(iter_folder_candidates(tmp)[0], game)

            out = os.path.join(tmp, "fnaf.sb3")
            result = convert_file(tmp, out)
            self.assertIn("project.json", _sb3_names(result))
            notes = result["report"].get("notes", [])
            self.assertTrue(
                any("FiveNightsatFreddys.exe" in n for n in notes),
                f"notes should name the chosen file: {notes}",
            )

    def test_folder_scan_prefers_exe_over_big_dat(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "huge_assets.dat"), "wb") as fh:
                fh.write(b"junk" * 1000)
            game = os.path.join(tmp, "game.exe")
            with open(game, "wb") as fh:
                fh.write(_exe())
            self.assertEqual(iter_folder_candidates(tmp)[0], game)

    def test_folder_without_game_errors_helpfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "only_a_text_file.txt"), "wb") as fh:
                fh.write(b"nothing here")
            with self.assertRaises(RuntimeError) as ctx:
                convert_file(tmp, os.path.join(tmp, "x.sb3"))
            self.assertIn(".exe", str(ctx.exception))

    def test_empty_folder_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                convert_file(tmp, os.path.join(tmp, "x.sb3"))
            self.assertIn("no game found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
