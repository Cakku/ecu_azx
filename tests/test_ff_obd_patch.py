"""Brief F6 (#39, optional half): the stock generic-OBD mode-01 stack.

`re/findings/obd.md` says PID 0x52 (ethanol fuel %) **cannot** be added to
`patches/ff_fuel` by writing data words only, because all five (record, PID,
support-mask) lists the mode-01 lookup scans are exactly full and their loop
bounds are immediates in stock code.  Nothing was patched, so this file does
not test a patch; it pins the stock facts that decision rests on, so that a
follow-on brief can trust them and notice at once if they are wrong.

Three layers:

  1. static — the dense class table at 0x0A39B4, the five lists in
     calibration, that every slot is taken, and that PID 0x52 has neither a
     class byte nor a list entry;
  2. dynamic — the real `obd_pid_support_build` (0x5CBE8) and
     `obd_mode01_h1` (0x5D0F4) under `emu.Med9Emu`: the support bitmaps are
     built in RAM from the records' validity bytes, standard PIDs answer, and
     PID 0x52 answers "not supported";
  3. the control that proves *why* — writing only the dense class byte for
     0x52 changes nothing, while class byte **plus** a list slot makes the
     firmware answer `52 <A>` and light the support bit, without a single
     instruction changed.

Layer 3 edits a copy of the image inside the emulator only.  Nothing here
writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

from emu import Med9Emu  # noqa: E402  (tests.common put REPO on sys.path)

# --- the facts under test (re/findings/obd.md sections 2-4) ----------------
MODE01_H1 = 0x05D0F4          # dispatch table 0x2B9B0 +0x08
MODE01_H2 = 0x05CBE8          # dispatch table 0x2B9B0 +0x0C, the bitmap builder
PID_READ = 0x05CED4
CLASS_TBL = 0x0A39B4          # dense, index = PID, 0x59 bytes
CLASS_LEN = 0x59
BITMAP = 0x801215             # r13 + 0x1225, 12 bytes = three 4-byte bitmaps
R2_APP = 0x5C9FF0

# name, ptrs, ids, masks, n, value bytes
LISTS = (
    ("A2", R2_APP - 0x42CC, R2_APP - 0x427C, R2_APP - 0x4268, 20, 1),
    ("A3", R2_APP - 0x4254, R2_APP - 0x4234, R2_APP - 0x422C, 8, 2),
    ("B2", R2_APP - 0x4224, R2_APP - 0x4218, R2_APP - 0x4215, 3, 1),
    ("B3", R2_APP - 0x4210, R2_APP - 0x41F8, R2_APP - 0x41F2, 6, 2),
    ("B5", R2_APP - 0x41EC, R2_APP - 0x41DC, R2_APP - 0x41D8, 4, 4),
)

# one RAM record per list, seeded so that exactly five PIDs are "valid"
RECORDS = {
    0x802235: bytes([0x5A, 0x01]),                    # PID 05 coolant     (A2)
    0x802132: bytes([0x1A, 0xF8, 0x01]),              # PID 0C engine rpm  (A3)
    0x801A58: bytes([0x33, 0x01]),                    # PID 13 O2 present  (B2)
    0x80135A: bytes([0x01, 0x23, 0x01]),              # PID 21 dist w/ MIL (B3)
    0x801360: bytes([0x82, 0x07, 0x65, 0x00, 0x01]),  # PID 01 monitors    (B5)
}
SEEDED = (0x01, 0x05, 0x0C, 0x13, 0x21)

IO_STRUCT = 0x807800
IO_BUF = 0x807A00

# the A2 slot the control experiment steals (PID 0x58, long-term secondary
# fuel trim bank 2) and the RAM record it is pointed at
STEAL_PTR = R2_APP - 0x42CC + 19 * 4
STEAL_ID = R2_APP - 0x427C + 19
STEAL_MASK = R2_APP - 0x4268 + 19
FAKE_REC = 0x807F00


def _cal_off(cpu: int) -> int:
    """File offset of a CPU address (external flash, or the 0x5Cxxxx alias)."""
    return cpu - 0x400000 if cpu >= 0x5C0000 else cpu


def _io(buf: int, length: int) -> bytes:
    s = bytearray(16)
    struct.pack_into(">I", s, 0, buf)
    struct.pack_into(">H", s, 6, length)
    return bytes(s)


def _session(patch=None) -> Med9Emu:
    """A reset emulator with the five records seeded and the bitmaps built."""
    emu = Med9Emu(str(DUMP), r2="app")
    emu.reset()
    for addr, data in RECORDS.items():
        emu.write(addr, data)
    for addr, data in (patch or {}).items():
        emu.write(addr, data)
    emu.call(MODE01_H2, args=[0, 0], reset=False)
    return emu


def _mode01(emu: Med9Emu, pids) -> tuple[int, bytes]:
    """Run the real mode-01 handler; returns (status, response bytes)."""
    emu.write(IO_STRUCT, _io(IO_BUF, len(pids)))
    emu.write(IO_BUF, bytes(pids) + b"\x00" * 24)
    emu.call(MODE01_H1, args=[0, IO_STRUCT], reset=False)
    st = emu.read(IO_STRUCT, 16)
    length = struct.unpack(">H", st[8:10])[0]
    return st[0xA], (emu.read(IO_BUF, length) if length else b"")


@requires_dump
class TestObdTablesStatic(DumpUnchanged):
    """Layer 1: the dump's own bytes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dump = DUMP.read_bytes()
        cls.cls_tbl = cls.dump[CLASS_TBL:CLASS_TBL + CLASS_LEN]

    def _list(self, ptrs, ids, masks, n):
        p = struct.unpack(">%dI" % n, self.dump[_cal_off(ptrs):_cal_off(ptrs) + 4 * n])
        i = self.dump[_cal_off(ids):_cal_off(ids) + n]
        m = self.dump[_cal_off(masks):_cal_off(masks) + n]
        return p, i, m

    def test_class_table_is_dense_and_matches_j1979_lengths(self):
        """Index = PID; the low nibble is the response length class."""
        expect = {0x01: 5, 0x05: 2, 0x0C: 3, 0x10: 3, 0x24: 5, 0x3C: 3, 0x44: 3}
        for pid, want in expect.items():
            self.assertEqual(self.cls_tbl[pid] & 0x0F, want, hex(pid))
        self.assertEqual(sum(1 for b in self.cls_tbl if b), 76)

    def test_every_list_slot_is_taken(self):
        """The reason PID 0x52 is not a data-only addition."""
        seen = {}
        for name, ptrs, ids, masks, n, _ in LISTS:
            p, i, m = self._list(ptrs, ids, masks, n)
            for k in range(n):
                pid = i[k]
                self.assertNotIn(pid, seen, f"{name}[{k}] duplicates PID {pid:#04x}")
                seen[pid] = name
                self.assertNotEqual(self.cls_tbl[pid], 0,
                                    f"{name}[{k}] PID {pid:#04x} has class byte 0")
                self.assertTrue(0x7F8000 <= p[k] < 0x808000,
                                f"{name}[{k}] record {p[k]:#x} is not RAM")
                want_group = 1 if name[0] == "A" else 0
                self.assertEqual((self.cls_tbl[pid] >> 7) & 1, want_group)
                self.assertEqual(self.cls_tbl[pid] & 0x0F, int(name[1]))
        self.assertEqual(len(seen), 41)

    def test_support_masks_are_the_j1979_bits(self):
        for name, ptrs, ids, masks, n, _ in LISTS:
            _, i, m = self._list(ptrs, ids, masks, n)
            for k in range(n):
                self.assertEqual(m[k], 0x80 >> ((i[k] - 1) & 7),
                                 f"{name}[{k}] PID {i[k]:#04x}")

    def test_pid_52_is_absent_everywhere(self):
        self.assertEqual(self.cls_tbl[0x52], 0)
        for _, ptrs, ids, masks, n, _ in LISTS:
            _, i, _m = self._list(ptrs, ids, masks, n)
            self.assertNotIn(0x52, i)

    def test_lists_are_packed_with_no_room_to_grow(self):
        """Only two slack bytes in the whole 0x5C5D24-0x5C5E1B block."""
        spans = []
        for _, ptrs, ids, masks, n, _ in LISTS:
            spans += [(ptrs, 4 * n), (ids, n), (masks, n)]
        spans.sort()
        gaps = sum(nxt - (a + ln) for (a, ln), (nxt, _l) in zip(spans, spans[1:]))
        self.assertEqual(gaps, 2)
        self.assertEqual(spans[0][0], 0x5C5D24)
        self.assertEqual(spans[-1][0] + spans[-1][1] - 1, 0x5C5E1B)

    def test_mode01_table_entry(self):
        entry = self.dump[0x2B9B0:0x2B9B0 + 20]
        self.assertEqual(entry[0], 0x01)                                   # SID
        self.assertEqual(struct.unpack(">I", entry[4:8])[0], 0x50)         # sessions 4, 6
        self.assertEqual(struct.unpack(">I", entry[8:12])[0], MODE01_H1)
        self.assertEqual(struct.unpack(">I", entry[12:16])[0], MODE01_H2)


@requires_dump
class TestObdMode01Emulated(DumpUnchanged):
    """Layer 2: the real handlers out of the dump."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.emu = _session()
        cls.bitmaps = cls.emu.read(BITMAP, 12)

    def test_support_bitmaps_are_built_in_ram_from_the_valid_flags(self):
        self.assertEqual(self.bitmaps.hex(), "8810200180000000" + "00000000")
        for pid in SEEDED:
            byte, mask = (pid - 1) >> 3, 0x80 >> ((pid - 1) & 7)
            self.assertTrue(self.bitmaps[byte] & mask, f"PID {pid:#04x} not advertised")

    def test_bitmap_pids(self):
        self.assertEqual(_mode01(self.emu, [0x00]), (1, bytes.fromhex("0088102001")))
        self.assertEqual(_mode01(self.emu, [0x20]), (1, bytes.fromhex("2080000000")))
        # nothing in 0x41..0x60 is valid, so the 0x20 bitmap's continuation
        # bit is clear and the 0x40 request is refused
        self.assertEqual(_mode01(self.emu, [0x40])[0], 3)

    def test_data_pids_of_all_five_lists(self):
        self.assertEqual(_mode01(self.emu, [0x05]), (1, bytes.fromhex("055a")))
        self.assertEqual(_mode01(self.emu, [0x0C]), (1, bytes.fromhex("0c1af8")))
        self.assertEqual(_mode01(self.emu, [0x13]), (1, bytes.fromhex("1333")))
        self.assertEqual(_mode01(self.emu, [0x21]), (1, bytes.fromhex("210123")))
        self.assertEqual(_mode01(self.emu, [0x01]), (1, bytes.fromhex("0182076500")))

    def test_several_pids_in_one_request(self):
        self.assertEqual(_mode01(self.emu, [0x05, 0x0C]), (1, bytes.fromhex("055a0c1af8")))

    def test_pid_52_is_not_supported_today(self):
        self.assertEqual(_mode01(self.emu, [0x52])[0], 3)


@requires_dump
class TestObdPid52WouldNeedAListSlot(DumpUnchanged):
    """Layer 3: the control that settles brief F6 task 2."""

    def test_class_byte_alone_does_nothing(self):
        emu = _session({CLASS_TBL + 0x52: bytes([0x82])})
        self.assertEqual(emu.read(BITMAP, 12)[10], 0x00)
        self.assertEqual(_mode01(emu, [0x52])[0], 3)

    def test_class_byte_plus_a_list_slot_answers_pid_52(self):
        """Stealing A2 slot 19 is *not* a shippable patch - it removes PID 0x58.

        It is here to prove the mechanism end to end: with a slot, one class
        byte plus three data bytes plus a RAM record are enough, and both the
        answer and the support bitmap follow, with no instruction changed.
        """
        emu = _session({
            CLASS_TBL + 0x52: bytes([0x82]),
            STEAL_ID: bytes([0x52]),
            STEAL_MASK: bytes([0x40]),
            STEAL_PTR: struct.pack(">I", FAKE_REC),
            FAKE_REC: bytes([0x85, 0x01]),
        })
        bm = emu.read(BITMAP, 12)
        self.assertEqual(bm[10] & 0x40, 0x40, "PID 0x52 support bit not set")
        self.assertEqual(bm[7] & 0x01, 0x01, "PID 0x40 continuation bit not set")
        self.assertEqual(_mode01(emu, [0x52]), (1, bytes.fromhex("5285")))
        self.assertEqual(_mode01(emu, [0x40]), (1, bytes.fromhex("4000004000")))
        # 0x85 = 133 -> 133 * 100 / 255 = 52.2 % ethanol
        self.assertEqual(round(0x85 * 100 / 255, 1), 52.2)


if __name__ == "__main__":
    unittest.main()
