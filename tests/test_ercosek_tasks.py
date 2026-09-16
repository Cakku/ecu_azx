"""Brief C4 (#44): the ERCOSEK activation chain and the raster periods.

Two layers are checked against each other:

* `tools/ercosek_tasks.py` — the static decode of the task descriptors, the
  cyclic time tables and the two raster divider chains;
* `emu/os_clock.py` — the same dispatchers executed under Unicorn against a
  virtual Time Base, which must reproduce the same periods.

The reference numbers below are the periods of
`re/findings/scheduler.md` section 11.  Note the correction recorded there:
the tasks brief B1 named `task_10ms` (0x11EBF4) and `task_20ms` (0x11EC34)
are the **1 ms and 2 ms** rasters; the 10 ms and 20 ms rasters are
0x1205A0 / 0x120FAC (task set B) and 0x4328E4 / 0x45CAC4 (task set A).
"""
from __future__ import annotations

import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

import ercosek_tasks as et  # noqa: E402

# entry point -> design period in Time Base ticks (285 ns each)
EXPECTED_TICKS = {
    0x4240C8: 3508,        0x11EBF4: 3508,        # 1 ms
    0x424900: 7016,        0x11EC34: 7016,        # 2 ms
    0x424AF8: 17540,       0x11EC58: 17540,       # 5 ms
    0x4328E4: 35087,       0x1205A0: 35087,       # 10 ms
    0x45CAC4: 70174,       0x120FAC: 70174,       # 20 ms
    0x0C78F4: 175435,      0x0BE734: 175435,      # 50 ms
    0x0FB974: 350870,      0x13759C: 350870,      # 100 ms
    0x115AE0: 701740,      0x1210CC: 701740,      # 200 ms
    0x05BD3C: 3508700,     0x123A90: 3508700,     # 1000 ms
}


@requires_dump
class TestStaticDecode(DumpUnchanged):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.res = et.analyse(et.Image(str(DUMP)))
        cls.by_id = {r["id"]: r for r in cls.res["thunks"].values()}

    def test_thirty_seven_tasks(self):
        self.assertEqual(len(self.res["thunks"]), 37)
        ids = sorted(r["id"] for r in self.res["thunks"].values())
        self.assertEqual(len(set(ids)), 37)
        # every activation-counter byte lives in the range C2 measured
        for r in self.res["thunks"].values():
            self.assertTrue(0x7FE5FC <= r["flag"] <= 0x7FE644,
                            "flag %#x out of range" % r["flag"])
            self.assertEqual(r["max_act"], 1)

    def test_alarm_one_cycle_is_ten_ms(self):
        self.assertEqual(self.res["alarm_cycle"], 35087)
        self.assertEqual(self.res["alarm_callbacks"],
                         [0x0B0934, 0x443F74, 0x40C1D8])

    def test_time_tables_are_fifty_ms_cycles(self):
        for tab in self.res["tables"].values():
            self.assertEqual(tab["cycle_ticks"], 35080)      # 10 x 3508
            self.assertEqual(len(tab["entries"]), 18)
            for p in tab["periods"].values():
                self.assertTrue(p["uniform"])

    def test_divider_chains(self):
        for chain in self.res["dividers"].values():
            self.assertEqual([s["divider"] for s in chain],
                             [100, 20, 10, 5, 2])
            self.assertEqual([s["counter"] for s in chain],
                             [0x7FC2E0, 0x7FC2DC, 0x7FC2D8, 0x7FC2D4, 0x7FC2D0])

    def test_every_raster_period(self):
        got = {}
        for tid, ticks in self.res["periods"].items():
            got[self.by_id[tid]["entry"]] = ticks
        self.assertEqual(got, EXPECTED_TICKS)

    def test_the_two_tasks_of_issue_44(self):
        """0x4328E4 = 10 ms, 0x45CAC4 = 20 ms — the acceptance criterion."""
        periods = {self.by_id[t]["entry"]: p
                   for t, p in self.res["periods"].items()}
        self.assertEqual(periods[0x4328E4], 35087)
        self.assertEqual(periods[0x45CAC4], 2 * 35087)
        self.assertAlmostEqual(et.ms(periods[0x4328E4]), 10.0, places=2)
        self.assertAlmostEqual(et.ms(periods[0x45CAC4]), 20.0, places=2)


@requires_dump
class TestOsClock(DumpUnchanged):
    """The emulated clock must reproduce the static periods."""

    SECONDS = 1.0

    def _run(self, task_set: str):
        from emu.os_clock import OsClock, NS_PER_TICK, task_names
        clock = OsClock(str(DUMP), task_set)
        rep = clock.run(int(self.SECONDS * 1e9 / NS_PER_TICK))
        names = task_names(str(DUMP))
        return {names[flag][1]: s for flag, s in rep.items()}, clock

    def test_set_a_periods(self):
        got, _ = self._run("a")
        for entry, stats in got.items():
            self.assertEqual(stats["min_ticks"], stats["max_ticks"],
                             "entry %#x jitters" % entry)
            self.assertEqual(stats["min_ticks"], EXPECTED_TICKS[entry],
                             "entry %#x" % entry)

    def test_set_b_periods(self):
        got, _ = self._run("b")
        for entry, stats in got.items():
            self.assertEqual(stats["min_ticks"], EXPECTED_TICKS[entry],
                             "entry %#x" % entry)

    def test_ten_and_twenty_ms_flags(self):
        """The 10 ms and 20 ms activation flags come out at 10 and 20 ms.

        Task set B carries the rasters whose counters a bench log can read
        (0x7FD758 for 0x1205A0); set A is the one os_init installs.
        """
        for task_set, ten, twenty in (("a", 0x4328E4, 0x45CAC4),
                                      ("b", 0x1205A0, 0x120FAC)):
            got, _ = self._run(task_set)
            self.assertAlmostEqual(got[ten]["period_ms"], 10.0, places=2)
            self.assertAlmostEqual(got[twenty]["period_ms"], 20.0, places=2)

    def test_one_and_two_ms_flags(self):
        """B1's `task_10ms`/`task_20ms` are really the 1 ms and 2 ms rasters."""
        got, _ = self._run("b")
        self.assertAlmostEqual(got[0x11EBF4]["period_ms"], 1.0, places=2)
        self.assertAlmostEqual(got[0x11EC34]["period_ms"], 2.0, places=2)

    def test_alarm_cycle_stays_nominal(self):
        _, clock = self._run("a")
        # os_raster_divider recomputes the alarm-1 cycle every fifth call;
        # with the load byte 0x7FCE95 at its cold-start 0 it stays at 50 ms
        # worth of 10 ms rasters, i.e. the nominal 35087.
        self.assertEqual(clock.alarm_cycle, 35087)
        self.assertTrue(clock.alarm_cycle_writes)
        self.assertTrue(all(a == 1 for a, _ in clock.alarm_cycle_writes))


if __name__ == "__main__":
    unittest.main()
