"""Tests for the native F2.5 EXE pack extractor and CTFAK command building.

We synthesize a small file that follows the layout CTFAK expects
(MZ + PE + ``.extra`` section + PackData) and verify the extractor,
including a full ``convert_file`` run that goes through the EXE path.
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

from cts2 import ctfak, exe_pack
from cts2.converter import convert_file

HERE = os.path.dirname(__file__)
FIXTURES = os.path.join(HERE, "fixtures")


# --------------------------------------------------------------------------
# synthetic EXE builder
# --------------------------------------------------------------------------

def _pe_header(section_count: int, section_names: list, raw_ptrs: list,
               raw_sizes: list) -> bytes:
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 60, 0x40)  # e_lfanew
    pe = bytearray()
    pe += b"PE\x00\x00"
    # COFF file header: machine, nsections, timestamp, ptr_symtab,
    # num_symbols, opt_hdr_size, characteristics (20 bytes)
    pe += struct.pack("<HHIIIHH", 0x14C, section_count, 0, 0, 0, 96, 0x102)
    # optional header + 16 data directories, matching the reader's fixed
    # skip (28+68 then 16*8)
    pe += b"\x00" * (96 + 128)
    for name, raw_ptr, raw_size in zip(section_names, raw_ptrs, raw_sizes):
        pe += name.ljust(8, b"\x00")
        pe += struct.pack("<IIII", 0, 0, raw_size, raw_ptr)
        pe += b"\x00" * 24  # rest of the 40-byte section header
    return bytes(dos) + bytes(pe)


def _pack_item(name: str, data: bytes, compressed: bool, unicode: bool) -> bytes:
    raw = name.encode("utf-16-le") if unicode else name.encode("latin-1")
    out = struct.pack("<H", len(name))
    out += raw
    payload = zlib.compress(data) if compressed else data
    out += struct.pack("<ii", 0x1234, len(payload))
    if compressed:
        out += struct.pack("<H", exe_pack.BINGO_MARKER)
    out += payload
    return out


def _pack_body(pack_files, unicode: bool) -> bytes:
    """32-byte pack header + items, returning (body, game_data)."""
    magic = b"PAMU" if unicode else b"PAME"
    items = b"".join(_pack_item(n, d, c, unicode) for n, d, c in pack_files)
    pack = bytearray()
    pack += b"\x00" * 8                       # 8-byte header
    # dataSize: the game-data magic must sit at start + dataSize - 32
    data_size = 64 + len(items)
    pack += struct.pack("<II", 32, data_size)
    pack += struct.pack("<IiiI", 2, 0, 0, len(pack_files))
    pack += items
    game_data = magic + b"\x00" * 8  # PAMU/PAME + (fake) game data
    return bytes(pack), game_data


def build_synthetic_exe(pack_files, section=b".extra", unicode=True) -> bytes:
    """pack_files: list of (name, data, compressed)."""
    body, game_data = _pack_body(pack_files, unicode)
    pack_offset = 0x200
    hdr = _pe_header(1, [section], [pack_offset], [len(body) + len(game_data)])
    return hdr + b"\x00" * (pack_offset - len(hdr)) + body + game_data


def build_fallback_exe(pack_files) -> bytes:
    """No ``.extra`` section: the pack starts at raw_ptr + raw_size of the
    last section (appended after the section's raw data, like real exes)."""
    body, game_data = _pack_body(pack_files, True)
    code = b"\xCC" * 128
    pack_offset = 0x200
    # section raw data is just the "code"; pack follows the section
    hdr = _pe_header(1, [b".text"], [pack_offset], [len(code)])
    return hdr + b"\x00" * (pack_offset - len(hdr)) + code + body + game_data


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

class TestExePack(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXTURES, "minimal.mfa"), "rb") as fh:
            self.mfa_data = fh.read()

    def test_unpack_extra_section(self):
        exe = build_synthetic_exe([
            ("readme.txt", b"hello pack", False),
            ("game.mfa", self.mfa_data, True),   # zlib to exercise the marker
            ("plain.dat", bytes(range(32)), False),
        ])
        start = exe_pack.find_pack_start(exe)
        self.assertEqual(start, 0x200)
        magic, version, files = exe_pack.read_pack(exe, start)
        self.assertEqual(magic, "PAMU")
        self.assertEqual(version, 2)
        self.assertEqual([f.name for f in files], ["readme.txt", "game.mfa", "plain.dat"])
        self.assertEqual(files[0].data, b"hello pack")
        self.assertFalse(files[0].compressed)
        self.assertEqual(files[1].data, self.mfa_data)
        self.assertTrue(files[1].compressed)
        self.assertEqual(files[2].data, bytes(range(32)))

    def test_fallback_last_section(self):
        exe = build_fallback_exe([("game.mfa", self.mfa_data, False)])
        start = exe_pack.find_pack_start(exe)
        self.assertEqual(start, 0x200 + 128)
        magic, _version, files = exe_pack.read_pack(exe, start)
        self.assertEqual(magic, "PAMU")
        self.assertEqual(files[0].data, self.mfa_data)

    def test_non_unicode_pack(self):
        exe = build_synthetic_exe([("notes.txt", b"abc", False)], unicode=False)
        magic, _v, files = exe_pack.extract_pack(exe)
        self.assertEqual(magic, "PAME")
        self.assertEqual(files[0].name, "notes.txt")
        self.assertEqual(files[0].data, b"abc")

    def test_rejects_garbage(self):
        with self.assertRaises(exe_pack.PackError):
            exe_pack.find_pack_start(b"not an executable at all")
        with self.assertRaises(exe_pack.PackError):
            exe_pack.extract_pack(b"MZ" + b"\x00" * 200)

    def test_find_mfa_prefers_magic(self):
        exe = build_synthetic_exe([
            ("decoy.mfa", b"not an mfa file", False),
            ("blob.bin", self.mfa_data, False),  # MFU2 magic wins
        ])
        name, data = exe_pack.extract_mfa_from_exe(exe)
        self.assertEqual(name, "blob.bin")
        self.assertEqual(data, self.mfa_data)

    def test_no_mfa_returns_none(self):
        exe = build_synthetic_exe([("a.txt", b"x", False)])
        self.assertIsNone(exe_pack.extract_mfa_from_exe(exe))

    def test_convert_synthetic_exe_to_sb3(self):
        exe = build_synthetic_exe([
            ("readme.txt", b"hello pack", False),
            ("game.mfa", self.mfa_data, True),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            exe_path = os.path.join(tmp, "game.exe")
            with open(exe_path, "wb") as fh:
                fh.write(exe)
            out = os.path.join(tmp, "game.sb3")
            result = convert_file(exe_path, out)
            self.assertTrue(os.path.exists(out))
            with zipfile.ZipFile(io.BytesIO(result["project"])) as z:
                pj = json.loads(z.read("project.json"))
            self.assertTrue(any(t["name"] == "Stage" for t in pj["targets"]))
            notes = result["report"].get("notes", [])
            self.assertTrue(any("without CTFAK" in n for n in notes))

    def test_pack_dump_and_summary(self):
        exe = build_synthetic_exe([
            ("a.txt", b"1", False),
            ("game.mfa", self.mfa_data, False),
        ])
        summary = exe_pack.pack_summary(exe)
        self.assertEqual(summary["magic"], "PAMU")
        self.assertEqual(summary["mfa_entry"], "game.mfa")
        self.assertEqual(len(summary["files"]), 2)
        with tempfile.TemporaryDirectory() as tmp:
            files = exe_pack.dump_pack(exe, tmp)
            self.assertEqual(len(files), 2)
            self.assertTrue(os.path.exists(os.path.join(tmp, "game.mfa")))


class TestCtfak(unittest.TestCase):
    def test_command_headless_ctfak2(self):
        cmd = ctfak.ctfak_command(r"C:\CTFAK\CTFAK.Cli.exe", r"C:\games\demo.exe")
        self.assertEqual(cmd[0], r"C:\CTFAK\CTFAK.Cli.exe")
        self.assertIn("-path", cmd)
        self.assertIn("-parameters", cmd)
        self.assertIn("-tool", cmd)
        self.assertIn("Export as MFA", cmd)
        self.assertIn("-closeonfinish", cmd)
        self.assertEqual(cmd[cmd.index("-path") + 1], os.path.abspath(r"C:\games\demo.exe"))

    def test_command_generic_fallback(self):
        cmd = ctfak.ctfak_command("some_tool", "demo.exe")
        self.assertEqual(cmd, ["some_tool", os.path.abspath("demo.exe")])

    def test_find_via_env(self):
        fake = os.path.join(tempfile.gettempdir(), "fake-ctfak-bin", "CTFAK.Cli.exe")
        os.makedirs(os.path.dirname(fake), exist_ok=True)
        with open(fake, "wb") as fh:
            fh.write(b"MZ")
        old = os.environ.get("CTFAK_BIN")
        os.environ["CTFAK_BIN"] = fake
        try:
            self.assertEqual(ctfak.find_ctfak_binary(), fake)
            status = ctfak.status()
            self.assertTrue(status["found"])
            self.assertEqual(status["path"], fake)
        finally:
            if old is None:
                os.environ.pop("CTFAK_BIN", None)
            else:
                os.environ["CTFAK_BIN"] = old

    def test_missing_ctfak_error_has_guidance(self):
        old = os.environ.get("CTFAK_BIN")
        os.environ.pop("CTFAK_BIN", None)
        os.environ.pop("CTS2_CTFAK_DIR", None)
        # make sure no bundled/PATH candidate exists in this sandbox
        try:
            with self.assertRaises(ctfak.CtfakNotFoundError) as ctx:
                ctfak.exe_to_mfa("some-game.exe")
            msg = str(ctx.exception)
            self.assertIn("CTFAK", msg)
            self.assertIn(".NET 6", msg)
            self.assertIn("CTFAK_BIN", msg)
        finally:
            if old is not None:
                os.environ["CTFAK_BIN"] = old


if __name__ == "__main__":
    unittest.main()
