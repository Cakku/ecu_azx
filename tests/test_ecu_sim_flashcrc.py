"""The flash CRC task's period, the warm ECU and a patched image (brief G5, #20).

G3 (`re/findings/boot.md` 6.8, 6.5(d)) showed that `flash_crc_task`
(0x11CB10) is not a 10 ms raster but five consecutive processes of the set-A
background task 0: one background loop T_bg hashes 500 bytes, the value
appears in loop 4,926, and T_bg is bounded 0.51 ms .. 300.75 ms.  These tests
pin how `logging/ecu_sim.py`'s `FlashCrcTask` applies that:

* one loop = five activations = the cursor 0x7FB700 moves by 0x1F4, and the
  loop counter 0x7FD70C by one -- the number G3 measured by running task 0's
  processes (0x20190 -> 0x20384 -> 0x20578);
* T_bg is a parameter, refused outside the VERIFIED-STATIC bound, and the
  simulated clock runs one loop per T_bg;
* a warm ECU (the task already published this power cycle) is state 7 and
  only the loop counter moves;
* a patched image publishes a different value, and the firmware's own
  state machine agrees with `FlashCrcTask.expected` over the patched tail.

The full cold run (4,926 loops, ~30 s) lives once, in
`tests/test_ecu_sim_initstate.py::TestFlashCrcTask`.  Nothing here writes to
`data/` and nothing is ever flashed.
"""
from __future__ import annotations

import contextlib
import io
import struct
import sys
import unittest
import zlib

from tests.common import DUMP, REPO, DumpUnchanged, requires_dump

sys.path.insert(0, str(REPO / "logging"))

try:
    import ecu_sim
    from ecu_sim import (BG_LOOP_COUNTER, BG_LOOP_MS_DEFAULT, BG_LOOP_MS_MAX,
                         BG_LOOP_MS_MIN, FLASH_CRC_LOOPS, FlashCrcTask,
                         Med9Handlers)
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_sim = unittest.skipUnless(available, "unicorn / python-can missing")

STOCK_CRC = 0x5562139F
CURSOR = 0x7FB700


def handlers(**kw) -> "Med9Handlers":
    kw.setdefault("animate", False)
    return Med9Handlers(str(DUMP), **kw)


def u32(emu, addr: int) -> int:
    return struct.unpack(">I", emu.read(addr, 4))[0]


@requires_dump
@requires_sim
class TestTheBackgroundLoop(DumpUnchanged):
    def test_the_constants_are_g3s(self):
        self.assertEqual(FLASH_CRC_LOOPS, -(-24627 // 5))
        self.assertEqual((BG_LOOP_MS_MIN, BG_LOOP_MS_MAX), (0.51, 300.75))
        self.assertTrue(BG_LOOP_MS_MIN <= BG_LOOP_MS_DEFAULT <= BG_LOOP_MS_MAX)
        self.assertEqual(round(0x100FD7 / 3500.0, 2), 300.75,
                         "G3: deadline timer 1 = 0x100FD7 ticks at 3.5 per us")

    def test_one_loop_moves_the_cursor_by_500_bytes(self):
        """boot.md 6.8(c): 0x20190 -> 0x20384 -> 0x20578, 0x1F4 per loop."""
        h = handlers(flash_crc=True)
        c = h.flash_crc
        c.loop()                        # includes state 0, the table build
        cursors = [c.cursor]
        for _ in range(3):
            self.assertTrue(c.loop())
            cursors.append(c.cursor)
        steps = {b - a for a, b in zip(cursors, cursors[1:])}
        self.assertEqual(steps, {500}, [hex(x) for x in cursors])
        self.assertEqual(c.activations, 20)
        self.assertEqual(c.loops, 4)
        self.assertEqual(u32(h.emu, BG_LOOP_COUNTER), 4)
        self.assertEqual(c.errors, [])

    def test_t_bg_sets_the_simulated_rate(self):
        """One loop per T_bg of simulated time, counted, not timed."""
        for t_bg in (BG_LOOP_MS_MAX, 100.0):
            with self.subTest(t_bg=t_bg):
                c = handlers(flash_crc=t_bg).flash_crc
                self.assertAlmostEqual(c.bg_loop_s, t_bg / 1000.0)
                target = 1.5
                # advance() may stop early on its wall budget; it always makes
                # progress, so call it until the simulated clock is there
                while c.loops < int(target / c.bg_loop_s + 1e-9):
                    c.advance(target)
                self.assertEqual(c.loops, int(target / c.bg_loop_s + 1e-9))
                self.assertEqual(c.loop_counter, c.loops)
                self.assertAlmostEqual(c.publish_s, 4926 * t_bg / 1000.0)

    def test_t_bg_outside_the_static_bound_is_refused(self):
        for bad in (0.1, 301.0):
            with self.subTest(t_bg=bad), self.assertRaises(ValueError):
                handlers(flash_crc=bad)

    def test_the_engine_speed_gate_still_holds_the_cursor(self):
        """nmot_w >= 0xFFFF: the task returns at once, the loop still counts."""
        h = handlers(flash_crc=True)
        c = h.flash_crc
        c.loop()
        h.emu.write(0x7FEE74, b"\xff\xff")
        before = c.cursor
        c.loop()
        self.assertEqual(c.cursor, before)
        self.assertEqual(c.loop_counter, 2)


@requires_dump
@requires_sim
class TestTheWarmEcu(DumpUnchanged):
    def test_a_warm_ecu_is_done_and_nothing_but_the_counter_moves(self):
        h = handlers(flash_crc_warm=True)
        c = h.flash_crc
        self.assertEqual(c.state, 7)
        self.assertTrue(c.done)
        self.assertEqual(c.crc, STOCK_CRC)
        frozen = h.emu.read(0x7FB6F4, 0x10) + h.emu.read(0x7F9176, 6)
        counter = c.loop_counter
        c.advance(10.0)
        self.assertEqual(h.emu.read(0x7FB6F4, 0x10) + h.emu.read(0x7F9176, 6),
                         frozen, "state, cursor, register and value all still")
        self.assertEqual(c.activations, 0, "a parked task is not even called")
        self.assertEqual(c.loop_counter, counter + int(10.0 / c.bg_loop_s))
        self.assertFalse(c.activate(), "and if it is, it returns at once")

    def test_a_cold_ecu_is_not(self):
        c = handlers(flash_crc=True).flash_crc
        self.assertEqual(c.state, 0)
        self.assertFalse(c.done)
        self.assertEqual(c.crc, 0)
        self.assertEqual(c.loop_counter, 0)


@requires_dump
@requires_sim
class TestAPatchedImageReportsADifferentValue(DumpUnchanged):
    """flash_crc.json item 6: recompute, and the value must move."""

    def test_expected_is_the_stock_value_for_the_stock_dump(self):
        self.assertEqual(FlashCrcTask.expected(str(DUMP)), STOCK_CRC)

    def test_both_patches_move_it(self):
        for patch in ("ff_counter", "ff_fuel"):
            with self.subTest(patch=patch):
                image = ecu_sim.apply_patch_to_temp(
                    str(REPO / "patches" / patch), str(DUMP))
                crc = FlashCrcTask.expected(image)
                self.assertNotEqual(crc, STOCK_CRC)

    def test_the_firmware_agrees_over_the_patched_tail(self):
        """Run the real task from just before FFCAL001 to the publish.

        The prefix is zlib's (the same arithmetic `expected` uses); the tail
        holds `patches/ff_fuel`'s calibration block and is hashed by the
        firmware's own state machine, which must end on the value `expected`
        predicted for the whole patched image -- and not on the stock one.
        """
        import med9lib as ml
        h = handlers(patch_dir=str(REPO / "patches" / "ff_fuel"),
                     run_patch=False)
        data = bytes(h.emu.dump)
        ranges = FlashCrcTask.read_ranges(
            lambda a, n: data[ml.cpu_to_file(a):ml.cpu_to_file(a) + n])
        start_tail = 0x5E2400                   # below FFCAL001 at 0x5E2510
        idx = next(i for i, (lo, hi) in enumerate(ranges) if lo <= start_tail <= hi)
        prefix = 0
        for lo, hi in ranges[:idx]:
            prefix = zlib.crc32(data[ml.cpu_to_file(lo):ml.cpu_to_file(hi) + 1],
                                prefix)
        lo, hi = ranges[idx]
        prefix = zlib.crc32(data[ml.cpu_to_file(lo):ml.cpu_to_file(start_tail)],
                            prefix)
        stock = ml.load_dump(str(DUMP))
        off = ml.cpu_to_file(0x5E2510)
        self.assertNotEqual(data[off:off + 8], bytes(stock[off:off + 8]),
                            "the tail really contains the patch")

        task = FlashCrcTask(h.emu)
        task.activate()                                  # state 0: the table
        h.emu.write(0x7FB6F4, 1, 1)                      # state 1, hashing
        h.emu.write(0x7FB6F5, idx, 1)
        h.emu.write(0x7FB6F6, 100, 2)
        h.emu.write(0x7FB6F8, prefix ^ 0xFFFFFFFF, 4)    # the running register
        h.emu.write(CURSOR, start_tail, 4)
        h.emu.write(0x7FB6FC, hi, 4)
        crc = task.run_to_completion(max_loops=400)
        self.assertEqual(task.errors, [])
        self.assertEqual(crc, FlashCrcTask.expected(data))
        self.assertNotEqual(crc, STOCK_CRC)

    def test_the_cli_prints_it(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ecu_sim.main(["--dump", str(DUMP), "--print-flash-crc"])
        self.assertEqual(rc, 0)
        self.assertTrue(buf.getvalue().startswith("0x5562139f"), buf.getvalue())


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
