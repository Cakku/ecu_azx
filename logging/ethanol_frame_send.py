#!/usr/bin/env python3
"""The Mac-side stand-in for the Pico flex-fuel node: id 0x0EC at 10 Hz.

`pico_can_sender/` is the real thing and `tools/ethanol_frame_decode.py`
already reads its frames.  This is the other half, so the whole bench chain --
node, ECU, logger -- can be rehearsed on one virtual bus before any hardware
exists (brief E4, issues #37/#39):

    node  ->  logging/ecu_sim.py --sim-patch patches/ff_fuel  ->  med9log.py

The frame layout is **not** restated here: `frame()` builds the eight bytes
and then hands them to `tools/ethanol_frame_decode.decode()` and
`plausibility_notes()`, so the encoder cannot drift away from the decoder
(docs/05 section 2, `data/ethanol_node.dbc`).

The fault knobs are exactly the six rows of the #37 matrix in
`patches/ff_fuel/test/procedure.md` section 5:

===================  =========================================================
``--status 1``       row 1, sensor unplugged: the node keeps sending
``--stop-after S``   row 2, the node loses power after S seconds
``--status 2``       row 3, contaminated fuel -- HOLD, not FAULT
``--stall``          row 4, the rolling counter freezes
``--implausible``    row 5, E > 100 %
``--not-ready``      row 6, status 3 from the first frame
===================  =========================================================

Usage::

    python3 logging/ethanol_frame_send.py --bus virtual:med9 --e-pct 85
    python3 logging/ethanol_frame_send.py --bus gs_usb:0 --e-ramp 0:85:40
    python3 logging/ethanol_frame_send.py --bus virtual:med9 --stall \\
            --seconds 5

From Python, which is what `tests/test_ethanol_frame_send.py` and the
procedure dry-runs do::

    node = EthanolNode(e_pct=85)
    sender = FrameSender(link, node)
    sender.pump(now)              # sends if a frame is due
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "logging"), os.path.join(REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp import open_link, parse_bus_spec  # noqa: E402
from ethanol_frame_decode import (  # noqa: E402
    DEFAULT_ID, STATUS_NAMES, decode, plausibility_notes,
)

STATUS_OK, STATUS_SENSOR_FAULT, STATUS_CONTAMINATED, STATUS_NOT_READY = 0, 1, 2, 3
DEFAULT_RATE_HZ = 10.0


@dataclass
class EthanolNode:
    """One simulated node.  Everything it sends is a function of `t`."""

    e_pct: int = 85
    temp_c: int = 25
    status: int = STATUS_OK
    fw: int = 1
    can_id: int = DEFAULT_ID
    rate_hz: float = DEFAULT_RATE_HZ
    #: `A:B:S` -- ramp the ethanol from A to B over S seconds, then hold B
    ramp: tuple[int, int, float] | None = None
    #: repeat one counter value for ever (procedure.md section 5 row 4)
    stall: bool = False
    #: stop sending after this many seconds (row 2)
    stop_after: float | None = None
    #: report E > 100 %, which the patch must reject (row 5)
    implausible: bool = False
    #: status 3 from the first frame (row 6)
    not_ready: bool = False
    #: simulated second at which the configured fault starts.  procedure.md
    #: section 5 says "Pico running at a steady E-value first (let ff_e_filt
    #: settle) ... Then, one step at a time": before this the node is
    #: nominal, after it the fault applies.  None = from the first frame.
    fault_after: float | None = None

    def __post_init__(self):
        self.counter = 0
        self.sent = 0

    # -- the frame --------------------------------------------------------
    def faulted(self, t: float) -> bool:
        return self.fault_after is None or t >= self.fault_after

    def ethanol_at(self, t: float) -> int:
        if self.implausible and self.faulted(t):
            return 101
        if self.ramp is None:
            return self.e_pct
        a, b, secs = self.ramp
        if secs <= 0:
            return b
        f = min(max(t / secs, 0.0), 1.0)
        return int(round(a + (b - a) * f))

    def status_at(self, t: float) -> int:
        if not self.faulted(t):
            return STATUS_OK
        return STATUS_NOT_READY if self.not_ready else self.status

    def sending(self, t: float) -> bool:
        return self.stop_after is None or t < self.stop_after

    def frame(self, t: float) -> bytes:
        """The eight bytes, checked against the decoder before they go out."""
        e = self.ethanol_at(t)
        freq_hz = 50 + min(max(e, 0), 100)          # docs/05 section 2
        data = bytes((e & 0xFF, (self.temp_c + 40) & 0xFF, (freq_hz // 2) & 0xFF,
                      self.counter & 0xFF, 0, 0, self.fw & 0xFF,
                      self.status_at(t) & 0xFF))
        d = decode(data)                            # the layout, not a copy
        assert d.counter == self.counter & 0xFF
        assert d.fuel_temp_c == self.temp_c
        return data

    def advance_counter(self, t: float | None = None) -> None:
        if not self.stall or (t is not None and not self.faulted(t)):
            self.counter = (self.counter + 1) & 0xFF

    def describe(self, t: float = 0.0) -> str:
        d = decode(self.frame(t))
        notes = plausibility_notes(d)
        return d.line() + ("   [" + "; ".join(notes) + "]" if notes else "")


class FrameSender:
    """Paces an :class:`EthanolNode` onto a `CanLink` at its own rate."""

    def __init__(self, link, node: EthanolNode | None = None, *,
                 t0: float | None = None, clock=time.monotonic,
                 scale: float = 1.0):
        self.link = link
        self.node = node or EthanolNode()
        self.clock = clock
        #: simulated seconds per wall second.  `logging/ecu_sim.py` can be run
        #: with `--time-scale`, and the node has to be stretched the same way
        #: or the ECU would see a 10 Hz frame arrive every 50 simulated ms.
        self.scale = max(scale, 1e-6)
        self.t0 = self.clock() if t0 is None else t0
        self.period = 1.0 / max(self.node.rate_hz, 0.001)
        self._next = 0.0
        self.sent = 0

    def elapsed(self) -> float:
        """Simulated seconds since the node powered up."""
        return (self.clock() - self.t0) * self.scale

    def pump(self, t: float | None = None) -> bool:
        """Send a frame if one is due at simulated second `t`.  True if sent."""
        t = self.elapsed() if t is None else t
        if t < self._next:
            return False
        self._next += self.period
        if self._next < t:                       # a long stall: do not burst
            self._next = t + self.period
        if not self.node.sending(t):
            return False
        data = self.node.frame(t)
        self.link.send(self.node.can_id, data)
        self.node.advance_counter(t)
        self.sent += 1
        self.node.sent = self.sent
        return True

    def run(self, seconds: float, sleep: float = 0.002) -> int:
        """Send for `seconds` **simulated** seconds."""
        deadline = self.elapsed() + seconds
        while self.elapsed() < deadline:
            if not self.pump():
                time.sleep(sleep)
        return self.sent


def _parse_ramp(text: str) -> tuple[int, int, float]:
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--e-ramp takes A:B:SECONDS")
    return int(parts[0], 0), int(parts[1], 0), float(parts[2])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="The six fault knobs are the six rows of the #37 matrix; see "
               "patches/ff_fuel/test/procedure.md section 5.")
    ap.add_argument("--bus", default="virtual:med9",
                    help="interface:channel (default virtual:med9)")
    ap.add_argument("--id", type=lambda s: int(s, 0), default=DEFAULT_ID,
                    help=f"CAN id (default {DEFAULT_ID:#05x})")
    ap.add_argument("--e-pct", type=int, default=85, help="ethanol, 0-100 %%")
    ap.add_argument("--temp", type=int, default=25, help="fuel temperature, degC")
    ap.add_argument("--status", type=int, default=0, choices=(0, 1, 2, 3),
                    help="; ".join(f"{k} {v}" for k, v in STATUS_NAMES.items()))
    ap.add_argument("--fw", type=int, default=1, help="node firmware version")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ,
                    help="frames per second (default 10)")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after this long (0 = until ctrl-C)")
    ap.add_argument("--e-ramp", type=_parse_ramp, default=None, metavar="A:B:S",
                    help="ramp ethanol from A %% to B %% over S seconds")
    ap.add_argument("--stall", action="store_true",
                    help="freeze the rolling counter (matrix row 4)")
    ap.add_argument("--stop-after", type=float, default=None, metavar="S",
                    help="stop sending after S seconds (matrix row 2)")
    ap.add_argument("--implausible", action="store_true",
                    help="report 101 %% ethanol (matrix row 5)")
    ap.add_argument("--not-ready", action="store_true",
                    help="report status 3 (matrix row 6)")
    ap.add_argument("--fault-after", "--node-fault-after", dest="fault_after",
                    type=float, default=None, metavar="S",
                    help="send nominal frames first and apply the configured "
                         "fault only after S seconds (procedure.md section 5: "
                         "settle, then step)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    node = EthanolNode(e_pct=a.e_pct, temp_c=a.temp, status=a.status, fw=a.fw,
                       can_id=a.id, rate_hz=a.rate, ramp=a.e_ramp,
                       stall=a.stall, stop_after=a.stop_after,
                       implausible=a.implausible, not_ready=a.not_ready,
                       fault_after=a.fault_after)
    link = open_link(parse_bus_spec(a.bus))
    sender = FrameSender(link, node)
    print(f"ethanol node on {link.description}, id {node.can_id:#05x}, "
          f"{node.rate_hz:g} Hz")
    print("  " + node.describe())
    try:
        if a.seconds:
            sender.run(a.seconds)
        else:
            while True:
                if not sender.pump():
                    time.sleep(0.002)
                elif a.verbose and sender.sent % 10 == 1:
                    print("  " + node.describe(sender.elapsed()))
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{sender.sent} frames sent")
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
