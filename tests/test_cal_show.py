"""tools/cal_show.py: the naming pass's read-out helper must report the bytes
that are in the dump and nothing else (issue #41, brief F4).

The interesting property is that it is *read-only* and *draft-driven*: every
shape it prints comes from re/calibration_draft.csv, and --guess never invents
a unit that re/findings/ has not established.
"""
from __future__ import annotations

import io
import unittest

from tests.common import REPO, DumpUnchanged  # noqa: F401  (REPO puts tools/ on sys.path)

import cal_show as c  # noqa: E402
import med9lib as m  # noqa: E402

IMAGE = REPO / "data" / "passat_azx_ori.bin"
DRAFT = REPO / "re" / "calibration_draft.csv"

# The tmst axis of KFWKSTT (0x5C6C62), proven in re/findings/start.md section 3.
TMST_AXIS = [24, 31, 37, 44, 55, 64, 84, 91, 101, 117, 144, 184]


class TestParsers(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(c.self_test(), 0)

    def test_parse_number_takes_fractions(self):
        self.assertEqual(c.parse_number("100/4096"), 100.0 / 4096)
        self.assertEqual(c.parse_number("0.75"), 0.75)
        self.assertIsNone(c.parse_number(""))
        self.assertEqual(c.parse_number("", 2.0), 2.0)

    def test_elem_spec(self):
        self.assertEqual(c.elem_spec("u16"), (2, False))
        self.assertEqual(c.elem_spec("s8"), (1, True))
        self.assertEqual(c.elem_spec("s16"), (2, True))
        self.assertEqual(c.elem_spec("", 2, True), (2, True))

    def test_monotone(self):
        self.assertTrue(c.monotone([1, 1, 2, 9]))
        self.assertFalse(c.monotone([1, 9, 2]))


class TestGuessIsAFilter(unittest.TestCase):
    """--guess must reject as well as accept, or it is not evidence."""

    def test_a_temperature_axis_is_not_a_rail_pressure(self):
        self.assertTrue(c.band_fit(TMST_AXIS, 0.75, -48.0, -50.0, 150.0)[2])
        self.assertFalse(c.band_fit(TMST_AXIS, 0.005, 0.0, 1.0, 250.0)[2])

    def test_every_known_unit_cites_a_finding(self):
        for entry in c.KNOWN_UNITS:
            name, unit, scale, offset, lo, hi, source = entry
            self.assertTrue(source.endswith(")") or ".md" in source, entry)
            self.assertLess(lo, hi)
            self.assertNotEqual(scale, 0.0)

    def test_guess_prints_one_line_per_unit(self):
        out = io.StringIO()
        c.guess(TMST_AXIS, out)
        lines = [line for line in out.getvalue().splitlines() if line.startswith("  ")]
        self.assertEqual(len(lines), len(c.KNOWN_UNITS))


class TestAgainstTheDump(DumpUnchanged):
    @unittest.skipUnless(IMAGE.is_file(), "dump missing")
    def test_read_elems_matches_med9lib(self):
        data = m.load_dump(str(IMAGE))
        # KFWKSTT's tmst axis, read as the draft describes it
        self.assertEqual(c.read_elems(data, 0x5C6C62, 12, 1, False), TMST_AXIS)
        # TIMINP, the u16 = 900 of re/findings/injection.md section 1
        self.assertEqual(c.read_elems(data, 0x5C7328, 1, 2, False), [900])
        # KRKATE, u16 = 3858
        self.assertEqual(c.read_elems(data, 0x5D3DBC, 1, 2, False), [3858])

    @unittest.skipUnless(IMAGE.is_file() and DRAFT.is_file(), "files missing")
    def test_main_prints_a_draft_object(self):
        draft = c.load_draft(str(DRAFT))
        self.assertIn("0X5C6C7C", {k.upper() for k in draft})
        row = draft["0x5C6C7C".upper()]
        self.assertEqual(row["kind"], "map_2d")

    @unittest.skipUnless(IMAGE.is_file(), "dump missing")
    def test_an_unknown_address_is_an_error_not_a_guess(self):
        self.assertEqual(c.main([str(IMAGE), "0x5FFFFE"]), 1)


if __name__ == "__main__":
    unittest.main()
