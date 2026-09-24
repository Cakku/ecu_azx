#!/usr/bin/env python3
"""A minimal ISO 15765-4 (generic OBD over CAN) tester: 0x7DF in, 0x7E8 out.

What a generic scan tool does on this ECU, per `re/findings/obd.md` section 11:
one single frame on the functional id **0x7DF** (DLC 8, PCI `0N`), the answer
on **0x7E8**, a flow control on the physical id 0x7E0 if the answer is a first
frame.  It is the client half of `logging/ecu_sim.py --obd-can` and of the
bench check "read `01 52` with a scan tool" (docs/08); on the car it needs
nothing but a CAN adapter on the OBD connector's CAN pair (500 kbit/s).

Silence is an answer: on the functional channel the ECU does not answer an
unsupported PID, a service outside session 6 (`3E`, `10`, `21` ...) or a
negative response with NRC 0x10-0x12 (obd.md 11.2), and it never answers
the physical id 0x7E0.  The client reports that as ``(no answer)``.

Usage::

    python3 logging/obd_client.py --sim 01 00            # in-process simulator
    python3 logging/obd_client.py --sim --sim-patch patches/ff_fuel 01 40
    python3 logging/obd_client.py --bus slcan:/dev/tty.usbmodem1411 01 52
    python3 logging/obd_client.py --sim --pids           # 01 00 / 20 / 40 decoded

`--sim` starts `EcuSimulator(obd_can=True)` on a private python-can virtual
bus in this process, exactly as `med9log.py --sim` does for TP2.0.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp import open_link, parse_bus_spec  # noqa: E402
from med9kwp.isotp import (OBD_FUNCTIONAL, OBD_PHYSICAL, OBD_RESPONSE,  # noqa: E402
                           IsoTpError, Reassembler, flow_control, single_frame)


class ObdClient:
    """One request, one answer (or None for the ECU's silence)."""

    def __init__(self, link, *, tx_id: int = OBD_FUNCTIONAL,
                 rx_id: int = OBD_RESPONSE, fc_id: int = OBD_PHYSICAL,
                 timeout: float = 1.0, pad: int = 0x00):
        self.link = link
        self.tx_id, self.rx_id, self.fc_id = tx_id, rx_id, fc_id
        self.timeout = timeout
        self.pad = pad
        #: (direction, can_id, data) of every frame, for a transcript
        self.frames: list[tuple[str, int, bytes]] = []

    def query(self, payload: bytes) -> bytes | None:
        frame = single_frame(payload, self.pad)
        self.link.send(self.tx_id, frame)
        self.frames.append(("tx", self.tx_id, frame))
        rs = Reassembler()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            got = self.link.recv(max(deadline - time.monotonic(), 0.0))
            if got is None:
                break
            can_id, data = got
            if can_id != self.rx_id:
                continue
            self.frames.append(("rx", can_id, data))
            try:
                msg = rs.feed(data)
            except IsoTpError:
                continue
            if rs.needs_flow_control:
                fc = flow_control(pad=self.pad)
                self.link.send(self.fc_id, fc)
                self.frames.append(("tx", self.fc_id, fc))
            if msg is not None:
                return msg
        return None


def decode_bitmap(base: int, body: bytes) -> list[int]:
    """`41 PP b0 b1 b2 b3` -> the supported PIDs it lists."""
    bits = int.from_bytes(body[:4], "big")
    return [base + 1 + i for i in range(32) if bits & (0x80000000 >> i)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("request", nargs="*", help="hex bytes, e.g. 01 00")
    ap.add_argument("--bus", default=None, help="interface:channel")
    ap.add_argument("--sim", action="store_true",
                    help="run logging/ecu_sim.py --obd-can in-process")
    ap.add_argument("--sim-patch", default=None, metavar="DIR",
                    help="with --sim: apply this patch and run its hooks")
    ap.add_argument("--physical", action="store_true",
                    help="send on 0x7E0 instead of 0x7DF")
    ap.add_argument("--pids", action="store_true",
                    help="ask 01 00, 01 20, 01 40 and list the supported PIDs")
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if not a.request and not a.pids:
        ap.error("give a request (e.g. 01 00) or --pids")

    sim = ctx = None
    if a.sim:
        from ecu_sim import EcuSimulator
        channel = f"obd_{os.getpid()}"
        sim = EcuSimulator.on_virtual_bus(channel, obd_can=True,
                                          patch_dir=a.sim_patch)
        ctx = sim.background()
        ctx.__enter__()
        link = open_link(parse_bus_spec(f"virtual:{channel}"))
    else:
        link = open_link(parse_bus_spec(a.bus))
    client = ObdClient(link, tx_id=OBD_PHYSICAL if a.physical else OBD_FUNCTIONAL,
                       timeout=a.timeout)
    rc = 0
    try:
        requests = ([bytes([0x01, p]) for p in (0x00, 0x20, 0x40)] if a.pids
                    else [bytes(int(x, 16) for x in a.request)])
        for req in requests:
            answer = client.query(req)
            text = answer.hex(" ") if answer is not None else "(no answer)"
            print(f"{req.hex(' '):<10} -> {text}")
            if a.pids and answer is not None and answer[:2] == bytes([0x41, req[1]]):
                print("            supported:",
                      " ".join(f"{p:02X}" for p in decode_bitmap(req[1], answer[2:])))
            if answer is None and not a.pids:
                rc = 2
        if a.verbose:
            for d, cid, data in client.frames:
                print(f"  {d} {cid:03X} {data.hex(' ')}")
    finally:
        link.close()
        if ctx is not None:
            ctx.__exit__(None, None, None)
            sim.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
