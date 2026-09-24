"""ISO 15765-2 framing for the generic-OBD route (0x7DF/0x7E0 -> 0x7E8).

The tester side of what `re/findings/obd.md` section 11 found in the ECU:
requests are **single frames** (PCI `0N`, N <= 7, padded to DLC 8 -- the ECU
drops anything with DLC != 8 and refuses a first frame on 0x7DF), answers
are single frames `0N ...` padded with 0x00, or a first frame `1L LL` followed
by consecutive frames `2n` after the tester sent a flow control `30 BS STmin`
on the physical id 0x7E0.  Nothing here is ECU-specific beyond those facts;
the transport itself is the textbook ISO 15765-2.

Usage::

    from med9kwp.isotp import single_frame, Reassembler
    frame = single_frame(b"\\x01\\x00")            # 02 01 00 00 00 00 00 00
    r = Reassembler()
    r.feed(b"\\x06\\x41\\x00\\xbe\\x1f\\xa8\\x13\\x00")  # -> b"\\x41\\x00\\xbe\\x1f\\xa8\\x13"
"""
from __future__ import annotations

OBD_FUNCTIONAL = 0x7DF
OBD_PHYSICAL = 0x7E0
OBD_RESPONSE = 0x7E8

SF, FF, CF, FC = 0x0, 0x1, 0x2, 0x3


def single_frame(payload: bytes, pad: int = 0x00) -> bytes:
    """PCI `0N` + payload, padded to 8 bytes."""
    if not 1 <= len(payload) <= 7:
        raise ValueError("a single frame carries 1..7 bytes")
    return (bytes([len(payload)]) + bytes(payload)).ljust(8, bytes([pad]))


def flow_control(block_size: int = 0, st_min: int = 0, pad: int = 0x00) -> bytes:
    """FC `30 BS STmin` (continue to send), padded to 8 bytes."""
    return bytes([0x30, block_size & 0xFF, st_min & 0xFF]).ljust(8, bytes([pad]))


class IsoTpError(ValueError):
    pass


class Reassembler:
    """Turn the ECU's frames back into one message.

    `feed(frame)` returns the complete payload once it has one, else None.
    `needs_flow_control` is True right after a first frame.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._buf = bytearray()
        self._want = 0
        self._sn = 0
        self.needs_flow_control = False

    def feed(self, frame: bytes) -> bytes | None:
        if not frame:
            raise IsoTpError("empty frame")
        kind, low = frame[0] >> 4, frame[0] & 0xF
        if kind == SF:
            if not 1 <= low <= 7 or len(frame) < low + 1:
                raise IsoTpError(f"bad single frame {frame.hex()}")
            self.reset()
            return bytes(frame[1:1 + low])
        if kind == FF:
            self.reset()
            self._want = (low << 8) | frame[1]
            if self._want <= 7:
                raise IsoTpError(f"first frame for {self._want} bytes")
            self._buf += frame[2:8]
            self._sn = 1
            self.needs_flow_control = True
            return None
        if kind == CF:
            self.needs_flow_control = False
            if not self._want:
                raise IsoTpError("consecutive frame without a first frame")
            if low != self._sn:
                raise IsoTpError(f"sequence {low}, expected {self._sn}")
            self._sn = (self._sn + 1) & 0xF
            self._buf += frame[1:8]
            if len(self._buf) >= self._want:
                out = bytes(self._buf[:self._want])
                self.reset()
                return out
            return None
        if kind == FC:
            return None                      # the ECU's own flow control
        raise IsoTpError(f"PCI {frame[0]:#04x}")
