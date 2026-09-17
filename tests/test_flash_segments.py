"""tools/flash_segments.py — the tables behind the ECU's own flash route.

Brief E6, issues #26 #27 #28 #32.  Every expectation here is a fact recorded
in `re/findings/flash_programming.md` with the instruction that reads it, so a
failure means either the tool or the document drifted.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from common import DUMP, DumpUnchanged, requires_dump

import flash_segments as fs


@requires_dump
class TestDeviceTable(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.d = DUMP.read_bytes()

    def test_three_devices_and_the_on_chip_one_is_index_1(self):
        devs = fs.devices(self.d)
        self.assertEqual(len(devs), 3)
        self.assertEqual((devs[0]["start"], devs[0]["end"]), (0x000000, 0x3FFFFF))
        self.assertEqual((devs[1]["start"], devs[1]["end"]), (0x404000, 0x47FFFF))
        self.assertEqual((devs[2]["start"], devs[2]["end"]), (0xC00000, 0xC7FFFF))

    def test_ram_loader_carries_the_same_ranges(self):
        app = fs.devices(self.d)
        ldr = fs.devices(self.d, fs.DEV_TABLE_LOADER)
        self.assertEqual([(e["start"], e["end"]) for e in app],
                         [(e["start"], e["end"]) for e in ldr])
        # ... but its driver entry points are in internal SRAM, not external.
        for e in ldr:
            for op in e["ops"]:
                self.assertTrue(0x7F8000 <= op < 0x800000, hex(op))

    def test_application_driver_ops_point_into_the_0x804800_image(self):
        for e in fs.devices(self.d):
            # four RAM entry points inside the relocated copy, one in place
            ram = [o for o in e["ops"] if 0x804800 <= o < 0x808688]
            self.assertEqual(len(ram), 4, e)
            self.assertIn(0x082784, e["ops"])


@requires_dump
class TestGeometry(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.d = DUMP.read_bytes()

    def test_five_types_and_their_totals(self):
        g = fs.geometry(self.d)
        self.assertEqual([e["total"] for e in g],
                         [0x400000, 0x200000, 0x200000, 0x80000, 0x200000])

    def test_uc3f_block_map_matches_the_mpc563_array(self):
        """MPC561/563 RM Fig. 21-8: 16K + 48K + 48K + 16K + 6 x 64K."""
        uc3f = fs.geometry(self.d)[fs.UC3F_TYPE]
        blocks = fs.blocks(uc3f["regions"], 0x400000)
        self.assertEqual(len(blocks), 10)
        self.assertEqual(
            [(s, n) for _, s, n in blocks],
            [(0x400000, 0x4000), (0x404000, 0xC000), (0x410000, 0xC000),
             (0x41C000, 0x4000), (0x420000, 0x10000), (0x430000, 0x10000),
             (0x440000, 0x10000), (0x450000, 0x10000), (0x460000, 0x10000),
             (0x470000, 0x10000)])
        # the blocks tile the whole array, exactly once
        self.assertEqual(blocks[-1][1] + blocks[-1][2], 0x480000)

    def test_uc3f_select_masks_are_one_hot_and_cover_every_field(self):
        masks = fs.uc3f_masks(self.d, 10)
        self.assertEqual(masks, [0x0200, 0x0080, 0x0040, 0x0100, 0x0020,
                                 0x0010, 0x0008, 0x0004, 0x0002, 0x0001])
        for m in masks:
            self.assertEqual(bin(m).count("1"), 1)
        # The driver ORs mask<<8 into UC3FCTL.  BLOCK[0:7] occupy bits 16:23
        # (weights 0x8000..0x0100), SBBLOCK[0:1] bits 14:15 (0x20000/0x10000):
        # every shifted mask must land in exactly that 10-bit field.
        for m in masks:
            self.assertTrue(0x00000100 <= (m << 8) <= 0x00020000, hex(m))
            self.assertEqual((m << 8) & ~0x0003FF00, 0, hex(m))

    def test_the_six_ff_fuel_on_chip_hooks_land_in_erasable_blocks(self):
        hooks = {0x42247C: 4, 0x432940: 5, 0x41D40C: 3,
                 0x41A680: 2, 0x41A808: 2, 0x431384: 5}
        blocks = fs.blocks(fs.geometry(self.d)[fs.UC3F_TYPE]["regions"], 0x400000)
        for addr, expect_idx in hooks.items():
            idx = next(i for i, s, n in blocks if s <= addr < s + n)
            self.assertEqual(idx, expect_idx, hex(addr))
            self.assertGreaterEqual(addr, 0x404000)      # never small block 0


@requires_dump
class TestProgrammingKwp(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.d = DUMP.read_bytes()

    def test_thirteen_entries_including_request_download(self):
        t = fs.kwp_prog_table(self.d)
        self.assertEqual(len(t), 13)
        by_sid = {e["sid"]: e for e in t}
        self.assertIn(0x34, by_sid)                      # absent from 0x2B820
        self.assertEqual(by_sid[0x34]["h1"], 0x086A28)
        self.assertEqual(by_sid[0x31]["h1"], 0x088D40)
        self.assertEqual(by_sid[0x36]["h1"], 0x086CDC)

    def test_no_entry_is_session_gated(self):
        for e in fs.kwp_prog_table(self.d):
            self.assertEqual(e["session_mask"], 0xFFFFFFFF)
            self.assertEqual(e["sub"], 0xFF)

    def test_the_level1_lfsr_mask_sits_in_front_of_the_table(self):
        self.assertEqual(fs._u32(self.d, fs.LFSR_MASK_AT), 0x5FBD5DBD)

    def test_whitelist_admits_the_on_chip_flash_and_nothing_below_0x20000(self):
        rows = {(s, e) for s, e, _, _ in fs.DOWNLOAD_WHITELIST}
        self.assertIn((0x404000, 0x47FFFF), rows)
        for start, end in rows:
            self.assertGreaterEqual(start, 0x020000)     # boot block unreachable
            self.assertFalse(start <= 0x403FFF < end and start >= 0x400000)
        # the 0x080000-0x09FFFF row is the calibration alias, not a real target
        self.assertIn((0x080000, 0x09FFFF), rows)


@requires_dump
class TestMode4Segments(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.d = DUMP.read_bytes()

    def test_thirtytwo_contiguous_eeprom_blocks(self):
        segs = fs.mode4_segments(self.d)
        self.assertEqual(len(segs), 32)
        self.assertEqual(segs[0]["eeprom_off"], 0x000)
        self.assertEqual(segs[-1]["eeprom_off"], 0x7E0)
        # blocks tile the device with no gap once each one is rounded up to a
        # whole 0x20-byte EEPROM page (eeprom.md section 3.2)
        for a, b in zip(segs, segs[1:]):
            end = a["eeprom_off"] + a["span"]
            self.assertEqual((end + 0x1F) & ~0x1F, b["eeprom_off"], a["index"])
        last = segs[-1]
        self.assertEqual(last["eeprom_off"] + last["span"], 0x800)

    def test_the_window_only_reaches_the_first_kilobyte(self):
        segs = fs.mode4_segments(self.d)
        inside = [s for s in segs if s["window_start"] < 0x480400]
        self.assertEqual(len(inside), 22)                # blocks 0..21
        # block 10 (the programming record) must be readable over OBD
        blk10 = segs[10]
        self.assertEqual(blk10["eeprom_off"], 0x260)
        self.assertLess(blk10["window_end"], 0x480400)

    def test_it_agrees_with_eeprom_map(self):
        """The window map and the EEPROM map read the same 32 records."""
        import eeprom_map
        blocks = eeprom_map.read_blocks(self.d)
        segs = fs.mode4_segments(self.d)
        self.assertEqual(len(segs), len(blocks))
        for seg, blk in zip(segs, blocks):
            self.assertEqual(seg["eeprom_off"], blk.addr)
            self.assertEqual(seg["length"], blk.length)
            self.assertEqual(seg["flags"], blk.flags)


@requires_dump
class TestCli(DumpUnchanged):
    def test_all_sections_print(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fs.main([str(DUMP), "--all"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        for needle in ("0x404000  0x47FFFF", "RequestDownload",
                       "SBBLOCK[0]", "EEP_CONF"):
            self.assertIn(needle, out)

    def test_json_is_valid(self):
        import json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fs.main([str(DUMP), "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(len(doc["devices"]), 3)
        self.assertEqual(len(doc["uc3f_blocks"]), 10)


if __name__ == "__main__":
    unittest.main()
