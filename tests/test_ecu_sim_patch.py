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
    """Advance the simulated clock, feeding the node's frames at 10 Hz."""
    step, n = 0.1, int(seconds / 0.1)
    for i in range(n):
        if frames:
            runner.on_frame(0x0EC, ff.frame(e_pct=e_pct, t_fuel_c=temp_c,
                                            counter=i & 0xFF, status=status))
        runner.advance(runner.sim_t + step)


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
        """The stub scales 0x803038 in place; the upstream value is re-made."""
        h = handlers()
        drive(h.runner, 6.0)
        rk = struct.unpack(">H", h.emu.read(0x803038, 2))[0]
        base = h.ram.rk_base(h.runner.sim_t)
        f = struct.unpack_from(">H", h.emu.read(PATCH_RAM, 0x40), 0x0A)[0]
        self.assertLessEqual(rk, (base * f >> 10) + 2)
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
        """And with the shipped offset 0 it reads the block id, not an E%."""
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "eeprom.bin"
        path.write_bytes(qe.factory_image(str(DUMP)))
        h = handlers(eeprom=str(path))
        drive(h.runner, 0.5, frames=False)
        st = h.emu.read(PATCH_RAM, 0x40)
        self.assertEqual(struct.unpack_from(">H", st, 0x30)[0], 8,
                         "ff_persist_offset = 0 sits on the {block id} byte, "
                         "so the patch reads 8 % (eeprom.md section 10.5)")
        self.assertEqual(st[0x33], 2, "the read itself succeeded")


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
