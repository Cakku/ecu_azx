"""`logging/ethanol_frame_send.py`: the Mac-side stand-in for the Pico node.

The encoder is checked against `tools/ethanol_frame_decode.py` rather than
against a second copy of the layout, and the six fault knobs are checked
against the six rows of the #37 matrix in
`patches/ff_fuel/test/procedure.md` section 5 (brief E4).
"""
from __future__ import annotations

import sys
import unittest

from tests.common import REPO

sys.path.insert(0, str(REPO / "logging"))
sys.path.insert(0, str(REPO / "tools"))

import ethanol_frame_decode as dec  # noqa: E402

try:
    from ethanol_frame_send import EthanolNode, FrameSender
    from med9kwp import open_link, parse_bus_spec
    from med9kwp.can_transport import have_can
    available = True
except Exception:                                            # pragma: no cover
    available = False
requires_send = unittest.skipUnless(available, "python-can missing")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@requires_send
class TestNode(unittest.TestCase):
    def test_the_frame_round_trips_through_the_decoder(self):
        node = EthanolNode(e_pct=73, temp_c=31, fw=2, status=0)
        d = dec.decode(node.frame(0.0))
        self.assertEqual(d.ethanol_pct, 73)
        self.assertEqual(d.fuel_temp_c, 31)
        self.assertEqual(d.fw_version, 2)
        self.assertEqual(d.status, 0)
        self.assertEqual(dec.plausibility_notes(d), [],
                         "a nominal frame must raise no plausibility note")

    def test_the_frequency_byte_matches_the_ethanol_percent(self):
        for e in (0, 20, 50, 85, 100):
            with self.subTest(e=e):
                d = dec.decode(EthanolNode(e_pct=e).frame(0.0))
                # byte 2 is f/2, so an odd frequency loses 1 Hz on the wire
                # (docs/05 section 2); the decoder's own tolerance is +-3 Hz
                self.assertLessEqual(abs(d.freq_hz - (50 + e)), 1,
                                     "docs/05 section 2")
                self.assertEqual(dec.plausibility_notes(d), [])

    def test_the_counter_rolls_and_can_be_stalled(self):
        node = EthanolNode()
        for _ in range(300):
            node.advance_counter(0.0)
        self.assertEqual(node.counter, 300 & 0xFF)
        frozen = EthanolNode(stall=True)
        for _ in range(10):
            frozen.advance_counter(0.0)
        self.assertEqual(frozen.counter, 0)

    def test_a_ramp_is_linear_and_then_holds(self):
        node = EthanolNode(ramp=(0, 80, 40.0))
        self.assertEqual(node.ethanol_at(0.0), 0)
        self.assertEqual(node.ethanol_at(20.0), 40)
        self.assertEqual(node.ethanol_at(40.0), 80)
        self.assertEqual(node.ethanol_at(100.0), 80)

    def test_the_six_matrix_rows(self):
        cases = {
            1: EthanolNode(e_pct=85, status=1),
            2: EthanolNode(e_pct=85, stop_after=5.0),
            3: EthanolNode(e_pct=85, status=2),
            4: EthanolNode(e_pct=85, stall=True),
            5: EthanolNode(e_pct=85, implausible=True),
            6: EthanolNode(e_pct=85, not_ready=True),
        }
        self.assertEqual(dec.decode(cases[1].frame(0)).status, 1)
        self.assertTrue(cases[2].sending(4.9))
        self.assertFalse(cases[2].sending(5.1))
        self.assertEqual(dec.decode(cases[3].frame(0)).status, 2)
        self.assertTrue(cases[4].stall)
        self.assertEqual(dec.decode(cases[5].frame(0)).ethanol_pct, 101)
        self.assertIn("out of range",
                      " ".join(dec.plausibility_notes(dec.decode(
                          cases[5].frame(0)))))
        self.assertEqual(dec.decode(cases[6].frame(0)).status, 3)

    def test_fault_after_keeps_the_node_nominal_first(self):
        """procedure.md section 5 wants a settled estimate before each step."""
        node = EthanolNode(e_pct=85, status=2, stall=True, implausible=True,
                           fault_after=40.0)
        before = dec.decode(node.frame(10.0))
        self.assertEqual((before.status, before.ethanol_pct), (0, 85))
        node.advance_counter(10.0)
        self.assertEqual(node.counter, 1, "the counter runs before the fault")
        after = dec.decode(node.frame(50.0))
        self.assertEqual((after.status, after.ethanol_pct), (2, 101))
        node.advance_counter(50.0)
        self.assertEqual(node.counter, 1, "and freezes after it")


@requires_send
class TestSender(unittest.TestCase):
    def setUp(self):
        if not have_can():                                   # pragma: no cover
            self.skipTest("python-can missing")
        self.link = open_link(parse_bus_spec("virtual:test_ff_send"))
        self.rx = open_link(parse_bus_spec("virtual:test_ff_send"))
        self.rx.set_accept(None)
        self.addCleanup(self.link.close)
        self.addCleanup(self.rx.close)

    def test_it_sends_at_its_own_rate_on_a_virtual_bus(self):
        clock = FakeClock()
        node = EthanolNode(e_pct=60, rate_hz=10.0)
        sender = FrameSender(self.link, node, clock=clock)
        for step in range(100):                       # one simulated second
            clock.t = step * 0.01
            sender.pump()
        self.assertEqual(sender.sent, 10, "10 Hz means ten frames a second")
        got = []
        while True:
            frame = self.rx.recv(0.0)
            if frame is None:
                break
            got.append(frame)
        self.assertEqual(len(got), 10)
        ids = {can_id for can_id, _data in got}
        self.assertEqual(ids, {dec.DEFAULT_ID})
        counters = [dec.decode(data).counter for _id, data in got]
        self.assertEqual(counters, list(range(10)))

    def test_the_clock_scale_stretches_wall_time(self):
        clock = FakeClock()
        sender = FrameSender(self.link, EthanolNode(rate_hz=10.0),
                             clock=clock, scale=5.0)
        clock.t = 1.0
        self.assertAlmostEqual(sender.elapsed(), 5.0,
                              msg="one wall second is five simulated ones")
        clock.t = 0.0
        sender._next = 0.0
        for step in range(100):
            clock.t = step * 0.002              # one simulated second
            sender.pump()
        self.assertEqual(sender.sent, 10)

    def test_stop_after_stops(self):
        clock = FakeClock()
        sender = FrameSender(self.link, EthanolNode(stop_after=0.5),
                             clock=clock)
        for step in range(200):
            clock.t = step * 0.01
            sender.pump()
        self.assertEqual(sender.sent, 5, "half a second at 10 Hz")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
