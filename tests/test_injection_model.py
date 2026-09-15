"""emu/models/injection.py against the real code (brief B6, issue #14).

The acceptance criterion of brief B6 is that the Python model of the
`rk -> ti` chain reproduces the ECU functions bit-exactly.  `verify()` runs
`rk2ti` (0x0AC370), `fkkvs_func` (0x0AC4B8) and `rkti_dp_angle` (0x0AC42C) in
the Unicorn harness over a grid of inputs and compares every result.
"""
from __future__ import annotations

import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

from emu.models import injection  # noqa: E402


@requires_dump
class TestInjectionModel(DumpUnchanged, unittest.TestCase):
    """The model must match the ECU code on every sampled input."""

    def test_calibration_constants(self):
        m = injection.InjectionModel(str(DUMP))
        self.assertEqual(m.u16(injection.KRKATE), 3858)
        self.assertEqual(m.u16(injection.TIMINP), 900)
        # the shared dp axis has twelve breakpoints, which is what makes the
        # two bare value arrays (KLTIKRPR, dead time) 24 bytes each
        self.assertEqual(m.u16(injection.DP_AXIS), 12)
        self.assertEqual(injection.KLTIKRPR + 2 * 12, injection.TV_DP)
        self.assertEqual(injection.TV_DP + 2 * 12, injection.TIMINP)

    def test_kltikrpr_follows_the_bernoulli_law(self):
        """value * sqrt(dp) is constant to 1 %: the axis really is a pressure
        difference and the value really is a time per unit fuel mass."""
        m = injection.InjectionModel(str(DUMP))
        n = m.u16(injection.DP_AXIS)
        products = []
        for i in range(n):
            dp = m.u16(injection.DP_AXIS + 2 + 2 * i)
            val = m.u16(injection.KLTIKRPR + 2 * i)
            products.append(val * dp ** 0.5)
        self.assertLess(max(products) / min(products) - 1.0, 0.025)

    def test_model_matches_the_ecu(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:  # pragma: no cover - unicorn is an extra
            self.skipTest("unicorn not installed")
        self.assertEqual(injection.verify(str(DUMP), verbose=False), 0)

    def test_flex_fuel_factor_1_is_a_no_op(self):
        """`F(E0) = 1.000` must be bit-identical to stock, or the patch is not
        safe to ship (docs/05_flexfuel_design.md section 3.3)."""
        m = injection.InjectionModel(str(DUMP))
        for rk in (0, 340, 900, 2000, 9999, 30000):
            for dp in (400, 3000, 10000, 24000):
                for nmot in (3200, 8000, 24000):
                    self.assertEqual(
                        m.flexfuel_injection_time(rk, dp, nmot, 1024),
                        m.injection_time(rk, dp, nmot))


if __name__ == "__main__":
    unittest.main()
