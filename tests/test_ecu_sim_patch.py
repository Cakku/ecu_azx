"""The whole bench chain in one process: node -> simulator -> logger.

`logging/ecu_sim.py --sim-patch` applies `patches/ff_fuel` and runs the
patch's own hooks off a simulated 10 ms clock; `logging/ethanol_frame_send.py`
stands in for the Pico on the same virtual bus; `logging/med9log.py` reads the
state block over KWP.  What this file asserts is checks 1-5 of the comment in
`logging/sessions/ff_fuel.json` plus a persistence round trip through the
EEPROM file (brief E4, issues #37/#38/#39).

Nothing here writes to `data/` and nothing is ever flashed.
"""
from __future__ import annotations

import csv
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

sys.path.insert(0, str(REPO / "logging"))

FF_FUEL = REPO / "patches" / "ff_fuel"
SESSION = REPO / "logging" / "sessions" / "ff_fuel.json"
PATCH_JSON = FF_FUEL / "patch.json"
PATCH_RAM = 0x7FFB00
#: taken from the reference model, not restated, so these tests keep working
#: when a later wave appends to `struct ff_state` (E2 took the length from
#: 64 to 68; nothing in the annex moved).
MAGIC = 0x46463031

try:
    import med9log
    from ecu_sim import AnimatedRam, DEFAULT_STATICS, Med9Handlers, PatchRunner
    from emu.models import flexfuel as ff
    from emu import qspi_eeprom as qe
    MAGIC = ff.FlexFuelModel.MAGIC
    STATE_LEN = ff.FlexFuelModel.LENGTH
    from med9kwp.can_transport import have_can
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_sim = unittest.skipUnless(available, "unicorn / python-can missing")


def handlers(**kw) -> "Med9Handlers":
    kw.setdefault("patch_dir", str(FF_FUEL))
    kw.setdefault("seed", 0x12345678)
    kw.setdefault("ram", AnimatedRam(live_task_set="A",
                                     statics=dict(DEFAULT_STATICS)))
    return Med9Handlers(str(DUMP), **kw)


def drive(runner: "PatchRunner", seconds: float, *, e_pct=85, status=0,
          frames=True, temp_c=25) -> None:
    """Advance the simulated clock, feeding the node's frames at 10 Hz.

    Deterministic in SIMULATED time: `PatchRunner.advance` stops early when
    its wall-clock budget (MAX_CATCHUP_WALL_S) runs out, which on a loaded
    host used to leave the runner short of the target -- and a test that
    counted segments over "6 s" then saw fewer than it asserted (the G5
    flake).  Each 100 ms step is therefore driven until the runner's own
    clock has reached it, so every test sees exactly `seconds` of ECU time
    whatever the host is doing.
    """
    step, n = 0.1, int(seconds / 0.1)
    start = runner.sim_t
    for i in range(n):
        if frames:
            runner.on_frame(0x0EC, ff.frame(e_pct=e_pct, t_fuel_c=temp_c,
                                            counter=i & 0xFF, status=status))
        target = start + (i + 1) * step
        while runner.sim_t + runner.tick_s <= target + 1e-9:
            runner.advance(target + 1e-9)


# ------------------------------------------------- the runner on its own ----
@requires_dump
@requires_sim
class TestPatchRunner(DumpUnchanged):
    def test_the_hooks_fill_the_state_block(self):
        h = handlers()
        drive(h.runner, 5.0)
        st = h.emu.read(PATCH_RAM, 0x40)
        self.assertEqual(struct.unpack_from(">I", st, 0)[0], MAGIC, "check 1")
        self.assertEqual(struct.unpack_from(">H", st, 4)[0], STATE_LEN,
                         "check 1")
        self.assertEqual(st[0x1E], 1, "check 2: only the set-A hook fires")
        self.assertEqual(st[0x1D], 1, "check 2: and it owns the tick")
        ticks = struct.unpack_from(">I", st, 0x20)[0]
        self.assertEqual(ticks, h.runner.ticks, "check 3")
        raster = struct.unpack(">I", h.emu.read(0x7FD754, 4))[0]
        self.assertLessEqual(abs(ticks - raster), 1,
                             "check 3: ff_ticks tracks the live raster 1:1")
        self.assertEqual(st[0x13], 1, "ff_cal_ok")
        self.assertEqual(st[0x0C], ff.MODE_OK, "check 5: frames arrived")
        self.assertGreater(struct.unpack_from(">I", st, 0x2C)[0], 0,
                           "check 5: ff_rk_calls counts segments")
        self.assertEqual(h.runner.errors, [])

    def test_no_frames_is_fault_at_f_1024(self):
        h = handlers()
        drive(h.runner, 3.0, frames=False)
        st = h.emu.read(PATCH_RAM, 0x40)
        self.assertEqual(st[0x0C], ff.MODE_FAULT, "check 4")
        self.assertEqual(struct.unpack_from(">H", st, 0x0A)[0], 1024, "check 4")

    def test_the_task_set_selects_the_hook(self):
        for task_set, want in (("A", 1), ("B", 2)):
            with self.subTest(task_set=task_set):
                h = handlers(ram=AnimatedRam(live_task_set=task_set,
                                             statics=dict(DEFAULT_STATICS)))
                drive(h.runner, 1.0)
                self.assertEqual(h.emu.read(PATCH_RAM, 0x40)[0x1E], want)

    def test_the_segment_hook_never_lets_rk_compound(self):
        """The stub scales 0x803038 in place; the upstream value is re-made.

        Counted in activations, not seconds (brief G5): `drive` reaches the
        simulated target whatever the host load, the runner has run exactly
        600 activations, and the segment count is the one the animated rpm
        ramp predicts for them -- so the check cannot flake under a loaded
        suite and still proves the hook ran many times without compounding.
        """
        h = handlers()
        drive(h.runner, 6.0)
        self.assertEqual(h.runner.ticks, 600, "6 s of 10 ms activations")
        rk = struct.unpack(">H", h.emu.read(0x803038, 2))[0]
        base = h.ram.rk_base(h.runner.sim_t)
        f = struct.unpack_from(">H", h.emu.read(PATCH_RAM, 0x40), 0x0A)[0]
        self.assertLessEqual(rk, (base * f >> 10) + 2)
        # rpm * cylinders / 120 per second, summed over the 600 activations
        want = sum(h.ram.rpm(0.01 * k) * h.ram.cylinders / 120.0 * 0.01
                   for k in range(1, 601))
        self.assertLessEqual(abs(h.runner.segments - int(want)), 1)
        self.assertGreater(h.runner.segments, 100)

    def test_dwkrz_is_never_positive(self):
        """ignition.md section 5: the knock retard array is s8 and <= 0."""
        h = handlers()
        for t in (0.0, 3.0, 7.0, 11.0, 15.0):
            h.ram.apply(h.emu, t)
            raw = h.emu.read(0x7FCE57, 6)
            with self.subTest(t=t):
                self.assertTrue(all((b - 256 if b & 0x80 else b) <= 0
                                    for b in raw), raw.hex())


# ------------------------------------------- Flash 1, the other patch -------
@requires_dump
@requires_sim
class TestTheRunnerDrivesFlash1Too(DumpUnchanged):
    """`--sim-patch patches/ff_counter` runs that patch's own stubs.

    `PatchRunner` used to look its hook up by the literal name
    `ff_fuel_hook_a`; it now takes whichever symbol ends in `_hook_a` /
    `_hook_b`, so a Flash-1 rehearsal exercises the real trampolines of
    brief F1 rather than `AnimatedRam`'s stand-in.
    """

    FF_COUNTER = REPO / "patches" / "ff_counter"

    def _run(self, task_set: str, seconds: float = 1.0):
        h = Med9Handlers(str(DUMP), patch_dir=str(self.FF_COUNTER),
                         ram=AnimatedRam(live_task_set=task_set,
                                         statics=dict(DEFAULT_STATICS)))
        # simulated time, not wall time: advance() may stop on its budget
        while h.runner.sim_t + h.runner.tick_s <= seconds + 1e-9:
            h.runner.advance(seconds + 1e-9)
        return h

    def test_the_set_a_stub_counts_and_names_itself(self):
        h = self._run("A")
        block = h.emu.read(PATCH_RAM, 8)
        self.assertEqual(struct.unpack_from(">I", block, 0)[0], h.runner.ticks)
        self.assertEqual(struct.unpack_from(">H", block, 4)[0], 0xFC01)
        self.assertEqual(block[6], 1, "ff_src_seen = set A")
        self.assertEqual(block[7], 0, "ff_reserved")
        self.assertEqual(h.runner.errors, [])
        self.assertGreater(h.runner.ticks, 10)

    def test_the_set_b_stub_names_itself(self):
        h = self._run("B")
        self.assertEqual(h.emu.read(PATCH_RAM, 8)[6], 2)
        self.assertEqual(h.runner.errors, [])

    def test_a_stock_image_has_no_flash1_block(self):
        h = Med9Handlers(str(DUMP), animate=True,
                         ram=AnimatedRam(live_task_set="A",
                                         statics=dict(DEFAULT_STATICS)))
        self.assertFalse(h.ram.flash1)
        h.ram.apply(h.emu, 30.0)
        self.assertEqual(h.emu.read(PATCH_RAM, 8), bytes(8))

    def test_the_patched_image_switches_the_stand_in_on(self):
        """Without a runner, the image alone is enough to animate it."""
        from ecu_sim import apply_patch_to_temp
        image = apply_patch_to_temp(str(self.FF_COUNTER), str(DUMP))
        h = Med9Handlers(image, animate=True,
                         ram=AnimatedRam(live_task_set="A",
                                         statics=dict(DEFAULT_STATICS)))
        self.assertTrue(h.ram.flash1)
        self.assertTrue(h.ram.owns_patch_ram)
        h.ram.apply(h.emu, 5.0)
        self.assertEqual(struct.unpack(">I", h.emu.read(PATCH_RAM, 4))[0], 500)
        self.assertEqual(h.emu.read(PATCH_RAM + 6, 1), b"\x01")


# --------------------------------- the DDLI path, across several ids --------
@requires_dump
@requires_sim
class TestDdliAcrossSeveralIds(DumpUnchanged):
    """`ddli_def_table`'s entry-array pointers (kwp.md 4.1, 2026-09-17).

    The session file has grown past one dynamic id, and every id has its own
    entry array -- 0x80366C for 0xF0, 0x80370C + (n-1)*0x18 for 0xF1..0xF9.
    `ddli_init` (0x12E39C) fills those pointers, and the emulator has no OS to
    run it, so `Med9Handlers.power_on` calls it.  Without that, all ten ids
    share one array at address 0: the second id defined overwrites the first
    one's entries and the writes land on the exception branch table.
    """

    SLOT = 0x804038

    @staticmethod
    def define(h, lid, chunks):
        h.handle(bytes([0x2C, lid, 0x04]))
        payload = bytearray([0x2C, lid])
        pos = 1
        for addr, size in chunks:
            payload += bytes([0x03, pos, size, (addr >> 16) & 0xFF,
                              (addr >> 8) & 0xFF, addr & 0xFF])
            pos += size
        return h.handle(bytes(payload))[-1]

    def test_power_on_filled_the_entry_array_pointers(self):
        h = handlers()
        want = [0x80366C] + [0x80370C + i * 0x18 for i in range(9)]
        got = [struct.unpack(">I", h.emu.read(self.SLOT + 8 * i + 4, 4))[0]
               for i in range(10)]
        self.assertEqual(got, want)

    def test_a_second_dynamic_id_does_not_clobber_the_first(self):
        h = handlers(animate=False)
        h.runner = None
        base = 0x807400
        h.emu.write(base, bytes(range(0x10, 0x10 + 24)))
        h.handle(b"\x10\x89")
        first = [(base + i, 1) for i in range(20)]
        second = [(base + 20 + i, 1) for i in range(3)]
        self.assertEqual(self.define(h, 0xF0, first), b"\x6c\xf0")
        self.assertEqual(self.define(h, 0xF1, second), b"\x6c\xf1")
        self.assertEqual(h.handle(b"\x21\xf0")[-1][2:],
                         bytes(range(0x10, 0x24)))
        self.assertEqual(h.handle(b"\x21\xf1")[-1][2:],
                         bytes(range(0x24, 0x27)))

    def test_defining_ids_leaves_the_exception_branch_table_alone(self):
        h = handlers(animate=False)
        h.runner = None
        before = h.emu.read(0x000000, 0x100)
        h.handle(b"\x10\x89")
        for lid in (0xF0, 0xF1, 0xF2):
            self.define(h, lid, [(0x7FFB00, 2), (0x7FFB02, 2)])
        self.assertEqual(h.emu.read(0x000000, 0x100), before,
                         "entries must go to 0x80366C/0x80370C, not to 0")

    def test_the_whole_session_file_reads_back_byte_for_byte(self):
        """Every chunk of every id, against a direct read of the same RAM."""
        sess = med9log.load_session(str(SESSION), str(PATCH_JSON))
        plan = med9log.plan_chunks(sess.variables)
        h = handlers(animate=False)
        drive(h.runner, 2.0)
        h.runner = None
        h.handle(b"\x10\x89")
        for lid in plan.ids:
            self.define(h, lid, [(c.address, c.size) for c in plan.chunks[lid]])
        for lid in plan.ids:
            with self.subTest(lid=hex(lid)):
                got = h.handle(bytes([0x21, lid]))[-1]
                self.assertEqual(got[:2], bytes([0x61, lid]))
                want = b"".join(h.emu.read(c.address, c.size)
                                for c in plan.chunks[lid])
                self.assertEqual(got[2:], want)

    def test_the_firmware_still_refuses_more_than_its_budget(self):
        """20 entries on 0xF0, 3 on the others -- tbl_ddli_max_entries."""
        h = handlers(animate=False)
        h.runner = None
        h.handle(b"\x10\x89")
        base = 0x807400
        self.assertEqual(self.define(h, 0xF0, [(base + i, 1) for i in range(21)]),
                         b"\x7f\x2c\x12")
        self.assertEqual(self.define(h, 0xF1, [(base + i, 1) for i in range(4)]),
                         b"\x7f\x2c\x12")
        self.assertEqual(self.define(h, 0xF1, [(base + i, 1) for i in range(3)]),
                         b"\x6c\xf1")


# ------------------------------------------- the measuring blocks over KWP ---
@requires_dump
@requires_sim
class TestGroupsOnTheRunningPatch(DumpUnchanged):
    def test_group_111_and_108_answer_from_the_live_state(self):
        from med9kwp import vag_formulas
        h = handlers()
        drive(h.runner, 20.0)
        h.handle(b"\x10\x89")
        answer = h.handle(b"\x21\x6f")[-1]
        self.assertEqual(answer[:2], b"\x61\x6f")
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(4)]
        e, f, t, mode = (vag_formulas.decode(*x) for x in triples)
        st = h.emu.read(PATCH_RAM, 0x40)
        self.assertAlmostEqual(e.value, struct.unpack_from(">H", st, 0x34)[0],
                               places=3)
        self.assertGreaterEqual(f.value, 100.0)
        self.assertAlmostEqual(t.value, 25.0, places=3)
        self.assertEqual(mode.value % 256, st[0x0C])

        answer = h.handle(b"\x21\x6c")[-1]
        triples = [tuple(answer[2 + 3 * i:5 + 3 * i]) for i in range(4)]
        self.assertEqual([x[0] for x in triples], [0x21, 0x22, 0x22, 0x36])
        dzw = vag_formulas.decode(*triples[1])
        self.assertEqual(dzw.unit, "degCA",
                         "formula 0x22 with A = 0x4B is an ignition angle")
        self.assertEqual(dzw.value, 0.0,
                         "ff_zw_enable ships at 0, so the offset must be 0")
        worst = vag_formulas.decode(*triples[2])
        self.assertLessEqual(worst.value, 0.0, "dwkrz can only take advance")


# --------------------------------------------------- EEPROM persistence -----
@requires_dump
@requires_sim
class TestPersistenceThroughTheSimulator(DumpUnchanged):
    def test_an_eeprom_file_is_created_read_back_and_written_back(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "eeprom.bin"
        h = handlers(eeprom=str(path))
        self.assertTrue(path.exists(), "a factory image is created if missing")
        self.assertEqual(len(path.read_bytes()), qe.DEVICE_SIZE)
        self.assertEqual(h.emu.read(0x7F9F80, 2), bytes([8, 1]),
                         "the start-up read filled the block-8 mirror")

        # a distinctive byte at a payload offset that is NOT the id stamp
        h.eeprom.mem[0x1C2] = 0x5A
        saved = h.save_eeprom()
        self.assertEqual(saved, str(path))
        self.assertEqual(path.read_bytes()[0x1C2], 0x5A)

        again = handlers(eeprom=str(path))
        self.assertEqual(again.eeprom.mem[0x1C2], 0x5A,
                         "a restart sees what the last run left on the device")

    def test_the_patch_reads_the_store_at_its_calibrated_offset(self):
        """E5's fix of the D2 bug (#38): the factory record reads back, a
        stored byte at the calibrated payload offset reads back, and the
        {id, version} stamp at +0/+1 is what the manager validates, not what
        we read.

        The block and offset are read from `patches/ff_fuel/ffcal001.json`
        (via `bench_rehearsal.persist_location`), never restated: brief G7
        moves the offset from 2 to 19 and this test must follow it.
        """
        import bench_rehearsal
        loc = bench_rehearsal.persist_location()
        block, off = loc["block"], loc["offset"]
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "eeprom.bin"
        factory = qe.factory_image(str(DUMP))
        path.write_bytes(factory)
        h = handlers(eeprom=str(path))
        mirror = loc["mirror"] - off
        self.assertEqual(h.emu.read(mirror, 2), bytes([block, 1]),
                         "the start-up read filled the mirror, stamp first")
        drive(h.runner, 0.5, frames=False)
        st = h.emu.read(PATCH_RAM, 0x40)
        self.assertEqual(st[0x33], 2, "the read itself succeeded")
        factory_byte = factory[loc["eeprom"]]
        if factory_byte <= 100:
            self.assertEqual(struct.unpack_from(">H", st, 0x30)[0],
                             factory_byte,
                             f"the factory record's {loc['label']} reads back "
                             "(0x00 = E0 at +2), not the block id "
                             "(eeprom.md section 10.5)")

        # a stored value behind the stamp survives a restart and is read back
        payload = bytes(h.emu.read(mirror, off)) + bytes([55])
        path.write_bytes(qe.factory_image(str(DUMP), payloads={block: payload}))
        h2 = handlers(eeprom=str(path))
        self.assertEqual(h2.emu.read(mirror, off + 1), payload,
                         "the start-up read accepted the block")
        drive(h2.runner, 0.5, frames=False)
        st2 = h2.emu.read(PATCH_RAM, 0x40)
        self.assertEqual(struct.unpack_from(">H", st2, 0x30)[0], 55,
                         f"ff_persist_offset = {off} reads the stored E%")
        self.assertEqual(st2[0x33], 2)


# ------------------------------------------------ the logger, end to end ----
@requires_dump
@requires_sim
class TestLoggerEndToEnd(DumpUnchanged):
    """One short `med9log log --sim --sim-patch --sim-node` run."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not have_can():                                   # pragma: no cover
            raise unittest.SkipTest("python-can missing")
        cls.tmp = Path(tempfile.mkdtemp())
        cls.out = cls.tmp / "run.csv"
        args = types.SimpleNamespace(
            bus="virtual:unused", sim=True, sim_dump=None,
            sim_patch=str(FF_FUEL), eeprom=str(cls.tmp / "eeprom.bin"),
            sim_task_set="A", time_scale=5.0, sim_stock_tasks=False,
            sim_node=True, node_e_pct=85, node_temp=25, node_status=0,
            node_e_ramp=None, node_stall=False, node_stop_after=None,
            node_implausible=False, node_not_ready=False,
            node_fault_after=None,
            address=0x01, rx_id=0x300, timeout=1.0,
            session=str(SESSION), output=str(cls.out), seconds=3.0,
            sim_seconds=None, rate=5.0, raw=False, patch=str(PATCH_JSON))
        cls.rc = med9log.cmd_log(args)
        rows = [r for r in csv.reader(cls.out.read_text().splitlines())
                if r and not r[0].lstrip().startswith("#")][1:]
        cls.series = {}
        for t, name, value, *_ in rows:
            cls.series.setdefault(name, []).append((float(t), float(value)))

    def last(self, name):
        return self.series[name][-1][1]

    def test_the_run_finished_and_the_log_is_labelled_simulated(self):
        self.assertEqual(self.rc, 0)
        head = [ln for ln in self.out.read_text().splitlines()
                if ln.startswith("#")]
        self.assertIn("# simulated: true", head)
        self.assertTrue(any(ln.startswith("# sim_patch:") for ln in head))

    def test_checks_1_to_5_of_the_session_file(self):
        self.assertEqual(self.last("ff_magic"), MAGIC, "check 1")
        self.assertEqual(self.last("ff_length"), STATE_LEN, "check 1")
        self.assertEqual(self.last("ff_src_seen"), 1, "check 2")
        self.assertEqual(self.last("ff_src_owner"), 1, "check 2")
        self.assertLessEqual(
            abs(self.last("ff_ticks") - self.last("raster_setA_10ms_count")), 2,
            "check 3")
        self.assertEqual(self.last("ff_mode"), 1, "check 5: OK with the node")
        self.assertGreater(self.last("ff_frames"), 0, "check 5")
        self.assertEqual(self.last("ff_e_raw"), 85, "check 5")
        self.assertEqual(self.last("can_rx_spare0_b0"), 85,
                         "check 5: the driver shadow carries the frame")
        self.assertGreater(self.last("ff_rk_calls"), 0, "check 5")
        self.assertGreater(self.last("ff_e_filt"), 0, "check 5")
        self.assertGreaterEqual(self.last("ff_f_q10"), 1.0, "check 5")

    def test_the_slew_limit_holds_in_ecu_time(self):
        e = self.series["ff_e_filt"]
        clock = self.series["raster_setA_10ms_count"]
        ecu = (clock[-1][1] - clock[0][1]) / 100.0
        self.assertGreater(ecu, 1.0)
        self.assertLessEqual((e[-1][1] - e[0][1]) / ecu, 2.05,
                             "ff_e_filt may rise at most 2 %/s of ECU time")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
