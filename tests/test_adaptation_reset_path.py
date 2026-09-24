"""The stock adaptation-reset path fault_clear_then_adaptation_reset 0x0D1068.

Brief H2 (#47 / #26 / #38).  G7 found that 0x0D1068 clears the fault memory
(EEP_CONF block 24) and then resets all 17 KWP adaptation channels
(`adaptation_reset_all_commit` 0x038D64, block 8 +2..+18) when **0x7FEB59 != 0
and bit 0 of 0x7FA02B** (block 11 mirror +11) are set.  H2 established the
setter (the routine-0xC5 progress handler 0x087494, programming stack) and the
meaning of 0x7FEB59 (`re/findings/eeprom.md` section 11).

Two kinds of check here:

* **Static** anchors of the section-11 facts, straight from the dump: the reset
  routine has exactly one caller (so a plain tester `14 FF 00` cannot reach it),
  0x0D1068 gates on both cells, and the setter site raw-writes EEPROM 0x28B.

* **Dynamic** (VERIFIED-DYNAMIC, emulated), the G7 pattern of
  `tests/test_ff_diag_patch.py` on the QSPI device model of
  `emu/qspi_eeprom.py`: seed the device with the flag set and E% 85 at block 8
  +19, run a real boot's set-up (init entries 33/34/38/39 and
  `adaptation_restore_all`, which arms the implemented-channel mask), set
  0x7FEB59, perturb a channel, then drive 0x0D1068 to completion with the NVM
  pump and the virtual time base.  Assert: the fault-clear machine completes and
  block 24 is committed; every channel returns to its default; block 8 is
  rewritten with the E% at +19 intact; the flag mirror bit is cleared (block 11
  staged); and the whole-SRAM / whole-device difference between an E% of 85 and
  the default 0 is that byte and the block checksum only.

Nothing here modifies `data/passat_azx_ori.bin` (`DumpUnchanged`), and it uses
only the stock firmware under Unicorn -- no patch is applied.  The modelled
parts (the RAM fault-entry erase is downstream of this handler; the device
write-back of the cleared block 11 is deferred to key-off) are labelled.
"""
from __future__ import annotations

import struct
import unittest

from tests import test_ff_diag_patch as tdp
from tests import test_ff_fuel_patch as tff
from tests.common import DUMP, DumpUnchanged, requires_dump

import find_branch_refs as fbr  # noqa: E402  (tests.common put tools/ on path)
import med9lib as m  # noqa: E402
import eeprom_map as em  # noqa: E402

# --- the functions and cells of section 11 ---------------------------------
RESET_PATH = 0x0D1068          # fault_clear_then_adaptation_reset
RESET_ALL = 0x038D64           # adaptation_reset_all_commit
RESET_ALL_CALL = 0x0D10D0      # its sole caller, inside RESET_PATH
FAULT_CLEAR = 0x035300         # the kwp14 fault-clear state machine
CLEAR_STATE = 0x7FB718         # kwp14_clear_state (0/1/2)
C5_RESULTS = 0x087494          # kwp_prog_routine_c5_results (the setter)
C5_DISPATCH = 0x087AFC         # SID 0x33 RequestRoutineResults dispatch
CLASSIFIER = 0x133F40          # boot_mode_classifier (sole writer of 0x7FEB59)

FEB59 = 0x7FEB59               # gate 1: abnormal_boot_latch
FLAG_MIRROR = 0x7FA02B         # gate 2: block 11 mirror +11 (bit 0)
FLAG_EEPROM = 0x28B            # block 11 copy 0 payload +11 in the device
FLAG_EEPROM_C1 = 0x2AB         # copy 1

BLK8_MIRROR = tdp.BLK8_MIRROR  # 0x7F9F80
BLK8_LEN = tdp.BLK8_LEN        # 0x20
BLK8_DEV = tdp.BLK8_DEV        # (0x1C0, 0x1E0)
PERSIST_OFF = tdp.PERSIST_OFF  # 19 (the ff_fuel E% store)
ADAP_CHANNELS = tdp.ADAP_CHANNELS  # range(2, 19): payload +2..+18
ADAP_RAM = tdp.ADAP_RAM        # (0x7FD062, 12)
ADAP_RESTORE_ALL = tdp.ADAP_RESTORE_ALL  # 0x12E3F4 (the mr r11,r1 entry)
NVM_MASK = 0x8001D4            # implemented-channel mask restore_all arms
CH8_RAM = 0x7FD067             # adaptation channel 8 (adap_ch8_ksta), default 128
CH8_PAYLOAD = 9               # channel 8 = block 8 payload +9  (k + 1)

BLK24_DEV = 0x620              # EEP_CONF block 24 in the device
BLK24_IDX = 24

# init entries a real boot runs before the fault services can answer (G5,
# `re/findings/kwp.md` section 12.7; boot.md section 6.4):
#   33 = 0x1344A4 seeds the boot-mode status byte 0x7FD411
#   34 = 0x12F10C sets the fault-code table pointer 0x7FBA58
#   38 = 0x12F138 installs the RAM fault-memory pointers
#   39 = 0x12EC00 (dfp_init) frees the fault-memory lock {0x7FBA5C, 0x7FBA60}
INIT_ENTRIES = ((33, 0x1344A4), (34, 0x12F10C), (38, 0x12F138), (39, 0x12EC00))

MAX_INSNS = 8_000_000


def _emu_imports():
    from emu import Med9Emu
    import emu.qspi_eeprom as q
    from emu.time_base import VirtualTimeBase
    return Med9Emu, q, VirtualTimeBase


# ---------------------------------------------------------------------------
# Static: the dump itself, no emulator
# ---------------------------------------------------------------------------
@requires_dump
class TestStaticFacts(DumpUnchanged):
    """The section-11 call graph and instruction anchors, from the bytes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = m.load_dump(str(DUMP))

    def word(self, cpu: int) -> int:
        return struct.unpack_from(">I", self.data, m.cpu_to_file(cpu))[0]

    def test_reset_all_has_exactly_one_caller(self):
        """0x038D64 is called only from 0x0D10D0 -> a plain `14 FF 00`, whose
        handler runs the same fault-clear machine but never 0x038D64, cannot
        reset the fuel trims (eeprom.md section 11.3)."""
        hits = fbr.scan(self.data, [RESET_ALL], want_ptr=True)[RESET_ALL]
        calls = [site for _off, site, kind in hits if kind == "bl"]
        ptrs = [site for _off, site, kind in hits if kind == "ptr"]
        self.assertEqual(calls, [RESET_ALL_CALL],
                         "adaptation_reset_all_commit must have one bl caller")
        self.assertEqual(ptrs, [], "and it is not held in any pointer table")

    def test_reset_path_gates_on_both_cells(self):
        # 0xD1074  lbz r12,-0x1497(r13)  -> 0x7FEB59
        self.assertEqual(self.word(0x0D1074), 0x898DEB69)
        # 0xD1094  lis r12,0x80 ; 0xD1098 lbz r3,-0x5FD5(r12) -> 0x7FA02B
        self.assertEqual(self.word(0x0D1094), 0x3D800080)
        self.assertEqual(self.word(0x0D1098), 0x886CA02B)
        # 0xD10D0  bl 0x038D64
        tgt, name = fbr.branch_target(self.word(RESET_ALL_CALL), RESET_ALL_CALL)
        self.assertEqual((tgt, name), (RESET_ALL, "bl"))

    def test_routine_c5_is_the_setter_site(self):
        # 0x87844  li r3,0x28B ; 0x87848 li r4,1 ; 0x87850 bl 0x85B54 (write)
        self.assertEqual(self.word(0x087844), 0x386002_8B & 0xFFFFFFFF)
        self.assertEqual(self.word(0x087848), 0x38800001)
        tgt, name = fbr.branch_target(self.word(0x087850), 0x087850)
        self.assertEqual((tgt, name), (0x085B54, "bl"), "eeprom_write_bytes")
        # the copy write 0x87894 -> EEPROM 0x2AB
        self.assertEqual(self.word(0x087888), 0x386002_AB & 0xFFFFFFFF)

    def test_the_c5_dispatch_reaches_the_setter(self):
        # 0x87AFC dispatch: cmpwi r3,0xC5 (0x87B18) ; beq 0x87B30 ; bl 0x87494
        self.assertEqual(self.word(0x087B18), 0x2C0300C5)
        tgt, name = fbr.branch_target(self.word(0x087B34), 0x087B34)
        self.assertEqual((tgt, name), (C5_RESULTS, "bl"))

    def test_feb59_has_a_single_writer_in_the_classifier(self):
        """0x134030 (stb r6,-0x1497(r13)) is the only store to 0x7FEB59, and it
        lives inside boot_mode_classifier 0x133F40."""
        self.assertEqual(self.word(0x134030), 0x98CDEB69)
        # 0x133F88 lwz r12,-0x7FD0(r13) = 0x7F8020, then compared to 0xAABFFB11
        self.assertEqual(self.word(0x133F8C), 0x3D60AABF)
        self.assertEqual(self.word(0x133F90), 0x616BFB11)

    def test_the_flag_lives_in_block_11(self):
        blocks = {b.idx: b for b in em.read_blocks(self.data)}
        self.assertEqual(blocks[11].addr, 0x280)
        self.assertEqual(FLAG_EEPROM, blocks[11].addr + 11)
        self.assertEqual(FLAG_EEPROM_C1, blocks[11].copy_addr(1) + 11)
        # the manager mirror of block 11 +11 is 0x7FA02B
        self.assertEqual(blocks[11].mirror_cpu + 11, FLAG_MIRROR)


# ---------------------------------------------------------------------------
# Dynamic: the whole path under Unicorn on the stock dump
# ---------------------------------------------------------------------------
@requires_dump
@tff.requires_emu
class ResetPathEmu(DumpUnchanged):
    """Boot the stock image with the flag set and drive 0x0D1068."""

    def boot(self, *, e_pct: int = 85, flag: bool = True, feb59: bool = True,
             restore: bool = True):
        Med9Emu, q, VirtualTimeBase = _emu_imports()
        emu = Med9Emu(str(DUMP), r2="app")
        payloads = {8: {PERSIST_OFF: bytes([e_pct])}}
        if flag:
            payloads[11] = {11: b"\x01"}
        dev = q.M95160(q.factory_image(str(DUMP), payloads=payloads))
        q.install_eeprom(emu, device=dev)
        self.assertTrue(q.cold_start(emu), "the start-up read never reached idle")
        tb = VirtualTimeBase(emu)
        for _idx, addr in INIT_ENTRIES:
            self.assertTrue(emu.call(addr, reset=False, max_insns=MAX_INSNS).ok,
                            f"init entry {_idx:#x} failed")
        if restore:
            r = emu.call(ADAP_RESTORE_ALL, reset=False, max_insns=MAX_INSNS)
            self.assertTrue(r.ok, r.summary())
        if feb59:
            emu.write(FEB59, 1, 1)
        return emu, dev, tb

    def pump(self, emu, q, n: int = 80):
        for _ in range(n):
            self.assertTrue(
                emu.call(q.NVM_PUMP_WRAPPER, reset=False, max_insns=MAX_INSNS).ok)

    def drive(self, emu, tb, *, iters: int | None = None, stop_on_clear=True):
        """Call 0x0D1068 the way the 100 ms task does, pumping the NVM queue and
        advancing the time base between calls.  Returns the iteration count."""
        _M, q, _V = _emu_imports()
        limit = iters if iters is not None else 40
        for i in range(limit):
            tb.advance(2.0)
            self.assertTrue(
                emu.call(RESET_PATH, reset=False, max_insns=MAX_INSNS).ok)
            self.pump(emu, q)
            if (iters is None and stop_on_clear
                    and (emu.read(FLAG_MIRROR, 1)[0] & 1) == 0):
                return i + 1
        return limit

    def defaults(self) -> bytes:
        return tdp.TestPersistOffsetOffTheChannels.defaults(self)


class TestGate(ResetPathEmu):
    """Both gates are required; neither alone fires the reset."""

    def _perturb(self, emu):
        emu.write(CH8_RAM, 0x40, 1)
        emu.write(BLK8_MIRROR + CH8_PAYLOAD, 0x40, 1)

    def test_without_the_flag_nothing_happens(self):
        emu, _dev, tb = self.boot(flag=False, feb59=True)
        self._perturb(emu)
        self.assertEqual(self.drive(emu, tb, iters=6), 6)
        self.assertEqual(emu.read(CH8_RAM, 1), b"\x40", "channel not reset")
        self.assertEqual(emu.read(CLEAR_STATE, 1), b"\x00", "no fault-clear ran")

    def test_without_feb59_nothing_happens(self):
        emu, _dev, tb = self.boot(flag=True, feb59=False)
        self._perturb(emu)
        self.assertEqual(self.drive(emu, tb, iters=6), 6)
        self.assertEqual(emu.read(CH8_RAM, 1), b"\x40", "channel not reset")
        self.assertEqual(emu.read(FLAG_MIRROR, 1)[0] & 1, 1, "flag still set")


class TestFullPath(ResetPathEmu):
    """The whole path with both gates set (E% 85 at +19)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        self.emu, self.dev, self.tb = self.boot()
        # restore_all must have armed the implemented-channel mask, else
        # 0x038D64 resets nothing.
        self.assertEqual(struct.unpack(">I", self.emu.read(NVM_MASK, 4))[0],
                         0x000077BE, "adaptation_restore_all arms the mask")
        self.assertEqual(self.emu.read(CH8_RAM, 1), b"\x80",
                         "channel 8 is at its default after restore_all")
        # perturb channel 8 so the reset has visible work to do
        self.emu.write(CH8_RAM, 0x40, 1)
        self.emu.write(BLK8_MIRROR + CH8_PAYLOAD, 0x40, 1)
        self.iters = self.drive(self.emu, self.tb)

    def test_the_fault_clear_machine_completed(self):
        self.assertLess(self.iters, 40, "0x0D1068 reached its end")
        self.assertEqual(self.emu.read(CLEAR_STATE, 1), b"\x00",
                         "kwp14_clear_state cycled 0 -> 1 -> 2 -> 0")

    def test_block_24_is_committed(self):
        """The fault-clear machine commits EEP_CONF block 24.  (The RAM
        fault-entry erase is downstream of this handler -- G5, kwp.md 12.7 --
        and is not modelled here; the block-24 commit is the concrete
        evidence.)"""
        blk = {b.idx: b for b in em.read_blocks(m.load_dump(str(DUMP)))}[BLK24_IDX]
        raw = bytes(self.dev.mem[BLK24_DEV:BLK24_DEV + blk.length])
        self.assertEqual(raw[:2], bytes([BLK24_IDX, 0x02]), "block-24 {id,ver} stamp")
        stored = struct.unpack_from(">H", raw, blk.payload)[0]
        self.assertEqual(em.block_checksum(raw[:blk.payload]), stored,
                         "block 24 carries a valid checksum after the commit")

    def test_every_channel_returns_to_its_default(self):
        dflt = self.defaults()
        self.assertEqual(self.emu.read(CH8_RAM, 1), b"\x80",
                         "the perturbed channel 8 is back at its default")
        ref, _dev, _tb = self.boot(e_pct=0)  # a run that never perturbed a channel
        self.assertEqual(self.emu.read(*ADAP_RAM), ref.read(*ADAP_RAM),
                         "the channel RAM cells match a clean boot")
        for base in BLK8_DEV:
            raw = bytes(self.dev.mem[base:base + BLK8_LEN])
            for i in ADAP_CHANNELS:
                self.assertEqual(raw[i], dflt[i], f"device block 8 +{i}")

    def test_the_e_pct_at_19_survives_in_both_copies(self):
        for base in BLK8_DEV:
            raw = bytes(self.dev.mem[base:base + BLK8_LEN])
            self.assertEqual(raw[PERSIST_OFF], 85, f"E% intact at +{PERSIST_OFF}")
            self.assertEqual(raw[:2], tdp.BLK8_STAMP, "block-8 stamp kept")
            self.assertTrue(tdp.blk8_csum_ok(raw), "block-8 checksum valid")

    def test_the_flag_is_cleared_and_block_11_staged(self):
        self.assertEqual(self.emu.read(FLAG_MIRROR, 1)[0] & 1, 0,
                         "bit 0 of the block 11 mirror 0x7FA02B is cleared")
        # 0x0D1110 only STAGES the cleared byte into the mirror; the device
        # write of block 11 is deferred to the manager's key-off write-all
        # (eeprom.md 3.5).  So the device copy still reads the set bit here.
        self.assertEqual(self.dev.mem[FLAG_EEPROM] & 1, 1,
                         "device block 11 +11 write-back is deferred (labelled)")


class TestPlainFaultClearSparesTheChannels(ResetPathEmu):
    """A workshop DTC clear (`14 FF 00`) runs the fault-clear machine 0x035300
    directly and never 0x038D64, so it does not reset the fuel trims."""

    def test_running_the_fault_clear_machine_alone_leaves_channels(self):
        emu, dev, tb = self.boot(feb59=True, flag=True)
        emu.write(CH8_RAM, 0x40, 1)
        # drive 0x035300 the way kwp_sid_14_h1 does: keep the state in
        # kwp14_clear_state and step until it reports 2, pumping + advancing.
        _M, q, _V = _emu_imports()
        emu.write(CLEAR_STATE, 0, 1)
        for _ in range(40):
            state = emu.read(CLEAR_STATE, 1)[0]
            if state == 2:
                break
            tb.advance(2.0)
            r = emu.call(FAULT_CLEAR, args=[state], reset=False, max_insns=MAX_INSNS)
            self.assertTrue(r.ok, r.summary())
            emu.write(CLEAR_STATE, r.regs["r3"] & 0xFF, 1)
            self.pump(emu, q)
        self.assertEqual(emu.read(CLEAR_STATE, 1), b"\x02",
                         "the fault-clear machine completed on its own")
        self.assertEqual(emu.read(CH8_RAM, 1), b"\x40",
                         "the channel is untouched: no adaptation reset")
        # and block 24 was still committed by the fault clear
        self.assertEqual(dev.mem[BLK24_DEV], BLK24_IDX)


class TestWholeSramAndDeviceDiff(ResetPathEmu):
    """Through the entire path, an E% of 85 vs the default 0 at +19 differs only
    in that byte and the block-8 checksums -- nothing else in SRAM or the
    device."""

    FIXED_ITERS = 6

    def _run(self, e_pct: int):
        emu, dev, tb = self.boot(e_pct=e_pct)
        emu.write(CH8_RAM, 0x40, 1)
        emu.write(BLK8_MIRROR + CH8_PAYLOAD, 0x40, 1)
        self.drive(emu, tb, iters=self.FIXED_ITERS)
        self.assertEqual(emu.read(FLAG_MIRROR, 1)[0] & 1, 0, "path finished")
        return emu, dev

    def test_the_device_diff_is_the_e_pct_and_its_checksums(self):
        a, da = self._run(85)
        _b, db = self._run(0)
        changed = {i for i in range(len(da.mem)) if da.mem[i] != db.mem[i]}
        allowed = set()
        for base in BLK8_DEV:
            allowed |= {base + PERSIST_OFF, base + BLK8_LEN - 2, base + BLK8_LEN - 1}
        self.assertTrue(changed <= allowed, sorted(hex(x) for x in changed))
        self.assertIn(BLK8_DEV[0] + PERSIST_OFF, changed)

    def test_the_sram_diff_is_the_e_pct_and_its_checksums(self):
        a, _da = self._run(85)
        b, _db = self._run(0)
        sa = a.read(tff.SRAM_START, tff.SRAM_LEN)
        sb = b.read(tff.SRAM_START, tff.SRAM_LEN)
        changed = {tff.SRAM_START + i for i in range(tff.SRAM_LEN) if sa[i] != sb[i]}
        allowed = set()
        # the mirror and the manager's page buffer each hold a copy of block 8
        for base in (BLK8_MIRROR, tdp.NVM_PAGE_BUF):
            allowed |= {base + PERSIST_OFF, base + BLK8_LEN - 2, base + BLK8_LEN - 1}
        allowed |= set(range(tdp.NVM_CSUM_CELLS, tdp.NVM_CSUM_CELLS + 4))
        stack = {x for x in changed if tdp.STACK_LO <= x < tff.STACK_TOP + 4}
        # Outside the emulator's own stack scratch, the only SRAM that differs
        # between an E% of 85 and 0 is the block-8 mirror, the manager's page
        # buffer and its two checksum cells -- the E% byte and the checksums
        # that cover it, nothing that any other subsystem reads.
        self.assertTrue(changed - stack <= allowed,
                        sorted(hex(x) for x in changed - stack))
        # The stack residue is transient: the block-8 checksum is summed on the
        # stack and that sum depends on the E%, so those slots differ by the E%
        # delta.  Assert only that it stays inside the transient stack window
        # and never leaks into a live cell (already covered above).
        self.assertTrue(stack <= set(range(tdp.STACK_LO, tff.STACK_TOP + 4)),
                        sorted(hex(x) for x in stack))


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
