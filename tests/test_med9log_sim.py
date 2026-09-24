"""logging/med9log.py's --sim pass-through options and dump block count (brief H4).

--sim-t-bg-ms and --sim-seed-dtc hand ecu_sim's T_bg and DTC seeding through
to the in-process simulator; --sim-flash-crc's help states the 4,926-loop
publish; `dump` counts TransferData blocks per range.

    ./.venv/bin/python3 -m unittest tests.test_med9log_sim
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.common import DumpUnchanged, REPO

for _p in (str(REPO / "logging"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import can  # noqa: F401
    HAVE_CAN = True
    CAN_WHY = ""
except Exception as exc:                                     # pragma: no cover
    HAVE_CAN = False
    CAN_WHY = f"python-can is not installed ({exc}); pip install -r requirements.txt"

requires_can = unittest.skipUnless(HAVE_CAN, CAN_WHY)

if HAVE_CAN:
    import med9log


def _sim_args(**kw) -> argparse.Namespace:
    base = dict(sim=True, sim_dump=None, sim_patch=None, eeprom=None,
                sim_task_set="A", time_scale=1.0, sim_stock_tasks=False,
                sim_flash_crc=False, sim_t_bg_ms=None, sim_seed_dtc=[])
    base.update(kw)
    return argparse.Namespace(**base)


def _help(command: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        med9log.main([command, "--help"])
    return " ".join(buf.getvalue().split())


@requires_can
class TestHelpText(unittest.TestCase):
    def test_flash_crc_help_names_the_loop_count_not_a_fixed_time(self):
        text = _help("probe")
        self.assertIn("4,926 background loops of T_bg", text)
        self.assertIn("0.51-300.75 ms", text)
        self.assertNotIn("It needs 246 simulated seconds", text)

    def test_the_two_new_options_are_offered_on_every_sim_command(self):
        for command in ("probe", "log", "groups", "dump"):
            text = _help(command)
            with self.subTest(command=command):
                self.assertIn("--sim-t-bg-ms", text)
                self.assertIn("--sim-seed-dtc", text)


@requires_can
class TestFlashCrcArg(unittest.TestCase):
    def test_defaults_are_unchanged(self):
        self.assertIs(med9log._sim_flash_crc_arg(_sim_args()), False)
        self.assertIs(med9log._sim_flash_crc_arg(_sim_args(sim_flash_crc=True)), True)
        # an args object from an older caller without the new attributes
        self.assertIs(med9log._sim_flash_crc_arg(argparse.Namespace()), False)

    def test_t_bg_passes_through_as_milliseconds(self):
        self.assertEqual(med9log._sim_flash_crc_arg(
            _sim_args(sim_flash_crc=True, sim_t_bg_ms=10.0)), 10.0)

    def test_t_bg_without_the_crc_task_is_refused(self):
        with self.assertRaises(SystemExit):
            med9log._sim_flash_crc_arg(_sim_args(sim_t_bg_ms=10.0))

    def test_t_bg_outside_the_static_bound_is_refused(self):
        for bad in (0.5, 301.0):
            with self.subTest(t_bg=bad), self.assertRaises(SystemExit):
                med9log._sim_flash_crc_arg(_sim_args(sim_flash_crc=True,
                                                     sim_t_bg_ms=bad))


@requires_can
class TestPassThrough(DumpUnchanged):
    def _start(self, **kw):
        sim = med9log.start_simulator(_sim_args(**kw))
        self.addCleanup(sim.close)
        return sim.handlers

    def test_sim_t_bg_ms_reaches_the_flash_crc_task(self):
        h = self._start(sim_flash_crc=True, sim_t_bg_ms=10.0)
        self.assertIsNotNone(h.flash_crc)
        self.assertAlmostEqual(h.flash_crc.bg_loop_s, 0.010)

    def test_sim_seed_dtc_reaches_the_fault_memory(self):
        h = self._start(sim_seed_dtc=["P0601"])
        self.assertEqual(h.dtc.used, 1)
        h0 = self._start()
        self.assertEqual(h0.dtc.used, 0)


@requires_can
class TestDumpBlockCount(DumpUnchanged):
    def test_blocks_are_counted_per_range(self):
        # two 64-byte ranges: 2 + 2 blocks on the wire, not ceil(128 / 62) = 3
        with tempfile.TemporaryDirectory() as td:
            ranges = Path(td) / "r.json"
            ranges.write_text(json.dumps({"ranges": [
                {"start": "0x7FF000", "end": "0x7FF03F"},
                {"start": "0x7FF100", "end": "0x7FF13F"}]}), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = med9log.main(["dump", "--sim", "--ranges", str(ranges),
                                   "-o", str(Path(td) / "s.bin")])
            self.assertEqual(rc, 0)
            self.assertIn("2 ranges, 128 bytes, 4 TransferData blocks",
                          buf.getvalue())
            self.assertEqual((Path(td) / "s.bin").stat().st_size, 128)


if __name__ == "__main__":
    unittest.main()
