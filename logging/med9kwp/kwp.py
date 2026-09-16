"""KWP2000 for the MED9.1.1, on top of a TP2.0 channel.

Every service, gate and constant below comes from `re/findings/kwp.md`, which
was produced from this ECU's own code (brief B3, issue #13) and corrected by
brief C3 (section 12).  The parts that matter here:

* a request on the wire is ``SID [data...]``; a positive answer is
  ``SID+0x40 [data...]`` and a negative one ``7F SID NRC``;
* **DDLI (0x2C) + ReadDataByLocalId (0x21)** read arbitrary RAM at rate and
  need only a diagnostic session -- **no SecurityAccess** (kwp.md 4);
* **RequestUpload (0x35)** needs session 0x86, which needs SecurityAccess
  level 2 first, and refuses any range overlapping **0x7F9E3C-0x7FA47F**
  with NRC 0x31 (kwp.md 5);
* **TesterPresent is a bare ``3E``** on this ECU: a sub-function byte is
  answered with NRC 0x12 (kwp.md 12.2 -- this contradicts most KWP2000
  documentation, and it is what the real handler does);
* ``21 <group>`` answers 25 bytes carrying group *G* **and** group *G*+0x7F,
  and only groups 1..127 may be requested (kwp.md 12.3).

Usage::

    kwp = KwpClient(tp)                      # tp is a connected Tp20Client
    kwp.start_session(0x89)
    kwp.define_dynamic_id(0xF0, [(0x7FEE74, 2), (0x7FFB00, 6)])
    while True:
        print(kwp.read_dynamic_id(0xF0).hex())
        kwp.tester_present_if_idle()
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# services and constants (re/findings/kwp.md)
# ---------------------------------------------------------------------------
SID_START_SESSION = 0x10
SID_READ_LOCAL_ID = 0x21
SID_DYNAMIC_DEFINE = 0x2C
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_TRANSFER_EXIT = 0x37
SID_TESTER_PRESENT = 0x3E
SID_SECURITY_ACCESS = 0x27

SESSION_DEFAULT = 0x81
SESSION_83 = 0x83
SESSION_PROGRAMMING = 0x85
SESSION_UPLOAD = 0x86
SESSION_LOGGING = 0x89          #: 0x21/0x2C with no security -- kwp.md 2

DDLI_FIRST = 0xF0
DDLI_LAST = 0xF9
#: max chunks per dynamic id: 20 for 0xF0, 3 for 0xF1..0xF9 (kwp.md 4.1)
DDLI_MAX_ENTRIES = {DDLI_FIRST: 20}
DDLI_DEFAULT_MAX_ENTRIES = 3
DDLI_MODE_DEFINE = 0x03
DDLI_MODE_CLEAR = 0x04

#: TransferData hands back at most this many bytes per block (kwp.md 5.2)
UPLOAD_BLOCK = 0x3E

#: RequestUpload refuses any range overlapping this window with NRC 0x31
PROTECTED_WINDOW = (0x7F9E3C, 0x7FA47F)

#: 0x27 level-2 key constant, flash word at 0x0A331C (kwp.md 3)
SECURITY_L2_ADD = 0x11170
#: 0x27 level-1 Galois LFSR mask and round count (kwp.md 3)
SECURITY_L1_MASK = 0x5FBD5DBD
SECURITY_L1_ROUNDS = 5

NRC_RESPONSE_PENDING = 0x78

NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported / invalid record",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x51: "(VW) upload format error",
    0x53: "(VW) upload out of range",
    0x78: "requestCorrectlyReceived-responsePending",
}


class KwpError(RuntimeError):
    """Protocol error that is not a negative response."""


class NegativeResponse(KwpError):
    """The ECU answered ``7F <sid> <nrc>``."""

    def __init__(self, sid: int, nrc: int, note: str = ""):
        self.sid = sid
        self.nrc = nrc
        name = NRC_NAMES.get(nrc, "unknown")
        super().__init__(
            f"service {sid:#04x} refused with NRC {nrc:#04x} ({name})"
            + (f": {note}" if note else ""))


def key_level1(seed: int) -> int:
    """SecurityAccess level 1 key: 5-round Galois LFSR (kwp.md 3)."""
    x = seed & 0xFFFFFFFF
    for _ in range(SECURITY_L1_ROUNDS):
        if x < 0x80000000:
            x = (x << 1) & 0xFFFFFFFF
        else:
            x = ((x << 1 | 1) ^ SECURITY_L1_MASK) & 0xFFFFFFFF
    return x


def key_level2(seed: int) -> int:
    """SecurityAccess level 2 key: ``seed + 0x11170`` (kwp.md 3)."""
    return (seed + SECURITY_L2_ADD) & 0xFFFFFFFF


def overlaps_protected(addr: int, size: int) -> bool:
    lo, hi = PROTECTED_WINDOW
    return addr <= hi and (addr + size - 1) >= lo


def split_around_protected(addr: int, size: int) -> list[tuple[int, int]]:
    """Cut ``[addr, addr+size)`` into pieces that miss the protected window.

    `kwp_upload_range_check` (0x0A3160) rejects the whole request if any byte
    of it lands in 0x7F9E3C-0x7FA47F, so a caller that wants "all of RAM" has
    to ask twice.  The window itself is never returned.
    """
    lo, hi = PROTECTED_WINDOW
    end = addr + size
    out: list[tuple[int, int]] = []
    if addr < lo:
        out.append((addr, min(end, lo) - addr))
    if end > hi + 1:
        start = max(addr, hi + 1)
        out.append((start, end - start))
    return [(a, n) for a, n in out if n > 0]


@dataclass
class DdliChunk:
    """One ``03 <pos> <size> <a2> <a1> <a0>`` entry of a dynamic id."""

    address: int
    size: int

    def __post_init__(self) -> None:
        if not 1 <= self.size <= 0xFF:
            raise ValueError(f"DDLI chunk size {self.size} out of range 1..255")
        if not 0 <= self.address <= 0xFFFFFF:
            raise ValueError(
                f"DDLI address {self.address:#x} is not a 24-bit address "
                "(kwp.md 4.1: the top byte is forced to 0)")


class KwpClient:
    """KWP2000 request/response over a connected :class:`~med9kwp.Tp20Client`.

    `tester_present_s` is how often :meth:`tester_present_if_idle` sends a
    keep-alive; kwp.md 2.2 recommends ~2 s, comfortably under the ECU's
    default P3.
    """

    def __init__(self, tp, *, timeout: float = 1.0,
                 tester_present_s: float = 2.0,
                 pending_timeout: float = 5.0):
        self.tp = tp
        self.timeout = timeout
        self.tester_present_s = tester_present_s
        self.pending_timeout = pending_timeout
        self.session: int | None = None
        self.security_level = 0
        self._last_activity = time.monotonic()
        self.request_count = 0
        self.rtt_last: float | None = None

    # -- primitives --------------------------------------------------------
    def raw(self, payload: bytes, timeout: float | None = None) -> bytes:
        """One request; returns the whole positive response, SID included."""
        timeout = self.timeout if timeout is None else timeout
        started = time.monotonic()
        answer = self.tp.request(payload, timeout)
        self.request_count += 1
        deadline = started + self.pending_timeout
        while (len(answer) >= 3 and answer[0] == 0x7F
               and answer[2] == NRC_RESPONSE_PENDING):
            if time.monotonic() > deadline:
                raise NegativeResponse(payload[0], NRC_RESPONSE_PENDING,
                                       "the ECU never finished")
            answer = self.tp.recv_message(timeout)
        self.rtt_last = time.monotonic() - started
        self._last_activity = time.monotonic()
        if not answer:
            raise KwpError("empty KWP response")
        if answer[0] == 0x7F:
            if len(answer) < 3:
                raise KwpError(f"malformed negative response {answer.hex()}")
            raise NegativeResponse(answer[1], answer[2])
        if answer[0] != (payload[0] + 0x40) & 0xFF:
            raise KwpError(
                f"answer {answer[0]:#04x} does not match request "
                f"{payload[0]:#04x} (expected {(payload[0] + 0x40) & 0xFF:#04x})")
        return answer

    def request(self, sid: int, data: bytes = b"",
                timeout: float | None = None) -> bytes:
        """One request; returns the response bytes **after** the echoed SID."""
        return self.raw(bytes([sid]) + bytes(data), timeout)[1:]

    # -- session and keep-alive -------------------------------------------
    def start_session(self, sub: int = SESSION_LOGGING) -> None:
        self.request(SID_START_SESSION, bytes([sub]))
        self.session = sub
        if sub != SESSION_UPLOAD:
            # Entering 0x86 resets the security state to 0 (kwp.md 2); every
            # other session change wipes the dynamic ids (kwp.md 4.1).
            self.security_level = 0

    def tester_present(self) -> None:
        """Bare ``3E`` -- a sub-function byte is answered NRC 0x12 (kwp.md 12.2)."""
        self.request(SID_TESTER_PRESENT)

    def tester_present_if_idle(self) -> bool:
        if time.monotonic() - self._last_activity < self.tester_present_s:
            return False
        self.tester_present()
        return True

    # -- security ----------------------------------------------------------
    def request_seed(self, level: int = 2) -> int:
        sub = {1: 0x01, 2: 0x03, 3: 0x05}[level]
        data = self.request(SID_SECURITY_ACCESS, bytes([sub]))
        if len(data) < 5:
            raise KwpError(f"short seed response: {data.hex()}")
        return int.from_bytes(data[1:5], "big")

    def send_key(self, key: int, level: int = 2) -> None:
        sub = {1: 0x02, 2: 0x04, 3: 0x06}[level]
        data = self.request(SID_SECURITY_ACCESS,
                            bytes([sub]) + key.to_bytes(4, "big"))
        if len(data) < 2 or data[1] != 0x34:
            raise KwpError(f"send-key not granted: {data.hex()}")
        self.security_level = level

    def unlock_level2(self) -> int:
        """``27 03`` then ``27 04 <seed+0x11170>``.  Returns the seed used.

        Give **one key attempt per seed**: a wrong key arms a lockout timer and
        the next seed request answers NRC 0x37 (kwp.md 3.2).
        """
        seed = self.request_seed(2)
        self.send_key(key_level2(seed), 2)
        return seed

    # -- dynamic ids (the fast RAM route) ---------------------------------
    @staticmethod
    def max_chunks(lid: int) -> int:
        return DDLI_MAX_ENTRIES.get(lid, DDLI_DEFAULT_MAX_ENTRIES)

    def clear_dynamic_id(self, lid: int = DDLI_FIRST) -> None:
        """``2C <lid> 04``.  Always do this before (re)defining (kwp.md 4.1)."""
        try:
            self.request(SID_DYNAMIC_DEFINE, bytes([lid, DDLI_MODE_CLEAR]))
        except NegativeResponse as exc:
            if exc.nrc != 0x22:      # "nothing defined" is not a problem
                raise

    def define_dynamic_id(self, lid: int, chunks) -> None:
        """``2C <lid> 03 <pos> <size> <a2 a1 a0> ...`` for every chunk.

        `chunks` is a sequence of :class:`DdliChunk` or ``(address, size)``.
        Positions are computed here: the firmware insists the record is
        described contiguously from position 1 and rejects the whole define
        with NRC 0x12 otherwise.
        """
        entries = [c if isinstance(c, DdliChunk) else DdliChunk(*c)
                   for c in chunks]
        if not DDLI_FIRST <= lid <= DDLI_LAST:
            raise ValueError(f"dynamic id {lid:#04x} outside 0xF0..0xF9")
        limit = self.max_chunks(lid)
        if len(entries) > limit:
            raise ValueError(
                f"{len(entries)} chunks for dynamic id {lid:#04x}, but the "
                f"firmware allows {limit} (tbl_ddli_max_entries, kwp.md 4.1)")
        total = sum(e.size for e in entries)
        if total > 0xFF:
            raise ValueError(
                f"the record would be {total} bytes; keep it under 256")
        payload = bytearray([lid])
        pos = 1
        for e in entries:
            payload += bytes([DDLI_MODE_DEFINE, pos, e.size,
                              (e.address >> 16) & 0xFF,
                              (e.address >> 8) & 0xFF, e.address & 0xFF])
            pos += e.size
        self.request(SID_DYNAMIC_DEFINE, bytes(payload))

    def read_dynamic_id(self, lid: int = DDLI_FIRST) -> bytes:
        """``21 <lid>`` -> the concatenated chunks (the echoed lid removed)."""
        data = self.request(SID_READ_LOCAL_ID, bytes([lid]))
        if not data or data[0] != lid:
            raise KwpError(
                f"dynamic read echoed {data[:1].hex()} instead of {lid:#04x}")
        return data[1:]

    # -- measuring blocks --------------------------------------------------
    def read_group(self, group: int) -> tuple[list[tuple[int, int, int]],
                                              list[tuple[int, int, int]]]:
        """``21 <group>`` -> (triples of `group`, triples of `group`+0x7F).

        The ECU answers 25 bytes: the echoed group and **eight**
        ``(formula, A, B)`` triples, the first four for the requested group and
        the next four for group + 0x7F (kwp.md 12.3).  Only groups 1..127 may
        be requested; anything above is NRC 0x31.
        """
        if not 1 <= group <= 0x7F:
            raise ValueError(
                f"group {group} cannot be requested directly; only 1..127 are "
                f"reachable. Group {group} is the tail of the answer to "
                f"group {group - 0x7F}." if group <= 254 else
                f"group {group} does not exist")
        data = self.request(SID_READ_LOCAL_ID, bytes([group]))
        if not data or data[0] != group:
            raise KwpError(f"group read echoed {data[:1].hex()}")
        body = data[1:]
        triples = [(body[i], body[i + 1], body[i + 2])
                   for i in range(0, len(body) - 2, 3)]
        return triples[:4], triples[4:8]

    # -- upload ------------------------------------------------------------
    def request_upload(self, addr: int, size: int) -> int:
        """``35 <addr:3> 00 <size:3>`` -> the block-length indicator (0x3F)."""
        if overlaps_protected(addr, size):
            lo, hi = PROTECTED_WINDOW
            raise ValueError(
                f"[{addr:#08x}, {addr + size:#08x}) overlaps the protected "
                f"window {lo:#08x}-{hi:#08x}; the ECU would answer NRC 0x31. "
                "Use split_around_protected().")
        payload = bytes([(addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
                         0x00,
                         (size >> 16) & 0xFF, (size >> 8) & 0xFF, size & 0xFF])
        data = self.request(SID_REQUEST_UPLOAD, payload)
        return data[0] if data else 0

    def transfer_data(self) -> bytes:
        """One ``36`` -> up to 62 bytes.  Raises NegativeResponse(0x12) at the end."""
        return self.request(SID_TRANSFER_DATA)

    def transfer_exit(self) -> None:
        self.request(SID_TRANSFER_EXIT)

    def upload(self, addr: int, size: int, progress=None) -> bytes:
        """The whole 0x35 / 0x36... / 0x37 sequence for one range.

        Needs session 0x86, which needs SecurityAccess level 2 first
        (kwp.md 2.1/8 recipe B).  The range must not touch the protected
        window -- :func:`split_around_protected` does that for you.
        """
        self.request_upload(addr, size)
        out = bytearray()
        while len(out) < size:
            try:
                block = self.transfer_data()
            except NegativeResponse as exc:
                if exc.nrc == 0x12:          # the ECU says the range is done
                    break
                raise
            if not block:
                break
            out += block
            if progress is not None:
                progress(len(out), size)
        self.transfer_exit()
        if len(out) != size:
            raise KwpError(
                f"upload of {size} bytes from {addr:#08x} returned {len(out)}")
        return bytes(out)
