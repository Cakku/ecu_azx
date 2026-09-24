#!/usr/bin/env python3
"""The generic-OBD CAN route, 0x7DF/0x7E0 -> 0x7E8, run by the firmware itself.

Brief H3 (issue #48), `re/findings/obd.md` section 11.  A scan tool sends an
ISO 15765-4 request as an ISO 15765-2 single frame on 0x7DF; on this ECU it
lands in TouCAN module C message buffer 15 (mask 0x7C0) and the answer leaves
from module C buffer 13 with id 0x7E8.  This module puts a frame into that
buffer and lets **the firmware's own code** do everything between the two
buffers, under the `emu/` Unicorn harness:

* the TouCAN ISR body 0x404000 (kind-4 range object 0xD8 for 0x7DF, 0xD9 for
  0x7E0), `obd_func_rx_ind` 0x0B5534 and `isotp_rx_indication` 0x1420A0 --
  the ISO 15765-2 parser (PCI byte, DLC, SF/FF/CF/FC);
* the connection layer and `kwp_conn_cyclic` 0x13E650 (10 ms), which opens the
  connection, copies the functional address 0x33 to 0x7F804B, runs
  `kwp_service_h2_walk` (session 6, the OBD support bitmaps) and calls
  `kwp_service_dispatch` 0x13E98C with the real mode-01 handler;
* `isotp_transmit` 0x1429BC and `can_tx_frame` 0x13C4CC, which load MB13.

Its start-up is the firmware's too: init entry **44** (`can_rx_arm_all`
0x12E570, which registers the range objects and the owner map) and init entry
**281** (0x1344C0, the whole diagnostic stack), called once, in that order.

What is NOT the firmware -- the harness, each labelled where it is done:

1. **TouCAN hardware.**  A received frame is written into MB15 (control word
   CODE 0010 / DLC, id word ``id << 5``, the data) with IFLAG bit 15 set, and
   only if RX15MSK would let it through (``id & 0x7C0 == 0x7C0``, `can.md`
   section 6).  A transmit (MB13 CODE 1100) is taken off the buffer, the CODE
   set back to 1000 and IFLAG bit 13 raised, as the controller would after an
   acknowledged frame.  During init entry 44 the freeze handshake is stubbed:
   CANMCR reads return FRZACK = HALT, so the entry's `can_module_start` waits
   do not spin.
2. **The time base.**  The diagnostic module reads TBL with 44 inline `mftb`
   instructions (0x134000-0x144FFF) for its timeouts; they are rewritten, in
   the emulator's copy of the flash only, into loads of the low word of
   `emu/time_base.py`'s virtual time base.  Without it the 5 s connection
   timeout (obd.md 11.3) never expires.
3. **The raster.**  There is no OS: :meth:`ObdCanRoute.run` calls the bodies
   of the two set-A epilogues the stack lives in -- 0x1427BC and 0x13A5B4 every
   2 ms (0x424884, `scheduler.md` 11.5) and 0x1447C0 and 0x13E650 every 10 ms
   (`task_10ms_int_epilogue` 0x4328B4) -- without their OS tails.  Long idle
   stretches are compressed (:meth:`advance_to`): nothing in the stack moves
   between frames except the timeout comparisons, which are differences of the
   time base and so see a jump.

The client side (building a request, reading the answer frames) is
`logging/med9kwp/isotp.py`; `logging/obd_client.py` puts it on a bus.

Usage::

    from emu import Med9Emu
    from emu.time_base import VirtualTimeBase
    from emu.obd_can import ObdCanRoute
    emu = Med9Emu("data/passat_azx_ori.bin", r2="app")
    route = ObdCanRoute(emu, VirtualTimeBase(emu))
    route.request(b"\\x01\\x00")      # -> [(0x7E8, b"\\x06\\x41\\x00...")]

    python3 -m emu.obd_can                 # one 01 00 on the stock image
"""
from __future__ import annotations

import struct

from .time_base import R13_SDA

#: TouCAN module C (can.md section 4): CANMCR and the buffer area
CANMCR_C = 0x707880
IFLAG_C = CANMCR_C + 0x24
MB_AREA = CANMCR_C + 0x80
RX_MB = 15                         # 0x7C0-0x7FF, the range objects 0xD7-0xDA
TX_MB = 13                         # can_cfg_struct[5]: id 0x7E8
RX15MSK_ID = 0x7C0                 # base and mask of module C MB15
CANMCR_ALL = (0x707080, 0x707480, 0x707880)

#: ISO 15765-4 identifiers
OBD_FUNCTIONAL = 0x7DF
OBD_PHYSICAL = 0x7E0
OBD_RESPONSE = 0x7E8

#: the firmware functions this route calls (obd.md section 11)
ISR_BODY = 0x404000                # TouCAN ISR body (r3 = CANMCR, r4 = module)
INIT_CAN = (44, 0x12E570)          # can_rx_arm_all: owner map, range objects
INIT_DIAG = (281, 0x1344C0)        # the diagnostic stack's start-up
RASTER_2MS = (0x1427BC, 0x13A5B4)  # body of the set-A 2 ms epilogue 0x424884
RASTER_10MS = (0x1447C0, 0x13E650) # body of task_10ms_int_epilogue 0x4328B4
H2_WALK = 0x13ECB0                 # kwp_service_h2_walk: once per connection
#: the function range whose inline `mftb` reads are redirected (harness 2)
MFTB_RANGE = (0x134000, 0x145000)

#: RAM the route reports (VERIFIED-STATIC, obd.md 11)
SESSION_CURRENT = 0x803D3E
CONN_ACTIVE = 0x803D70             # 0 = no diagnostic connection
TESTER_TARGET = 0x7F804B           # 0x33 on the functional channel
CHANNEL_TYPE = 0x803D6A            # 4 on the functional channel
ISOTP_STATE = 0x804734             # 7 x 0x1C, [0x803E0C]+0x10

TASK_STACK_TOP = 0x7FF768          # the same task stack logging/ecu_sim uses
TICK_S = 0.002
TICKS_PER_10MS = 5

#: the five mode-01 PID lists (obd.md 3.2): name, ptrs, ids, n, value bytes
R2_APP = 0x5C9FF0
PID_LISTS = (
    ("A2", R2_APP - 0x42CC, R2_APP - 0x427C, 20, 1),
    ("A3", R2_APP - 0x4254, R2_APP - 0x4234, 8, 2),
    ("B2", R2_APP - 0x4224, R2_APP - 0x4218, 3, 1),
    ("B3", R2_APP - 0x4210, R2_APP - 0x41F8, 6, 2),
    ("B5", R2_APP - 0x41EC, R2_APP - 0x41DC, 4, 4),
)


def _mb(n: int) -> int:
    return MB_AREA + n * 0x10


def single_frame(payload: bytes, pad: int = 0x00) -> bytes:
    """An ISO 15765-2 single frame: PCI `0N`, the payload, padding to 8."""
    if not 1 <= len(payload) <= 7:
        raise ValueError("a single frame carries 1..7 bytes")
    return (bytes([len(payload)]) + bytes(payload)).ljust(8, bytes([pad]))


def seed_pid_records(emu, value=None) -> int:
    """MODEL: make every stock mode-01 PID record valid, as the sensor tasks would.

    On the ECU the 41 records of the five lists (obd.md 3.2) are written by
    the tasks that own each value; a cold emulator runs none of them, so every
    `valid` byte is 0 and `01 00` answers an empty bitmap.  This writes, for
    each list entry, a value (``value(pid, width)`` or a fixed pattern) and
    `valid` = 1 -- a stand-in, labelled, so that the bitmap a test compares
    is a realistic one.  The pointers and ids are read out of the image.
    Returns the number of records written.
    """
    n_written = 0
    for _name, ptrs, ids, n, width in PID_LISTS:
        p = struct.unpack(f">{n}I", emu.read(ptrs, 4 * n))
        pid = emu.read(ids, n)
        for k in range(n):
            val = (value(pid[k], width) if value else
                   bytes(((pid[k] * 7 + j * 29 + 3) & 0xFF) for j in range(width)))
            emu.write(p[k], bytes(val) + b"\x01")
            n_written += 1
    return n_written


class ObdCanRoute:
    """Module C MB15 in, MB13 out, the firmware in between (module docstring)."""

    def __init__(self, emu, time_base, *, start: bool = True):
        if time_base is None:
            raise ValueError("the OBD route needs a VirtualTimeBase "
                             "(emu/time_base.py): its timeouts read the time base")
        self.emu = emu
        self.tb = time_base
        #: simulated seconds on the route's own clock
        self.t = 0.0
        self._ticks = 0
        #: every frame the ECU put on the bus: (t, can_id, data)
        self.sent: list[tuple[float, int, bytes]] = []
        #: every frame given to the ECU: (t, can_id, data, accepted_by_mask)
        self.received: list[tuple[float, int, bytes, bool]] = []
        #: h2 walks seen, i.e. diagnostic connections opened by the firmware
        self.connections = 0
        self.errors: list[str] = []
        self.mftb_rewritten = 0
        self._walk_hook = None
        self.init_log: list[tuple[int, int, bool]] = []
        if start:
            self.start()

    # -- set-up ------------------------------------------------------------
    def _call(self, addr: int, *args, max_insns: int = 400_000):
        res = self.emu.call(addr, args=list(args),
                            regs={"r1": TASK_STACK_TOP}, reset=False,
                            max_insns=max_insns)
        if not res.ok and len(self.errors) < 20:
            self.errors.append(f"{addr:#08x}: {res.stop_reason} at {res.pc:#08x}"
                               f" {res.issues[:1]}")
        return res

    def _rewrite_mftb(self) -> int:
        """HARNESS 2: `mftb rD` (TBL) -> `lwz rD, lo(r13)` of the virtual TB."""
        lo_disp = self.tb.cell + 4 - R13_SDA
        start, end = MFTB_RANGE
        uc = self.emu.uc
        blob = bytearray(uc.mem_read(start, end - start))
        n = 0
        for off in range(0, len(blob), 4):
            word = int.from_bytes(blob[off:off + 4], "big")
            if (word & 0xFC1FFFFF) == 0x7C0C42E6:           # mftb rD, TBL
                rd = (word >> 21) & 31
                lwz = 0x80000000 | (rd << 21) | (13 << 16) | (lo_disp & 0xFFFF)
                blob[off:off + 4] = lwz.to_bytes(4, "big")
                n += 1
        if n:
            uc.mem_write(start, bytes(blob))
            drop = getattr(uc, "ctl_remove_cache", None)
            if drop is not None:
                try:
                    drop(start, end)
                except Exception:                            # pragma: no cover
                    pass
        return n

    def _freeze_stub(self, _pc, addr, _size):
        """HARNESS 1: FRZACK follows HALT; NOTRDY and SOFTRST read clear."""
        v = int.from_bytes(bytes(self.emu.uc.mem_read(addr, 2)), "big")
        v = v | 0x0100 if v & 0x1000 else v & ~0x0100
        return v & ~0x0A00

    def start(self) -> None:
        """Run init entries 44 and 281, then two idle 10 ms ticks."""
        if not self.mftb_rewritten:
            self.mftb_rewritten = self._rewrite_mftb()
        for mcr in CANMCR_ALL:
            self.emu.stub_read(mcr, self._freeze_stub)
        try:
            for idx, addr in (INIT_CAN, INIT_DIAG):
                res = self._call(addr, max_insns=20_000_000)
                self.init_log.append((idx, addr, res.ok))
        finally:
            for mcr in CANMCR_ALL:
                self.emu._read_stubs.pop(mcr, None)
        if self._walk_hook is None:
            from unicorn import UC_HOOK_CODE

            def _walk(_uc, _addr, _size, _ud):
                self.connections += 1
            self._walk_hook = self.emu.uc.hook_add(
                UC_HOOK_CODE, _walk, begin=H2_WALK, end=H2_WALK)
        self.tb.advance(self.t)
        self.run(0.02)

    # -- state -------------------------------------------------------------
    @property
    def session(self) -> int:
        return self.emu.read(SESSION_CURRENT, 1)[0]

    @property
    def connection_open(self) -> bool:
        return self.emu.read(CONN_ACTIVE, 4) != b"\x00\x00\x00\x00"

    @property
    def tester_target(self) -> int:
        return self.emu.read(TESTER_TARGET, 1)[0]

    @property
    def channel_type(self) -> int:
        return self.emu.read(CHANNEL_TYPE, 1)[0]

    def isotp_state(self, ch: int) -> bytes:
        return self.emu.read(ISOTP_STATE + ch * 0x1C, 0x1C)

    # -- the bus side --------------------------------------------------------
    def receive(self, can_id: int, data: bytes, dlc: int | None = None) -> bool:
        """HARNESS 1: a frame on the wire.  False if RX15MSK filters it out."""
        data = bytes(data)
        dlc = len(data) if dlc is None else dlc
        accepted = (can_id & RX15MSK_ID) == RX15MSK_ID and not can_id >> 11
        self.received.append((round(self.t, 4), can_id, data, accepted))
        if not accepted:
            return False
        mb = _mb(RX_MB)
        self.emu.write(mb, struct.pack(">H", 0x0020 | (dlc & 0xF)))
        self.emu.write(mb + 2, struct.pack(">H", (can_id & 0x7FF) << 5))
        self.emu.write(mb + 4, b"\x00\x00")
        self.emu.write(mb + 6, data[:8].ljust(8, b"\x00"))
        self.emu.write(IFLAG_C, struct.pack(">H", 1 << RX_MB))
        self._call(ISR_BODY, CANMCR_C, 2)
        return True

    def _service_tx(self) -> None:
        """HARNESS 1: MB13 CODE 1100 -> the frame goes out, IFLAG bit 13."""
        mb = _mb(TX_MB)
        ctrl = struct.unpack(">H", self.emu.read(mb, 2))[0]
        if (ctrl >> 4) & 0xF != 0xC:
            return
        can_id = struct.unpack(">H", self.emu.read(mb + 2, 2))[0] >> 5
        data = bytes(self.emu.read(mb + 6, ctrl & 0xF))
        self.sent.append((round(self.t, 4), can_id, data))
        self.emu.write(mb, struct.pack(">H", (ctrl & 0xFF0F) | 0x0080))
        self.emu.write(IFLAG_C, struct.pack(">H", 1 << TX_MB))
        self._call(ISR_BODY, CANMCR_C, 2)

    # -- the clock -----------------------------------------------------------
    def _tick(self) -> None:
        self.t += TICK_S
        self._ticks += 1
        self.tb.advance(self.t)
        for fn in RASTER_2MS:
            self._call(fn)
        if self._ticks % TICKS_PER_10MS == 0:
            for fn in RASTER_10MS:
                self._call(fn)
        self._service_tx()

    def run(self, seconds: float) -> list[tuple[float, int, bytes]]:
        """HARNESS 3: the 2 ms / 10 ms raster for `seconds`; the frames sent."""
        before = len(self.sent)
        n = max(int(round(seconds / TICK_S)), 0)
        for _ in range(n):
            self._tick()
        return self.sent[before:]

    def advance_to(self, t: float, *, busy_s: float = 0.04) -> list:
        """Bring the route's clock to `t`, compressing an idle stretch.

        The last `busy_s` before `t` are run tick by tick; anything before is
        one jump of the time base (module docstring, harness 3).
        """
        if t <= self.t:
            return []
        gap = t - self.t
        if gap > 2 * busy_s:
            head = self.run(busy_s)
            self.t = t - busy_s
            self.tb.advance(self.t)
            return head + self.run(busy_s)
        return self.run(gap)

    def idle(self, seconds: float) -> list:
        return self.advance_to(self.t + seconds)

    # -- one request ---------------------------------------------------------
    def request(self, payload: bytes, *, can_id: int = OBD_FUNCTIONAL,
                pad: int = 0x00, wait_s: float = 0.1, dlc: int = 8,
                flow_control: bytes = b"\x30\x00\x00") -> list[tuple[int, bytes]]:
        """Send one single-frame request and collect what comes back.

        If the ECU answers with a first frame, a flow control is sent on the
        physical id 0x7E0 -- what a scan tool does -- and the consecutive
        frames are collected.  Returns [(can_id, data)] for the frames the
        ECU sent within `wait_s` simulated seconds.
        """
        frame = single_frame(payload, pad)[:dlc]
        start = len(self.sent)
        self.receive(can_id, frame, dlc)
        fc_sent = False
        waited = 0.0
        while waited < wait_s:
            self.run(0.01)
            waited += 0.01
            new = self.sent[start:]
            if (not fc_sent and new and (new[0][2][:1] or b"\x00")[0] >> 4 == 1):
                self.receive(OBD_PHYSICAL, flow_control.ljust(8, bytes([pad])))
                fc_sent = True
        return [(cid, data) for _t, cid, data in self.sent[start:]]


def main(argv=None) -> int:                                   # pragma: no cover
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "passat_azx_ori.bin"))
    ap.add_argument("request", nargs="*", default=["01", "00"])
    a = ap.parse_args(argv)
    from . import Med9Emu
    from .time_base import VirtualTimeBase
    emu = Med9Emu(a.dump, r2="app")
    route = ObdCanRoute(emu, VirtualTimeBase(emu))
    req = bytes(int(x, 16) for x in a.request)
    for cid, data in route.request(req):
        print(f"{cid:03X}  {data.hex(' ')}")
    print(f"session {route.session}, connections {route.connections}")
    return 0 if not route.errors else (print(*route.errors, sep="\n", file=sys.stderr) or 1)


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
