"""emu/models/window.py against the real code (brief B9, issue #17).

The injection window is the safety-relevant limit for E85 at wide-open
throttle, so the acceptance criterion of brief B9 is that the Python model of
`%AWEA`'s angle-synchronous half reproduces the ECU function bit-exactly.
`verify()` runs `awea_ti_to_angle` (0x41B9C0) and the `k_nmot` block
(0x41B9A4) in the Unicorn harness over a grid of engine speeds, start angles
and injection times and compares `dwi` (0x803088) and the resulting
start-of-injection angle (0x80307E).
"""
from __future__ import annotations

import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

from emu.models import window  # noqa: E402


@requires_dump
class TestWindowModel(DumpUnchanged, unittest.TestCase):
    """The model must match the ECU code on every sampled input."""

    def test_window_terms_are_flat_scalars(self):
        """Both window curves are constant in this dataset, which is why the
        window is effectively two scalars (re/findings/rail.md section 9.1)."""
        m = window.WindowModel(str(DUMP))
        self.assertEqual(m.margin_counts(), 67)     # 50.25 degCA of margin
        self.assertEqual(m.latest_counts(), 200)    # -> 360.0 degCA latest start
        # both extra window scalars are zero, so the live check is
        # wbho1s - dwi <= margin * 0x20
        self.assertEqual(m.u8(window.DWBHO1SMN), 0)
        self.assertEqual(m.u8(window.DWBHO1SMX), 0)

    def test_angle_unit_is_three_over_128_degrees(self):
        """The six per-cylinder references are spaced exactly 120 degCA, which
        is what proves the angle LSB and with it ti = 1 us
        (re/findings/rail.md section 14.1)."""
        m = window.WindowModel(str(DUMP))
        refs = [m.u16(0x409CA0 + 2 * i) for i in range(6)]
        self.assertEqual(refs, [3072, 8192, 13312, 18432, 23552, 28672])
        for a, b in zip(refs, refs[1:]):
            self.assertAlmostEqual(m.angle_deg(b - a), 120.0, places=6)
        self.assertAlmostEqual(m.angle_deg(window.CYCLE), 720.0, places=6)
        # one u8 map count is the 0.75 degCA that B7 proved for the ignition
        self.assertAlmostEqual(m.angle_deg(window.MAP_COUNT), 0.75, places=6)

    def test_k_nmot_implies_one_microsecond_per_ti_lsb(self):
        """dwi = (ti * k) >> 13 with k = nmot_w * 34360 >> 16 reproduces
        angle = t * rpm * 6e-6 to better than 0.1 % when ti is 1 us and the
        angle LSB is 3/128 degCA."""
        m = window.WindowModel(str(DUMP))
        for rpm in (1000, 2000, 4000, 6000):
            k = m.k_nmot(rpm * 4)
            ti_us = 4000
            got = m.angle_deg(m.dwi(ti_us, k))
            want = ti_us * rpm * 6e-6
            self.assertLess(abs(got / want - 1.0), 1e-3)

    def test_model_matches_the_ecu(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:  # pragma: no cover - unicorn is an extra
            self.skipTest("unicorn not installed")
        self.assertEqual(window.verify(str(DUMP), verbose=False), 0)

    def test_clamp_only_engages_past_the_margin(self):
        """Below the trigger the start angle must pass through untouched: a
        flex-fuel ti increase changes nothing until the window is reached."""
        m = window.WindowModel(str(DUMP))
        start = m.deg_angle(330.0)
        for rpm in (2000, 4000, 6000):
            k = m.k_nmot(rpm * 4)
            limit = m.max_ti(start, k)
            _, out = m.window_clamp(start, max(limit - 50, 0), k)
            self.assertEqual(out, start)
            _, out = m.window_clamp(start, limit + 200, k)
            self.assertNotEqual(out, start)

    def test_clamp_never_exceeds_the_latest_start(self):
        m = window.WindowModel(str(DUMP))
        cap = m.latest_counts() * window.MAP_COUNT + window.ANGLE_BASE
        for rpm in (1000, 3000, 6000):
            k = m.k_nmot(rpm * 4)
            for ti in (0, 5000, 20000, 65535):
                _, out = m.window_clamp(m.deg_angle(330.0), ti, k)
                self.assertLessEqual(out, cap)


if __name__ == "__main__":
    unittest.main()
