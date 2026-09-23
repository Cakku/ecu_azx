"""Briefs F6 and G1 (#39, optional half): generic-OBD mode 01, and PID 0x52.

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

Layer 3 edits a copy of the image inside the emulator only.

Brief G1 (2026-09-23) then IMPLEMENTED the design on the B2 list in
`patches/ff_fuel` (re/findings/obd.md section 9), and the layers below the
F6 ones prove it:

  4. the free-calibration survey that forced the fallback placement, and the
     facts the seven edits rest on (every reader of the B2 arrays, the group
     bit of the class byte, the blank block 0x160000);
  5. the applied image: the seven words, the class byte 0x02, the relocated
     list, the record pointer the linker resolved, and a clean bindiff;
  6. the two proofs, driving the real `obd_pid_support_build` and
     `obd_mode01_h1` of the PATCHED image with all 41 stock records valid:
     DISABLED (`ff_pid52_enable` = 0) every PID 0x00-0x58 answers byte for
     byte as on the stock image and the whole SRAM matches except our own
     block; ENABLED the only differences are bit 0x40 of 0x80121F, the 0x40
     answer and `01 52` = `52 A` with A = round(E% * 255 / 100);
  7. the record produced by the real 10 ms tick against
     `emu/models/flexfuel.py`, and `01 52` over the KWP route through
     `logging/ecu_sim.py`.

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import struct
import unittest

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

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


# ===========================================================================
# Brief G1 (2026-09-23): PID 0x52 on the grown B2 list, patches/ff_fuel
# ===========================================================================
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import bindiff  # noqa: E402  (tests.common put tools/ on sys.path)
import find_abs_refs  # noqa: E402
import med9lib as m  # noqa: E402
import patch_apply  # noqa: E402

from tests import test_ff_fuel_patch as tff  # noqa: E402

sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))
import ffcal001  # noqa: E402
from emu.models import flexfuel as ff  # noqa: E402

FF_FUEL = REPO / "patches" / "ff_fuel"
PATCH_RAM = 0x7FFB00
OFF_OBD52 = 0x4C
REC52 = PATCH_RAM + OFF_OBD52
STATE_LEN = 0x50
TASK_STACK = 0x7FF768
SRAM_START, SRAM_LEN = 0x7F8000, 0x10000

#: The seven stock-instruction edits: site -> (stock word, patched word, what).
EDITS = {
    0x5CCF4: ("3882bddc", "3c800016", "addi r4,r2,-0x4224 -> lis r4,0x16"),
    0x5CD0C: ("3982bde8", "3d82ffba", "addi r12,r2,-0x4218 -> addis r12,r2,-0x46"),
    0x5CD40: ("3922bdeb", "3d2dff97", "addi r9,r2,-0x4215 -> addis r9,r13,-0x69"),
    0x5CD5C: ("2c030003", "2c030004", "cmpwi r3,3 -> cmpwi r3,4"),
    0x5CFD4: ("38c2bde8", "3cc2ffba", "addi r6,r2,-0x4218 -> addis r6,r2,-0x46"),
    0x5CFE4: ("3982bddc", "3d800016", "addi r12,r2,-0x4224 -> lis r12,0x16"),
    0x5D010: ("2c080003", "2c080004", "cmpwi r8,3 -> cmpwi r8,4"),
}
NEW_PTRS, NEW_IDS, NEW_MASKS = 0x160000, 0x169FF0, 0x16FFF0
FREE_BLOCK = (0x160000, 0x170000)
R13_APP = 0x7FFFF0
B2_STOCK = LISTS[2]
BITMAP_52 = BITMAP + 10          # (0x52 - 1) >> 3 = 10 -> 0x80121F
MASK_52 = 0x40

#: Every run of 0xFF of 24 bytes or more inside the r2 window, and why none
#: of them is free (re/findings/obd.md section 9.1): either a code site that
#: forms its address r2-relatively, or the calibration segment header.
FF_RUNS = {
    (0x5C2020, 0x5C2080): "header",
    (0x5C20C0, 0x5C2180): "header",
    (0x5C2188, 0x5C21C0): "header",
    (0x5C421A, 0x5C4232): 0x0FE4BC,     # addi r30,r2,-0x5dd6 -> 0x5C421A
    (0x5C61B8, 0x5C61EA): None,         # the body of the 5x5 map at 0x5C61A0
    (0x5C6AF6, 0x5C6B26): 0x430498,     # addi r3,r2,-0x34fa -> 0x5C6AF6
    (0x5C8262, 0x5C82A2): 0x0F5BB8,     # addi r3,r2,-0x1d8e -> 0x5C8262
}
MAP_5X5 = (0x5C61A0, 0x42C248)          # the map header, and who passes it
CAL_HEADER = (0x5C2000, 0x5C2240)       # its own Bosch block, desc 0x1C3310


def _exts16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _code_words(data):
    """(cpu, word) for every aligned word of the two code regions."""
    for base, length in ((0x000000, 0x1C0000), (0x404000, 0x07C000)):
        for off in range(0, length, 4):
            fo = m.cpu_to_file(base + off)
            yield base + off, int.from_bytes(data[fo:fo + 4], "big")


def _r2_target(word: int):
    """The address an `addi`/D-form with rA = r2 forms under the app base."""
    op = word >> 26
    if op in (14, 32, 34, 36, 38, 40, 42, 44) and ((word >> 16) & 0x1F) == 2:
        return (R2_APP + _exts16(word & 0xFFFF)) & 0xFFFFFFFF
    return None


def _ff_runs(data, lo=0x5C2010, hi=0x5D1FF0, min_len=24):
    runs, a = [], lo
    while a < hi:
        if data[m.cpu_to_file(a)] == 0xFF:
            b = a
            while b < hi and data[m.cpu_to_file(b)] == 0xFF:
                b += 1
            if b - a >= min_len:
                runs.append((a, b))
            a = b
        else:
            a += 1
    return runs


@requires_dump
class TestFreeCalibrationSurvey(DumpUnchanged):
    """Layer 4a: why the grown list is NOT in calibration (obd.md 9.1)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = DUMP.read_bytes()

    def _word(self, cpu: int) -> int:
        fo = m.cpu_to_file(cpu)
        return int.from_bytes(self.data[fo:fo + 4], "big")

    def test_the_window_holds_exactly_these_ff_runs(self):
        self.assertEqual(sorted(_ff_runs(self.data)), sorted(FF_RUNS))

    def test_every_run_is_referenced_or_in_the_segment_header(self):
        for (lo, hi), why in FF_RUNS.items():
            with self.subTest(run=hex(lo)):
                if why == "header":
                    self.assertTrue(CAL_HEADER[0] <= lo and hi <= CAL_HEADER[1])
                elif why is None:
                    # 25 u16 cells of the 5x5 map whose header sits in front
                    hdr, site = MAP_5X5
                    fo = m.cpu_to_file(hdr)
                    self.assertEqual(self.data[fo:fo + 4], bytes.fromhex("00050005"))
                    self.assertEqual(hdr + 4 + 2 * 5 + 2 * 5, lo)
                    self.assertEqual(hi - lo, 25 * 2)
                    self.assertEqual(_r2_target(self._word(site)), hdr)
                else:
                    self.assertEqual(_r2_target(self._word(why)), lo,
                                     f"{why:#x} does not form {lo:#x}")

    def test_the_second_table_in_the_last_run_is_live_too(self):
        self.assertEqual(_r2_target(self._word(0x0F5BCC)), 0x5C8282)

    def test_nothing_else_reads_the_b2_arrays(self):
        """Five r2-relative sites, the seven edits cover all of them."""
        _, ptrs, ids, masks, n, _ = B2_STOCK
        lo, hi = ptrs, masks + n
        hits = sorted((cpu, t) for cpu, w in _code_words(self.data)
                      if (t := _r2_target(w)) is not None and lo <= t < hi)
        self.assertEqual(hits, [(0x5CCF4, ptrs), (0x5CD0C, ids), (0x5CD40, masks),
                                (0x5CFD4, ids), (0x5CFE4, ptrs)])

    def test_the_free_block_is_blank_and_unreferenced(self):
        lo, hi = FREE_BLOCK
        self.assertEqual(set(self.data[lo:hi]), {0xFF})
        refs = [t for _i, _k, t, _kind in find_abs_refs.resolve(self.data)
                if lo <= t < hi]
        self.assertEqual(refs, [])

    def test_ff_counter_owns_the_one_lis_word_of_the_blob_block(self):
        """Why the list is not in 0x150000-0x15FFFF (obd.md 9.2)."""
        fc = json.loads((REPO / "patches" / "ff_counter" / "patch.json").read_text())
        self.assertEqual(int(fc["build"]["flash"], 0), 0x150000)

    def test_the_class_byte_bit_7_selects_the_list_group(self):
        """0x5CEE4 rlwinm. r10,r8,0,24,24 / beq 0x5CFB0 -> B lists (obd.md 9.3)."""
        self.assertEqual(self._word(0x5CEE4), 0x550A0631)
        word = self._word(0x5CEE8)
        self.assertEqual(word >> 26, 16)
        self.assertEqual(0x5CEE8 + _exts16(word & 0xFFFC), 0x5CFB0)
        cls_tbl = self.data[CLASS_TBL:CLASS_TBL + CLASS_LEN]
        for pid in (0x13, 0x1C, 0x30):        # the three stock B2 PIDs
            self.assertEqual(cls_tbl[pid], 0x02, hex(pid))

    def test_the_seven_sites_hold_the_stock_words(self):
        for site, (old, _new, what) in EDITS.items():
            with self.subTest(site=hex(site), what=what):
                self.assertEqual(f"{self._word(site):08x}", old)

    def test_the_one_instruction_forms_reach_the_block(self):
        self.assertEqual((0x16 << 16), NEW_PTRS)
        self.assertEqual(R2_APP + (-0x46 << 16), NEW_IDS)
        self.assertEqual(R13_APP + (-0x69 << 16), NEW_MASKS)
        for a in (NEW_PTRS, NEW_IDS, NEW_MASKS):
            self.assertTrue(FREE_BLOCK[0] <= a < FREE_BLOCK[1])


# ------------------------------------------------------- 5. the applied image
@requires_dump
class G1Base(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.data, cls.report, cls.warnings = patch_apply.apply_patch(DUMP, FF_FUEL)
        cls.image = cls.tmp / "ff_fuel.bin"
        cls.image.write_bytes(bytes(cls.data))
        cls.syms = {k: int(v, 0) for k, v in tff.load_patch()["build"]["symbols"].items()}
        cls.params = ffcal001.load_params(FF_FUEL / "ffcal001.json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def word(self, cpu: int) -> int:
        fo = m.cpu_to_file(cpu)
        return int.from_bytes(bytes(self.data[fo:fo + 4]), "big")

    def cal(self, enable: int) -> bytes:
        return ffcal001.build(dict(self.params, ff_pid52_enable=enable))

    @classmethod
    def tick_state(cls, enable: int, e_pct: int) -> tuple[bytes, bytes]:
        """The 0x50-byte block the REAL 10 ms tick leaves behind."""
        emu = Med9Emu(cls.image)
        emu.reset()
        cal = ffcal001.build(dict(cls.params, ff_pid52_enable=enable))
        emu.write(tff.CAL_BASE, cal)
        mdl = ff.FlexFuelModel(tff.cal_from_block(cal))
        mdl.state.e_filt = e_pct * 16
        mdl.state.hold_ticks = 1000          # FAULT hold: the tick keeps e_filt
        mdl.seal()
        emu.write(PATCH_RAM, mdl.full_bytes())
        tff.EmuBase.no_frame(emu)
        res = emu.call(cls.syms["ff_fuel_hook_b"], regs={"r1": TASK_STACK}, reset=False)
        assert res.ok, (res.stop_reason, res.issues)
        return emu.read(PATCH_RAM, STATE_LEN), cal


class TestPid52Applied(G1Base):
    def test_it_applies_cleanly_and_bindiff_agrees(self):
        self.assertTrue(self.report["ok"], self.report["issues"])
        self.assertEqual(self.report["bytes"]["unexpected"], 0)
        ranges, rep = bindiff.diff(m.load_dump(str(DUMP)), m.load_dump(str(self.image)),
                                   str(FF_FUEL / "patch.json"))
        self.assertTrue(rep["ok"], rep["issues"])
        self.assertEqual(rep["counts"]["unexpected"], 0)

    def test_the_seven_words_are_in_place(self):
        for site, (_old, new, what) in EDITS.items():
            with self.subTest(site=hex(site), what=what):
                self.assertEqual(f"{self.word(site):08x}", new)

    def test_the_class_byte_is_02(self):
        self.assertEqual(self.data[CLASS_TBL + 0x52], 0x02)

    def test_the_relocated_list(self):
        _, ptrs, ids, masks, n, _ = B2_STOCK
        stock = DUMP.read_bytes()
        so = lambda a: _cal_off(a)  # noqa: E731
        want_ptrs = struct.unpack(">3I", stock[so(ptrs):so(ptrs) + 12]) + (REC52,)
        got_ptrs = struct.unpack(">4I", bytes(self.data[NEW_PTRS:NEW_PTRS + 16]))
        self.assertEqual(got_ptrs, want_ptrs)
        self.assertEqual(bytes(self.data[NEW_IDS:NEW_IDS + 4]),
                         stock[so(ids):so(ids) + 3] + b"\x52")
        self.assertEqual(bytes(self.data[NEW_MASKS:NEW_MASKS + 4]),
                         stock[so(masks):so(masks) + 3] + bytes([MASK_52]))
        self.assertEqual(MASK_52, 0x80 >> ((0x52 - 1) & 7))
        # the stock arrays are left exactly as they were
        self.assertEqual(bytes(self.data[so(ptrs):so(masks) + n]),
                         stock[so(ptrs):so(masks) + n])

    def test_the_record_pointer_is_the_linkers(self):
        self.assertEqual(self.syms["ff_obd_pid52_rec"], self.syms["ff_state"] + OFF_OBD52)
        self.assertEqual(self.syms["ff_obd_pid52_rec"], REC52)
        self.assertEqual(ff.OFF_OBD52, OFF_OBD52)
        self.assertIn("ff_obd_pid52_rec", tff.load_patch()["build"]["ram_symbols"])

    def test_every_new_range_sits_in_a_checksum_block_apply_fixes(self):
        blocks = {"0x0A0070": (0x058000, 0x060000), "0x0A0100": (0x0A0000, 0x0A8000),
                  "0x0A0260": (0x160000, 0x170000)}
        for site in EDITS:
            self.assertTrue(blocks["0x0A0070"][0] <= site < blocks["0x0A0070"][1])
        self.assertTrue(blocks["0x0A0100"][0] <= CLASS_TBL + 0x52 < blocks["0x0A0100"][1])
        for a in (NEW_PTRS, NEW_IDS, NEW_MASKS):
            self.assertTrue(blocks["0x0A0260"][0] <= a < blocks["0x0A0260"][1])
        import checksum as cs
        self.assertTrue(cs.verify(m.load_dump(str(self.image)), quiet=True))

    def test_the_blob_ends_well_before_the_list_block(self):
        build = tff.load_patch()["build"]
        self.assertLess(int(build["flash"], 0) + build["blob_size"], FREE_BLOCK[0])

    def test_ffcal001_ships_the_feature_off(self):
        off = m.cpu_to_file(tff.CAL_BASE)
        blk = bytes(self.data[off:off + ffcal001.LENGTH])
        ffcal001.check(blk)
        self.assertEqual(struct.unpack_from(">HH", blk, 0x08), (5, 0x014E))
        self.assertEqual(blk[0x14A], 0, "ff_pid52_enable must ship 0")

    def test_the_hook_table_is_still_eight_words(self):
        """The edits are build.data, not hooks (README, brief G1)."""
        build = tff.load_patch()["build"]
        self.assertEqual(len(build["hooks"]), 8)
        self.assertEqual(sum(bool(h.get("onchip_edit")) for h in build["hooks"]), 7)
        sites = {int(d["addr"], 0) for d in build["data"]}
        self.assertTrue(set(EDITS) <= sites)
        self.assertEqual(sum("on-chip flash" in w for w in self.warnings), 7)


# --------------------------------------------------------- 6. the two proofs
def _all_records(dump: bytes) -> dict:
    """A RAM record for every one of the 41 stock list entries, all valid."""
    recs = {}
    for name, ptrs, ids, _masks, n, width in LISTS:
        p = struct.unpack(">%dI" % n, dump[_cal_off(ptrs):_cal_off(ptrs) + 4 * n])
        i = dump[_cal_off(ids):_cal_off(ids) + n]
        for k in range(n):
            val = bytes(((i[k] * 7 + j * 29 + 3) & 0xFF) for j in range(width))
            recs[p[k]] = val + b"\x01"
    return recs


QUERIES = ([[p] for p in range(0x59)]
           + [[0x05, 0x0C], [0x52, 0x05], [0x01, 0x52, 0x0C, 0x13, 0x21, 0x41],
              [0x00, 0x20, 0x40]])


class TestPid52TwoProofs(G1Base):
    """Layer 6: stock vs patched, the real handlers, all 41 PIDs valid."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.records = _all_records(DUMP.read_bytes())
        cls.stock = cls._run(DUMP, None, None)

    @classmethod
    def _run(cls, image, state, cal):
        emu = Med9Emu(str(image), r2="app")
        emu.reset()
        if cal is not None:
            emu.write(tff.CAL_BASE, cal)
        if state is not None:
            emu.write(PATCH_RAM, state)
        for addr, data in cls.records.items():
            emu.write(addr, data)
        build = emu.call(MODE01_H2, args=[0, 0], reset=False)
        assert build.ok
        sram = emu.read(SRAM_START, SRAM_LEN)
        answers = {tuple(q): _mode01(emu, q) for q in QUERIES}
        return {"sram": sram, "answers": answers, "insns": build.insns,
                "bitmap": emu.read(BITMAP, 12)}

    def _patched(self, enable: int, e_pct: int):
        state, cal = self.tick_state(enable, e_pct)
        return self._run(self.image, state, cal), state

    # -- proof (a): disabled --------------------------------------------
    def test_disabled_every_pid_answers_exactly_as_stock(self):
        run, state = self._patched(0, 85)
        self.assertEqual(state[OFF_OBD52:OFF_OBD52 + 2], b"\x00\x00",
                         "the tick must leave {A, valid} = {0, 0}")
        self.assertEqual(run["bitmap"], self.stock["bitmap"])
        for q, ans in self.stock["answers"].items():
            with self.subTest(q=bytes(q).hex()):
                self.assertEqual(run["answers"][q], ans)
        self.assertEqual(run["answers"][(0x52,)][0], 3, "01 52 unsupported")

    def test_disabled_moves_no_sram_outside_our_block(self):
        run, _ = self._patched(0, 85)
        changed = {SRAM_START + i for i in range(SRAM_LEN)
                   if run["sram"][i] != self.stock["sram"][i]}
        self.assertEqual({a for a in changed
                          if not PATCH_RAM <= a < PATCH_RAM + STATE_LEN}, set())

    def test_an_invalid_calibration_is_disabled_too(self):
        state, cal = self.tick_state(1, 85)
        bad = bytearray(cal)
        bad[0x14A - 1] ^= 0xFF                     # break the checksum
        emu = Med9Emu(self.image)
        emu.reset()
        emu.write(tff.CAL_BASE, bytes(bad))
        emu.write(PATCH_RAM, state)                 # sealed, cal_ok = 1 ...
        emu.write(PATCH_RAM + 0x06, b"\x00\x00")   # ... but force a re-init
        tff.EmuBase.no_frame(emu)
        emu.call(self.syms["ff_fuel_hook_b"], regs={"r1": TASK_STACK}, reset=False)
        self.assertEqual(emu.read(PATCH_RAM + 0x13, 1), b"\x00", "cal_ok")
        self.assertEqual(emu.read(REC52, 2), b"\x00\x00")

    # -- proof (b): enabled ---------------------------------------------
    def test_enabled_answers_pid_52_and_moves_nothing_else(self):
        for e_pct in (0, 50, 85, 100):
            with self.subTest(e_pct=e_pct):
                run, state = self._patched(1, e_pct)
                a = (e_pct * 255 + 50) // 100          # round half up
                self.assertEqual(state[OFF_OBD52:OFF_OBD52 + 2], bytes([a, 1]))
                self.assertEqual(run["answers"][(0x52,)], (1, bytes([0x52, a])))
                # the bitmap: exactly bit 0x40 of 0x80121F more than stock
                want = bytearray(self.stock["bitmap"])
                want[10] |= MASK_52
                self.assertEqual(run["bitmap"], bytes(want))
                # every other single-PID answer is the stock one
                for q, ans in self.stock["answers"].items():
                    if 0x52 in q or 0x40 in q:
                        continue
                    self.assertEqual(run["answers"][q], ans, bytes(q).hex())
                st, body = run["answers"][(0x40,)]
                self.assertEqual(st, 1)
                self.assertEqual(body[0], 0x40)
                self.assertEqual(body[1:], bytes(want[8:12]))
                # and the whole SRAM: our block, and the one bitmap byte
                changed = {SRAM_START + i for i in range(SRAM_LEN)
                           if run["sram"][i] != self.stock["sram"][i]}
                self.assertEqual({x for x in changed
                                  if not PATCH_RAM <= x < PATCH_RAM + STATE_LEN},
                                 {BITMAP_52})

    def test_enabled_a_multi_pid_request_carries_pid_52(self):
        run, _ = self._patched(1, 85)
        st, body = run["answers"][(0x52, 0x05)]
        self.assertEqual(st, 1)
        self.assertEqual(body[:2], bytes([0x52, 217]))      # 85 % -> 216.75 -> 217
        self.assertEqual(body[2:], self.stock["answers"][(0x05,)][1])

    def test_with_only_five_pids_valid_the_0x40_bitmap_appears(self):
        """F6's sparse seeding: PID 0x52 alone lights the 0x40 continuation."""
        state, cal = self.tick_state(1, 85)
        emu = Med9Emu(str(self.image), r2="app")
        emu.reset()
        emu.write(tff.CAL_BASE, cal)
        emu.write(PATCH_RAM, state)
        for addr, data in RECORDS.items():
            emu.write(addr, data)
        emu.call(MODE01_H2, args=[0, 0], reset=False)
        self.assertEqual(emu.read(BITMAP, 12).hex(), "88102001" "80000001" "00004000")
        self.assertEqual(_mode01(emu, [0x40]), (1, bytes.fromhex("4000004000")))
        self.assertEqual(_mode01(emu, [0x20]), (1, bytes.fromhex("2080000001")))
        self.assertEqual(_mode01(emu, [0x52]), (1, bytes.fromhex("52d9")))

    def test_the_builder_costs_one_loop_iteration(self):
        """README: 'Stock-instruction edits (PID 0x52)', cost row."""
        off, _ = self._patched(0, 85)
        on, _ = self._patched(1, 85)
        self.assertGreater(off["insns"], self.stock["insns"])
        self.assertLess(off["insns"] - self.stock["insns"], 12,
                        "a skipped fourth B2 entry is a handful of instructions")
        self.assertGreater(on["insns"], off["insns"])
        self.assertLess(on["insns"] - self.stock["insns"], 40)


# ---------------------------------------- 7. the producer, and the KWP route
class TestPid52Producer(G1Base):
    def _tick(self, cal: bytes, e_filt: int, mode_hold=True):
        emu = Med9Emu(self.image)
        emu.reset()
        emu.write(tff.CAL_BASE, cal)
        mdl = ff.FlexFuelModel(tff.cal_from_block(cal))
        mdl.state.e_filt = e_filt
        mdl.state.hold_ticks = 1000 if mode_hold else 0
        mdl.seal()
        emu.write(PATCH_RAM, mdl.full_bytes())
        tff.EmuBase.no_frame(emu)
        emu.call(self.syms["ff_fuel_hook_b"], regs={"r1": TASK_STACK}, reset=False)
        mdl.tick(None)
        return emu, mdl

    def test_the_record_matches_the_model_over_the_whole_range(self):
        cal = self.cal(1)
        for e_filt in (0, 1, 3, 4, 7, 100, 799, 800, 801, 1359, 1360, 1599, 1600):
            with self.subTest(e_filt=e_filt):
                emu, mdl = self._tick(cal, e_filt)
                self.assertEqual(emu.read(REC52, 4), mdl.obd_record() + b"\x00\x00")
                self.assertEqual(mdl.state.obd52_a,
                                 min((e_filt * 255 + 800) // 1600, 255))
                self.assertEqual(emu.read(PATCH_RAM, 0x2C), mdl.block_bytes())
                self.assertEqual(emu.read(PATCH_RAM + 0x40, 0x10), mdl.core2_bytes())

    def test_the_record_is_zero_whenever_the_switch_is_off(self):
        cal = self.cal(0)
        for e_filt in (0, 800, 1600):
            emu, mdl = self._tick(cal, e_filt)
            self.assertEqual(emu.read(REC52, 4), bytes(4))
            self.assertEqual(mdl.obd_record(), b"\x00\x00")

    def test_a_cold_start_zeroes_the_record_before_anything_reads_it(self):
        emu = Med9Emu(self.image)
        emu.reset()
        emu.write(tff.CAL_BASE, self.cal(1))
        emu.write(PATCH_RAM, b"\xA5" * STATE_LEN)        # garbage, valid = 0xA5
        tff.EmuBase.no_frame(emu)
        emu.call(self.syms["ff_fuel_hook_b"], regs={"r1": TASK_STACK}, reset=False)
        self.assertEqual(emu.read(PATCH_RAM + 4, 2), struct.pack(">H", STATE_LEN))
        self.assertEqual(emu.read(REC52, 4), bytes([0, 1, 0, 0]),
                         "E0 after a cold start, and valid because the switch is on")

    def test_the_record_ends_the_checksummed_core(self):
        self.assertEqual(ff.CORE2_OFF + ff.CORE2_LEN, STATE_LEN)
        self.assertTrue(ff.CORE2_OFF <= OFF_OBD52 < STATE_LEN)


class TestPid52OverKwp(G1Base):
    """Layer 7b: `01 52` through `logging/ecu_sim.py`'s KWP dispatch.

    The simulator speaks TP2.0/KWP, not ISO 15765-4: the ISO-TP single-frame
    parser is only partly traced (obd.md section 8 item 1) and `ecu_sim.py`
    (owned by brief G5 this wave) has no 0x7DF/0x7E8 path.  What it does do is
    look the request up in the firmware's own dispatch table at 0x2B820 and
    run the real `obd_mode01_h1` under that entry's session mask 0x50.  The
    session is put to internal 6 - generic OBD - with the real
    `kwp_session_set`, which is what SID 0x10's h2 does for tester address
    0x33 (obd.md section 1.2); the bitmap builder is called by hand because
    the walker that calls a table entry's h2 is still unknown (section 8
    item 2).
    """

    SESSION_SET = 0x13CEE4

    def _handlers(self, image, cal=None, state=None):
        sys.path.insert(0, str(REPO / "logging"))
        from ecu_sim import Med9Handlers
        h = Med9Handlers(str(image), animate=False)
        if cal is not None:
            h.emu.write(tff.CAL_BASE, cal)
        if state is not None:
            h.emu.write(PATCH_RAM, state)
        h.emu.call(self.SESSION_SET, args=[6], reset=False)
        return h

    def test_disabled_answers_like_stock_and_enabled_answers_the_ethanol(self):
        stock = self._handlers(DUMP)
        want = stock.handle(b"\x01\x52")
        self.assertEqual(want[-1][:2], b"\x7f\x01", "stock: 01 52 is refused")
        state_off, cal_off = self.tick_state(0, 85)
        off = self._handlers(self.image, cal_off, state_off)
        self.assertEqual(off.handle(b"\x01\x52"), want)
        state_on, cal_on = self.tick_state(1, 85)
        on = self._handlers(self.image, cal_on, state_on)
        self.assertEqual(on.handle(b"\x01\x52"), [b"\x41\x52\xd9"])
        on.emu.call(MODE01_H2, args=[0, 0], reset=False)
        self.assertEqual(on.handle(b"\x01\x40")[-1][:1], b"\x41")
        self.assertTrue(on.handle(b"\x01\x40")[-1][4] & MASK_52)


if __name__ == "__main__":
    unittest.main()
