#!/usr/bin/env python3
"""TouCAN receive message buffers, as much hardware as the RX path needs.

`can_rx_poll` (0x4379C8) only ever looks at three things in a receive message
buffer: the module's **IFLAG** bit for that buffer, the buffer's **control
word** (CODE/BUSY and the DLC in its low nibble) and the eight **payload**
bytes.  Modelling those three is enough to make the real driver report a
fresh frame, which is what `tests/test_ff_fuel_patch.py` has been doing
inline since brief D1 and what `logging/ecu_sim.py` needs in order to run
`patches/ff_fuel` against frames off a live python-can bus (brief E4, #39).

Addresses, all VERIFIED-STATIC from `re/findings/can.md` section 4:

* `tbl_toucan_bases` holds **CANMCR** = module base + 0x80, so module C's
  CANMCR is 0x707880 and its buffer area starts at 0x707900;
* buffer *b* of a module is at ``canmcr + 0x80 + b * 0x10`` (MPC561RM
  Table 16-10), its payload at +6;
* **IFLAG** is at ``canmcr + 0x24`` and buffer *b* owns bit *b*.

RX slot 15 -- module **C**, buffer **6**, message buffer **0x707960**, driver
shadow 0x803F98/0x803F9C -- is the slot `patches/ff_fuel` claims for the Pico
ethanol frame (id 0x0EC, `patch.json` change at 0x2BD8C).

Usage::

    from emu.toucan import RxMailbox, SLOT15
    mb = RxMailbox(emu, SLOT15)
    mb.arm(b"\\x55\\x3c\\x32\\x01\\x00\\x00\\x01\\x00")   # a frame arrived
    ...                                                   # run the hook
    mb.idle()                                             # nothing new
"""
from __future__ import annotations

from dataclasses import dataclass

#: `tbl_toucan_bases` (can.md section 4): the CANMCR of each module.
CANMCR_A = 0x707080
CANMCR_B = 0x707480
CANMCR_C = 0x707880

IFLAG_OFFSET = 0x24
BUFFER_AREA = 0x80
BUFFER_STRIDE = 0x10
PAYLOAD_OFFSET = 6

#: `can_rx_shadow` (can.md section 4): 22 x 12 bytes `{u32 id; u8 data[8]}`.
RX_SHADOW = 0x803EE4
RX_SHADOW_STRIDE = 12


@dataclass(frozen=True)
class Slot:
    """One entry of the RX slot table of `re/findings/can.md` section 4."""

    index: int
    canmcr: int
    buffer: int

    @property
    def mb(self) -> int:
        return self.canmcr + BUFFER_AREA + self.buffer * BUFFER_STRIDE

    @property
    def payload(self) -> int:
        return self.mb + PAYLOAD_OFFSET

    @property
    def iflag(self) -> int:
        return self.canmcr + IFLAG_OFFSET

    @property
    def iflag_bit(self) -> int:
        return 1 << self.buffer

    @property
    def shadow_id(self) -> int:
        return RX_SHADOW + RX_SHADOW_STRIDE * self.index

    @property
    def shadow_data(self) -> int:
        return self.shadow_id + 4


#: the four spare slots of can.md section 4; 15 is ff_fuel's.
SLOT15 = Slot(15, CANMCR_C, 6)
SLOT16 = Slot(16, CANMCR_C, 7)
SLOT19 = Slot(19, CANMCR_C, 10)
SLOT20 = Slot(20, CANMCR_C, 11)


class RxMailbox:
    """What the TouCAN hardware does to one receive buffer when a frame lands.

    `arm(payload)` is "a matching frame arrived": the IFLAG bit is set and the
    control word carries CODE/BUSY clear with the DLC in its low nibble, which
    is the exact state `can_rx_poll` treats as fresh data.  `idle()` is
    "nothing new since the last poll".
    """

    def __init__(self, emu, slot: Slot = SLOT15):
        self.emu = emu
        self.slot = slot
        self.frames = 0

    def arm(self, payload: bytes, dlc: int | None = None) -> None:
        if len(payload) != 8:
            raise ValueError("a TouCAN payload is 8 bytes")
        s = self.slot
        self.emu.write(s.iflag, s.iflag_bit, 2)
        self.emu.write(s.mb, len(payload) if dlc is None else dlc, 2)
        self.emu.write(s.payload, payload)
        self.frames += 1

    def idle(self) -> None:
        self.emu.write(self.slot.iflag, 0, 2)

    # -- what the driver left behind, for a test or a logger --------------
    def shadow(self) -> tuple[int, bytes]:
        raw = self.emu.read(self.slot.shadow_id, 12)
        return int.from_bytes(raw[0:4], "big"), bytes(raw[4:12])
