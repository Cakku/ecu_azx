"""`emu/qspi_eeprom.py`: the QSPI queue engine, the M95160 and the NVM stack.

Four layers, each able to fail on its own (brief E4, issues #38/#39):

  1. the device on its own - opcodes, WEL/WIP, page wrap, 16-bit addressing;
  2. the firmware's own EEPROM code driving it: the two boot self-tests
     (0x017CF0 loopback, 0x017A84 WEL) and the three application primitives;
  3. the EEP_CONF block manager: `nvm_read_all_blocks` fills the RAM mirror
     from a synthetic device image, and the block header check of
     `re/findings/eeprom.md` section 10;
  4. D2's persistence sequence end to end on the applied ff_fuel image -
     stage, commit, pump, status 2, both device copies, then a cold restart
     that hands the stored ethanol back to the patch.

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

import eeprom_map as em  # noqa: E402  (tests.common put tools/ on sys.path)
import med9lib as ml  # noqa: E402
import patch_apply  # noqa: E402

sys.path.insert(0, str(REPO / "patches" / "ff_fuel"))

try:
    from emu import Med9Emu
    import emu.qspi_eeprom as q
    emu_available = True
except Exception:                                            # pragma: no cover
    emu_available = False
requires_emu = unittest.skipUnless(emu_available, "unicorn / emu package missing")

FF_FUEL = REPO / "patches" / "ff_fuel"
PATCH_RAM = 0x7FFB00
BLK8_MIRROR = 0x7F9F80
BLK8_LEN = 0x20
BLK8_COPY0, BLK8_COPY1 = 0x1C0, 0x1E0
IFLAG, MB6, MB6_DATA = 0x7078A4, 0x707960, 0x707966


def blk8_csum_ok(raw: bytes) -> bool:
    return ((sum(raw[:BLK8_LEN - 2])
             + struct.unpack(">H", raw[BLK8_LEN - 2:BLK8_LEN])[0]) & 0xFFFF) == 0xFFFF


# ----------------------------------------------------------- 1. the device --
class TestM95160(unittest.TestCase):
    def setUp(self):
        self.dev = q.M95160() if emu_available else None

    def xact(self, *bytes_out):
        self.dev.select()
        rx = [self.dev.xfer(b) for b in bytes_out]
        self.dev.deselect()
        return rx

    @requires_emu
    def test_a_fresh_device_is_all_ones_and_write_disabled(self):
        self.assertEqual(len(self.dev.mem), q.DEVICE_SIZE)
        self.assertEqual(set(self.dev.mem), {0xFF})
        self.assertEqual(self.xact(q.RDSR, 0)[1], 0)

    @requires_emu
    def test_wren_and_wrdi_move_the_wel_bit(self):
        self.xact(q.WREN)
        self.assertEqual(self.xact(q.RDSR, 0)[1] & q.ST_WEL, q.ST_WEL)
        self.xact(q.WRDI)
        self.assertEqual(self.xact(q.RDSR, 0)[1] & q.ST_WEL, 0)

    @requires_emu
    def test_a_write_without_wren_is_ignored(self):
        self.xact(q.WRITE, 0x01, 0x00, 0x42)
        self.assertEqual(self.dev.mem[0x100], 0xFF)

    @requires_emu
    def test_a_page_write_lands_and_clears_wel(self):
        self.xact(q.WREN)
        self.xact(q.WRITE, 0x01, 0x00, 0x42, 0x43)
        self.assertEqual(bytes(self.dev.mem[0x100:0x102]), b"\x42\x43")
        self.assertEqual(self.xact(q.RDSR, 0)[1] & q.ST_WEL, 0)

    @requires_emu
    def test_a_page_write_wraps_inside_its_own_page(self):
        self.xact(q.WREN)
        self.xact(q.WRITE, 0x01, 0x1E, 1, 2, 3, 4)
        self.assertEqual(bytes(self.dev.mem[0x11E:0x120]), b"\x01\x02")
        self.assertEqual(bytes(self.dev.mem[0x100:0x102]), b"\x03\x04",
                         "byte 3 must wrap to the start of the same page")
        self.assertEqual(bytes(self.dev.mem[0x120:0x122]), b"\xFF\xFF",
                         "and must NOT run into the next page")

    @requires_emu
    def test_a_read_is_sequential_and_wraps_at_the_end_of_the_array(self):
        self.dev.mem[0x7FE:0x800] = b"\xAA\xBB"
        self.dev.mem[0x000:0x002] = b"\xCC\xDD"
        rx = self.xact(q.READ, 0x07, 0xFE, 0, 0, 0, 0)
        self.assertEqual(rx[3:], [0xAA, 0xBB, 0xCC, 0xDD],
                         "opcode and the two address bytes read back as 0xFF")

    @requires_emu
    def test_wip_clears_after_the_configured_number_of_polls(self):
        dev = q.M95160(wip_polls=3)
        self.dev = dev
        self.xact(q.WREN)
        self.xact(q.WRITE, 0x00, 0x00, 0x55)
        for _ in range(3):
            self.assertEqual(self.xact(q.RDSR, 0)[1] & q.ST_WIP, q.ST_WIP)
        self.assertEqual(self.xact(q.RDSR, 0)[1] & q.ST_WIP, 0)

    @requires_emu
    def test_an_image_round_trips_through_a_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.dev.mem[0x10] = 0x5A
            self.dev.save(tmp / "e.bin")
            again = q.M95160.load(tmp / "e.bin")
            self.assertEqual(bytes(again.mem), bytes(self.dev.mem))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------- 2. the firmware's own EEPROM code
@requires_dump
@requires_emu
class TestAgainstTheFirmware(DumpUnchanged):
    def test_the_boot_self_tests_pass(self):
        """eeprom.md 1.6: loopback (0x017CF0) and the WEL latch (0x017A84)."""
        emu = Med9Emu(str(DUMP), r2="boot")
        q.QspiEeprom.install(emu)
        emu.call(q.BOOT_SPI_SELECT, reset=False)
        res = emu.call(q.BOOT_WEL_TEST, reset=False)
        self.assertTrue(res.ok, res.summary())
        self.assertEqual(res.regs["r3"], 1,
                         "WEL must be clear after WRDI and set after WREN")
        res = emu.call(q.BOOT_LOOPBACK_TEST, args=[0x807000], reset=False)
        self.assertTrue(res.ok, res.summary())
        self.assertEqual(res.regs["r3"], 1)
        self.assertEqual(emu.read(0x807000, 1), b"\x00", "error code must be 0")

    def test_the_application_primitives_round_trip(self):
        emu = Med9Emu(str(DUMP), r2="app")
        dev = q.QspiEeprom.install(emu)
        emu.call(q.APP_SPI_CONFIG, reset=False)
        emu.call(q.APP_WRITE_BYTE, args=[0x1C0, 0x5A], reset=False)
        self.assertEqual(dev.device.mem[0x1C0], 0x5A)
        dev.device.mem[0x000:0x040] = bytes(range(0x40))
        emu.call(q.APP_READ_BYTES, args=[0x000, 0x40, 0x807100], reset=False)
        self.assertEqual(emu.read(0x807100, 0x40), bytes(range(0x40)),
                         "a 0x40-byte read is three queues of <= 0x1D bytes")
        emu.write(0x807200, bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        emu.call(q.APP_WRITE_BYTES, args=[0x300, 4, 0x807200], reset=False)
        self.assertEqual(bytes(dev.device.mem[0x300:0x304]),
                         bytes([0xDE, 0xAD, 0xBE, 0xEF]))

    def test_the_self_test_entry_point_reports_pass(self):
        self.assertTrue(q.self_test(str(DUMP)))

    def test_the_queue_engine_clears_spe_and_sets_spif(self):
        emu = Med9Emu(str(DUMP), r2="app")
        dev = q.QspiEeprom.install(emu)
        emu.call(q.APP_SPI_CONFIG, reset=False)
        emu.call(q.APP_READ_BYTES, args=[0, 4, 0x807100], reset=False)
        self.assertGreater(dev.queues, 0, "the driver ran at least one queue")
        self.assertEqual(struct.unpack(">H", emu.read(q.SPCR1, 2))[0] & q.SPE, 0,
                         "the hardware clears SPE at the end of the queue")
        self.assertEqual(emu.read(q.SPSR, 1)[0] & q.SPIF, 0,
                         "and the driver clears SPIF once it has seen it")
        # drive one queue by hand to see SPIF set before anyone clears it
        emu.write(q.CMDRAM, 0x0E, 1)
        emu.write(q.TXRAM, q.RDSR, 2)
        emu.write(q.SPCR2, 0, 2)
        emu.write(q.SPCR1, q.SPE, 2)
        self.assertTrue(dev.run_pending(emu))
        self.assertEqual(emu.read(q.SPSR, 1)[0] & q.SPIF, q.SPIF)
        self.assertEqual(struct.unpack(">H", emu.read(q.SPCR1, 2))[0] & q.SPE, 0)


# --------------------------------------------------- 3. synthetic device images
@requires_dump
class TestImages(DumpUnchanged):
    def setUp(self):
        self.data = ml.load_dump(str(DUMP))
        self.blocks = em.read_blocks(self.data)

    def _check(self, raw: bytes):
        order = sorted(self.blocks, key=lambda b: b.addr)
        for k, b in enumerate(order):
            if b.idx == 0:
                continue
            end = order[k + 1].addr if k + 1 < len(order) else em.DEVICE_SIZE
            for n in range(max((end - b.addr) // (b.pages * em.PAGE), 1)):
                a = b.copy_addr(n)
                stored = struct.unpack_from(">H", raw, a + b.payload)[0]
                self.assertEqual(stored, em.block_checksum(raw[a:a + b.payload]),
                                 f"blk {b.idx} copy {n}")

    @requires_emu
    def test_blank_image_checksums_every_copy(self):
        self._check(q.blank_image(str(DUMP)))

    @requires_emu
    def test_factory_image_carries_every_block_default(self):
        raw = q.factory_image(str(DUMP))
        self._check(raw)
        b = self.blocks[8]
        dflt = bytes(self.data[em.DEFAULTS + b.dflt:
                               em.DEFAULTS + b.dflt + b.payload])
        self.assertEqual(raw[BLK8_COPY0:BLK8_COPY0 + b.payload], dflt)
        self.assertEqual(raw[BLK8_COPY0:BLK8_COPY0 + 2], bytes([8, 1]),
                         "payload +0/+1 is the {block id, version} stamp")

    @requires_emu
    def test_an_overlay_can_keep_the_header(self):
        raw = q.factory_image(str(DUMP), payloads={8: {2: bytes([85])}})
        self.assertEqual(raw[BLK8_COPY0:BLK8_COPY0 + 3], bytes([8, 1, 85]))
        self._check(raw)


# ---------------------------------------- 4. the block manager, cold start ----
@requires_dump
@requires_emu
class TestBlockManager(DumpUnchanged):
    """`nvm_read_all_blocks` (0x06227C) against a device that answers."""

    def _boot(self, image: bytes):
        emu = Med9Emu(str(DUMP), r2="app")
        qspi, _b = q.install_eeprom(emu, device=q.M95160(image))
        self.assertTrue(q.cold_start(emu), "the queue never reached idle")
        return emu, qspi

    def test_the_entry_point_is_four_bytes_below_the_documented_one(self):
        """eeprom.md 3.5 named the instruction after `mr r11,r1`."""
        data = ml.load_dump(str(DUMP))
        for entry in (q.NVM_READ_ALL_BLOCKS, q.NVM_WRITE_ALL_BLOCKS):
            off = ml.cpu_to_file(entry)
            with self.subTest(entry=hex(entry)):
                self.assertEqual(bytes(data[off:off + 4]),
                                 bytes.fromhex("7c2b0b78"), "mr r11,r1")
                self.assertEqual(bytes(data[off + 4:off + 8]),
                                 bytes.fromhex("9421ffe0"), "stwu r1,-0x20(r1)")

    def test_a_factory_image_fills_the_mirror(self):
        raw = q.factory_image(str(DUMP), payloads={8: {2: bytes([0x5A])}})
        emu, _ = self._boot(raw)
        self.assertEqual(emu.read(BLK8_MIRROR, BLK8_LEN),
                         raw[BLK8_COPY0:BLK8_COPY0 + BLK8_LEN])
        self.assertEqual(emu.read(BLK8_MIRROR + 2, 1), b"\x5A")

    def test_a_block_whose_id_version_stamp_is_wrong_falls_back_to_defaults(self):
        """The finding of eeprom.md section 10, and why +0 is not free."""
        data = ml.load_dump(str(DUMP))
        b = self.blocks_8 = em.read_blocks(data)[8]
        dflt = bytes(data[em.DEFAULTS + b.dflt:em.DEFAULTS + b.dflt + b.payload])
        for label, payload in (("E% on the id byte", {0: bytes([85])}),
                               ("E% on the version byte", {1: bytes([85])}),
                               ("an erased block", {0: b"\xFF\xFF"})):
            with self.subTest(label):
                raw = q.factory_image(str(DUMP), payloads={8: payload})
                emu, _ = self._boot(raw)
                got = emu.read(BLK8_MIRROR, b.payload)
                self.assertEqual(got, dflt,
                                 "the manager must have reloaded the defaults")
        raw = q.factory_image(str(DUMP), payloads={8: {2: bytes([85])}})
        emu, _ = self._boot(raw)
        self.assertEqual(emu.read(BLK8_MIRROR + 2, 1), bytes([85]),
                         "+2 and above survive, which is why "
                         "ff_persist_offset must be >= 2")


# ------------------------------------- 5. D2's persistence path, end to end ---
@requires_dump
@requires_emu
class TestPersistenceThroughTheDevice(DumpUnchanged):
    """#38: stage -> commit -> pump -> status 2 -> power cut -> read back."""

    OFFSET = 2

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import ffcal001
        cls.tmp = Path(tempfile.mkdtemp())
        data, _r, _w = patch_apply.apply_patch(DUMP, FF_FUEL)
        cls.image = cls.tmp / "ff_fuel.bin"
        cls.image.write_bytes(bytes(data))
        cls.syms = {k: int(v, 0) for k, v in
                    json.loads((FF_FUEL / "patch.json").read_text())
                    ["build"]["symbols"].items()}
        params = ffcal001.load_params(FF_FUEL / "ffcal001.json")
        cls.cal = ffcal001.build(dict(params, ff_persist_offset=cls.OFFSET))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def boot(self, image: bytes):
        emu = Med9Emu(str(self.image), r2="app")
        emu.write(0x5E2510, self.cal)
        dev = q.M95160(image)
        q.install_eeprom(emu, device=dev)
        emu.write(IFLAG, 0, 2)
        self.assertTrue(q.cold_start(emu))
        return emu, dev

    def tick(self, emu, rx=None, pump=True):
        from emu.models import flexfuel as ff        # noqa: F401 (import cost)
        if rx:
            emu.write(IFLAG, 1 << 6, 2)
            emu.write(MB6, 8, 2)
            emu.write(MB6_DATA, rx)
        else:
            emu.write(IFLAG, 0, 2)
        res = emu.call(self.syms["ff_fuel_hook_b"], reset=False)
        self.assertTrue(res.ok, res.summary())
        if pump:
            res = emu.call(q.NVM_PUMP_WRAPPER, reset=False, max_insns=4_000_000)
            self.assertTrue(res.ok, res.summary())

    def test_a_commit_reaches_status_2_and_both_device_copies(self):
        from emu.models import flexfuel as ff
        raw = q.factory_image(str(DUMP), payloads={8: {self.OFFSET: b"\xFF"}})
        before = raw[BLK8_COPY0:BLK8_COPY0 + BLK8_LEN]
        emu, dev = self.boot(raw)

        for i in range(600):
            self.tick(emu, ff.frame(e_pct=85, t_fuel_c=25,
                                    counter=(i // 10) & 0xFF)
                      if i % 10 == 0 else None)
        emu.write(PATCH_RAM + 0x3A, 0, 2)              # open the rate limit
        self.tick(emu)
        req = self.syms["ff_nvm_req"]
        rec = emu.read(req, 9)
        self.assertEqual(tuple(rec[4:9]), (8, 0, 0, 3, 1),
                         "blk 8, commit shape (off and len are 0), queued")
        self.assertEqual(emu.read(BLK8_MIRROR + self.OFFSET, 1)[0],
                         struct.unpack_from(">H", emu.read(PATCH_RAM, 0x40),
                                            0x34)[0] & 0xFF,
                         "the stage put ff_diag_e_pct at the calibrated offset")

        for _ in range(40):
            self.tick(emu)
            rec = emu.read(req, 9)
            if rec[8] != 1:
                break
        self.assertEqual(rec[8], 2, "the request record must reach status 2")

        self.tick(emu)                                  # let the patch notice
        st = emu.read(PATCH_RAM, 0x40)
        self.assertEqual(st[0x32], 3, "persist_state = DONE")
        self.assertEqual(st[0x33], 2, "persist_err = the manager's own 2")
        self.assertEqual(struct.unpack_from(">H", st, 0x3C)[0], 1, "one write")
        self.assertEqual(struct.unpack_from(">H", st, 0x3E)[0], 0, "no fails")

        stored = struct.unpack_from(">H", st, 0x30)[0]
        self.assertGreater(stored, 0)
        for copy, base in (("0", BLK8_COPY0), ("1", BLK8_COPY1)):
            with self.subTest(copy=copy):
                got = bytes(dev.mem[base:base + BLK8_LEN])
                self.assertEqual(got[self.OFFSET], stored)
                self.assertTrue(blk8_csum_ok(got))
                self.assertEqual(got[:2], before[:2], "the id/version stamp")
                self.assertEqual(got[14], before[14], "+14 is the stock client's")
        self.assertNotEqual(dev.mem[BLK8_COPY0 + 29], before[29],
                            "+29 is the manager's own ReplV byte and it DOES "
                            "move on the first commit")

    def test_the_value_survives_a_simulated_power_cut(self):
        from emu.models import flexfuel as ff
        raw = q.factory_image(str(DUMP), payloads={8: {self.OFFSET: b"\xFF"}})
        emu, dev = self.boot(raw)
        for i in range(600):
            self.tick(emu, ff.frame(e_pct=85, counter=(i // 10) & 0xFF)
                      if i % 10 == 0 else None)
        emu.write(PATCH_RAM + 0x3A, 0, 2)
        self.tick(emu)
        for _ in range(40):
            self.tick(emu)
            if emu.read(self.syms["ff_nvm_req"] + 8, 1)[0] != 1:
                break
        self.tick(emu)
        stored = struct.unpack_from(">H", emu.read(PATCH_RAM, 0x40), 0x30)[0]

        # power cut: a fresh emulator, the device image as it was left
        cold, _dev2 = self.boot(bytes(dev.mem))
        self.assertEqual(cold.read(BLK8_MIRROR + self.OFFSET, 1)[0], stored)
        self.tick(cold, pump=False)                    # the first activation
        st = cold.read(PATCH_RAM, 0x40)
        self.assertEqual(struct.unpack_from(">H", st, 0x24)[0], stored * 16,
                         "e_key")
        self.assertEqual(struct.unpack_from(">H", st, 0x08)[0], stored * 16,
                         "e_filt starts where it left off")
        self.assertEqual(struct.unpack_from(">H", st, 0x30)[0], stored,
                         "e_persist")
        self.assertEqual(st[0x33], 2, "persist_err = the read's rc")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
