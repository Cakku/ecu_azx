"""VW Transport Protocol 2.0 over CAN -- client and server.

TP2.0 is the transport VAG puts under KWP2000 on the powertrain CAN.  It is
**not** described anywhere in this repository's firmware findings (the ECU's
TP2.0 layer was never disassembled -- `re/findings/kwp.md` section 9 says so
explicitly), so the protocol below is a clean re-implementation from public
descriptions; the exact sources are listed in `logging/README.md` section 5 and
in `NOTICE.md`.  No third-party code is copied.

Model
-----
A *channel* is opened by the tester with a broadcast on **0x200** naming the
module's logical address (**0x01** = engine).  The module answers on
``0x200 + address`` with the two CAN ids the channel will use.  After that the
two sides exchange timing parameters and then send *messages*: a message is a
big-endian 16-bit length followed by its bytes, cut into 7-byte CAN frames
whose first byte is ``op << 4 | seq``.

============ ===================================================
op           meaning
============ ===================================================
0x0          more frames follow, **ACK me**
0x1          last frame of the message, **ACK me**
0x2          more frames follow, do not ACK
0x3          last frame of the message, do not ACK
0x9          ACK, not ready for the next message
0xB          ACK, ready
0xA0 / 0xA1  parameter request / response (6 bytes)
0xA3         channel test (keep-alive)
0xA4         break -- discard everything since the last ACK
0xA8         disconnect
============ ===================================================

`seq` is a 4-bit counter, one per direction, incremented for every data frame.
An ACK carries **the sequence number of the frame it acknowledges plus one**,
i.e. the next one the sender should use.

Only the first frame of a message carries the 2-byte length, so it holds 5
payload bytes and every later frame holds 7.

Nothing here uses threads or `asyncio`: every wait is a poll against a
deadline, so `Tp20Client` can be driven from a plain loop and `Tp20Server`
from the simulator's.

Usage::

    from med9kwp import open_link, parse_bus_spec, Tp20Client
    link = open_link(parse_bus_spec("slcan:/dev/tty.usbmodem1411"))
    tp = Tp20Client(link, dest=0x01)
    tp.connect()
    print(tp.request(b"\\x3e").hex())      # bare TesterPresent -> 7e
    tp.disconnect()
"""
from __future__ import annotations

import time
from dataclasses import dataclass

CHANNEL_SETUP_ID = 0x200
ENGINE_ADDRESS = 0x01

# channel-setup opcodes
SETUP_REQUEST = 0xC0
SETUP_POSITIVE = 0xD0
SETUP_NEGATIVE = (0xD6, 0xD7, 0xD8)

# channel-parameter opcodes (whole first byte)
OP_PARAM_REQUEST = 0xA0
OP_PARAM_RESPONSE = 0xA1
OP_CHANNEL_TEST = 0xA3
OP_BREAK = 0xA4
OP_DISCONNECT = 0xA8

# data opcodes (high nibble)
OP_MORE_ACK = 0x0
OP_LAST_ACK = 0x1
OP_MORE_NOACK = 0x2
OP_LAST_NOACK = 0x3
OP_ACK_BUSY = 0x9
OP_ACK_READY = 0xB

#: the four TP2.0 timing units, in seconds (0.1 ms, 1 ms, 10 ms, 100 ms)
TIMING_UNITS = (0.0001, 0.001, 0.01, 0.1)


class Tp20Error(RuntimeError):
    """Channel setup failed, or the peer broke the protocol."""


class Tp20Timeout(Tp20Error):
    """The peer did not answer in time."""


# ---------------------------------------------------------------------------
# timing bytes
# ---------------------------------------------------------------------------
def decode_timing(byte: int) -> float:
    """Seconds encoded by a T1/T3 byte: bits 7-6 pick the unit, 5-0 the count."""
    return (byte & 0x3F) * TIMING_UNITS[(byte >> 6) & 0x03]


def encode_timing(seconds: float) -> int:
    """Smallest-unit encoding of `seconds`, saturating at 63 x 100 ms."""
    for idx, unit in enumerate(TIMING_UNITS):
        count = round(seconds / unit)
        if count <= 0x3F:
            return (idx << 6) | max(count, 1)
    return (3 << 6) | 0x3F


@dataclass
class Tp20Params:
    """The five negotiated channel parameters.

    Defaults are the values VAG testers are normally seen to offer: 15 frames
    per block, 100 ms to wait for an ACK, 5 ms between consecutive frames.
    """

    block_size: int = 0x0F
    t1: int = 0x8A                   # 10 x 10 ms = 100 ms
    t2: int = 0xFF                   # unused, always 0xFF
    t3: int = 0x32                   # 50 x 0.1 ms = 5 ms
    t4: int = 0xFF                   # unused, always 0xFF

    @property
    def ack_timeout(self) -> float:
        return decode_timing(self.t1)

    @property
    def frame_gap(self) -> float:
        return decode_timing(self.t3)

    def to_bytes(self, opcode: int) -> bytes:
        return bytes([opcode, self.block_size, self.t1, self.t2, self.t3, self.t4])

    @classmethod
    def from_bytes(cls, data: bytes) -> "Tp20Params":
        if len(data) < 6:
            raise Tp20Error(f"short parameter message: {data.hex()}")
        return cls(block_size=data[1], t1=data[2], t2=data[3],
                   t3=data[4], t4=data[5])

    def describe(self) -> str:
        return (f"BS={self.block_size} T1={self.ack_timeout * 1000:.1f} ms "
                f"T3={self.frame_gap * 1000:.1f} ms")


def encode_channel_id(can_id: int | None) -> tuple[int, int]:
    """(low, high) bytes of a channel-setup id field; `None` = 'you choose'."""
    if can_id is None:
        return 0x00, 0x10                      # validity nibble 1 = invalid
    return can_id & 0xFF, (can_id >> 8) & 0x0F


def decode_channel_id(low: int, high: int) -> int | None:
    if high & 0xF0:
        return None
    return ((high & 0x0F) << 8) | low


# ---------------------------------------------------------------------------
# the segmentation state machine, shared by both ends
# ---------------------------------------------------------------------------
class _Channel:
    """One open TP2.0 channel: send/receive whole messages over `link`.

    `tx_id` is the CAN id this end transmits on, `rx_id` the one it listens to.
    """

    def __init__(self, link, tx_id: int, rx_id: int, params: Tp20Params,
                 *, name: str = "tp20", delay_s: float = 0.0):
        self.link = link
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.params = params
        self.name = name
        self.delay_s = delay_s
        self.tx_seq = 0
        self.rx_seq: int | None = None
        self.ack_seq: int | None = None
        self._rx_buf = bytearray()
        self._rx_want = 0
        self.inbox: list[bytes] = []
        self.peer_params: Tp20Params | None = None
        self.disconnected = False
        #: number of outgoing ACKs to silently drop (robustness testing)
        self.drop_acks = 0
        self.last_tx = 0.0
        self.last_rx = 0.0
        self.trace: list[str] | None = None

    # -- low level ---------------------------------------------------------
    def _emit(self, data: bytes) -> None:
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.trace is not None:
            self.trace.append(f"{self.name} tx {self.tx_id:03X} {data.hex()}")
        self.link.send(self.tx_id, data)
        self.last_tx = time.monotonic()

    def send_control(self, opcode: int) -> None:
        self._emit(bytes([opcode]))

    def send_params(self, opcode: int, params: Tp20Params | None = None) -> None:
        self._emit((params or self.params).to_bytes(opcode))

    # -- receiving ---------------------------------------------------------
    def _ack(self, seq: int, ready: bool = True) -> None:
        if self.drop_acks > 0:
            self.drop_acks -= 1
            if self.trace is not None:
                self.trace.append(f"{self.name} DROPPED ack seq={seq}")
            return
        op = OP_ACK_READY if ready else OP_ACK_BUSY
        self._emit(bytes([(op << 4) | (seq + 1) & 0x0F]))

    def handle_frame(self, data: bytes) -> str:
        """Feed one received frame in. Returns a short tag for the caller.

        Tags: ``"data"`` (a message landed in `inbox`), ``"ack"``,
        ``"param_req"``, ``"param_resp"``, ``"test"``, ``"break"``,
        ``"disconnect"``, ``"partial"``, ``"ignored"``.
        """
        if not data:
            return "ignored"
        self.last_rx = time.monotonic()
        if self.trace is not None:
            self.trace.append(f"{self.name} rx {self.rx_id:03X} {data.hex()}")
        head = data[0]
        if head == OP_PARAM_REQUEST:
            self.peer_params = Tp20Params.from_bytes(data)
            return "param_req"
        if head == OP_PARAM_RESPONSE:
            self.peer_params = Tp20Params.from_bytes(data)
            return "param_resp"
        if head == OP_CHANNEL_TEST:
            return "test"
        if head == OP_BREAK:
            self._rx_buf.clear()
            self._rx_want = 0
            self.rx_seq = None
            return "break"
        if head == OP_DISCONNECT:
            self.disconnected = True
            return "disconnect"

        op, seq = head >> 4, head & 0x0F
        if op in (OP_ACK_READY, OP_ACK_BUSY):
            self.ack_seq = seq
            return "ack"
        if op > OP_LAST_NOACK:
            return "ignored"

        # A retransmission after a lost ACK repeats the previous sequence
        # number: re-acknowledge it but do not append the payload twice.
        if self.rx_seq is not None and seq == self.rx_seq:
            if op in (OP_MORE_ACK, OP_LAST_ACK):
                self._ack(seq)
            return "partial"
        expected = 0 if self.rx_seq is None else (self.rx_seq + 1) & 0x0F
        if seq != expected:
            raise Tp20Error(
                f"{self.name}: sequence gap, expected {expected:#x} got {seq:#x}")
        self.rx_seq = seq

        body = data[1:]
        if not self._rx_want and not self._rx_buf:
            if len(body) < 2:
                raise Tp20Error(f"{self.name}: first frame without a length")
            self._rx_want = (body[0] << 8) | body[1]
            body = body[2:]
        self._rx_buf += body

        if op in (OP_MORE_ACK, OP_LAST_ACK):
            self._ack(seq)
        if op in (OP_LAST_ACK, OP_LAST_NOACK):
            msg = bytes(self._rx_buf[:self._rx_want])
            if len(msg) != self._rx_want:
                raise Tp20Error(
                    f"{self.name}: message declared {self._rx_want} bytes, "
                    f"got {len(msg)}")
            self._rx_buf.clear()
            self._rx_want = 0
            self.inbox.append(msg)
            return "data"
        return "partial"

    # -- sending -----------------------------------------------------------
    def frames_for(self, payload: bytes) -> list[bytes]:
        """Split `payload` into frames, without touching the sequence counter."""
        if not payload:
            raise ValueError("TP2.0 cannot carry an empty message")
        if len(payload) > 0xFFFF:
            raise ValueError("TP2.0 message longer than 0xFFFF bytes")
        body = bytes([len(payload) >> 8, len(payload) & 0xFF]) + payload
        return [body[i:i + 7] for i in range(0, len(body), 7)]

    # -- pumping -----------------------------------------------------------
    def pump_once(self, timeout: float) -> str | None:
        """Read at most one frame for this channel. `None` means 'timed out'.

        A channel test or a parameter request from the peer is answered here,
        because either may arrive at any moment, including in the middle of a
        transfer.
        """
        frame = self.link.recv(timeout)
        if frame is None:
            return None
        can_id, data = frame
        if can_id != self.rx_id:
            return "ignored"
        tag = self.handle_frame(data)
        if tag in ("test", "param_req"):
            self.send_params(OP_PARAM_RESPONSE)
        return tag

    def _await_ack(self, seq: int, frame: bytes, deadline: float,
                   retries: int) -> None:
        want = (seq + 1) & 0x0F
        attempts = retries + 1
        while True:
            self.ack_seq = None
            t1 = self.params.ack_timeout or 0.1
            stop = min(time.monotonic() + t1, deadline)
            while time.monotonic() < stop:
                tag = self.pump_once(max(stop - time.monotonic(), 0.0))
                if tag == "ack" and self.ack_seq == want:
                    return
            attempts -= 1
            if attempts <= 0 or time.monotonic() >= deadline:
                raise Tp20Timeout(
                    f"{self.name}: no ACK {want:#x} after {retries + 1} tries")
            self._emit(frame)                              # retransmit

    def send_message(self, payload: bytes, *, deadline: float,
                     retries: int = 3) -> None:
        frames = self.frames_for(payload)
        block = max(self.params.block_size, 1)
        since_ack = 0
        for index, body in enumerate(frames):
            last = index == len(frames) - 1
            since_ack += 1
            want_ack = last or since_ack >= block
            if last:
                op = OP_LAST_ACK if want_ack else OP_LAST_NOACK
            else:
                op = OP_MORE_ACK if want_ack else OP_MORE_NOACK
            seq = self.tx_seq
            frame = bytes([(op << 4) | seq]) + body
            self._emit(frame)
            self.tx_seq = (seq + 1) & 0x0F
            if want_ack:
                self._await_ack(seq, frame, deadline, retries)
                since_ack = 0
            elif self.params.frame_gap:
                time.sleep(self.params.frame_gap)


# ---------------------------------------------------------------------------
# tester side
# ---------------------------------------------------------------------------
class Tp20Client:
    """The tester end of a TP2.0 channel.

    `rx_id` is the CAN id we ask the module to transmit on; the id we must
    transmit on is whatever the module answers with (0x740 for a VAG engine
    ECU, but never assume it -- read it out of the response).
    """

    def __init__(self, link, *, dest: int = ENGINE_ADDRESS, rx_id: int = 0x300,
                 app_type: int = 0x01, setup_id: int = CHANNEL_SETUP_ID,
                 params: Tp20Params | None = None, timeout: float = 1.0,
                 retries: int = 3, keep_alive_s: float = 1.0,
                 trace: list[str] | None = None):
        self.link = link
        self.dest = dest
        self.wanted_rx_id = rx_id
        self.app_type = app_type
        self.setup_id = setup_id
        self.params = params or Tp20Params()
        self.timeout = timeout
        self.retries = retries
        self.keep_alive_s = keep_alive_s
        self.trace = trace
        self.channel: _Channel | None = None
        self.setup_rtt: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def connect(self, timeout: float | None = None) -> None:
        timeout = self.timeout if timeout is None else timeout
        reply_id = self.setup_id + self.dest
        self.link.set_accept({reply_id})
        self.link.drain()
        rx_lo, rx_hi = encode_channel_id(self.wanted_rx_id)
        tx_lo, tx_hi = encode_channel_id(None)     # "you pick my transmit id"
        request = bytes([self.dest, SETUP_REQUEST, tx_lo, tx_hi,
                         rx_lo, rx_hi, self.app_type])
        started = time.monotonic()
        if self.trace is not None:
            self.trace.append(f"client tx {self.setup_id:03X} {request.hex()}")
        self.link.send(self.setup_id, request)
        deadline = started + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Tp20Timeout(
                    f"no channel-setup answer from logical address "
                    f"{self.dest:#04x} on {reply_id:#05x} within {timeout:.1f} s")
            frame = self.link.recv(left)
            if frame is None:
                continue
            _, data = frame
            if len(data) < 7 or data[1] == SETUP_REQUEST:
                continue
            if self.trace is not None:
                self.trace.append(f"client rx {reply_id:03X} {data.hex()}")
            if data[1] in SETUP_NEGATIVE:
                raise Tp20Error(
                    f"channel setup refused, opcode {data[1]:#04x} "
                    "(module busy, or no free channel)")
            if data[1] != SETUP_POSITIVE:
                continue
            rx_id = decode_channel_id(data[2], data[3])
            tx_id = decode_channel_id(data[4], data[5])
            if rx_id is None or tx_id is None:
                raise Tp20Error(f"module returned an invalid id: {data.hex()}")
            self.setup_rtt = time.monotonic() - started
            break

        self.link.set_accept({rx_id})
        self.channel = _Channel(self.link, tx_id, rx_id, self.params,
                                name="client")
        self.channel.trace = self.trace
        self.exchange_parameters(timeout)

    def exchange_parameters(self, timeout: float | None = None) -> Tp20Params:
        ch = self._channel()
        timeout = self.timeout if timeout is None else timeout
        ch.send_params(OP_PARAM_REQUEST)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tag = ch.pump_once(max(deadline - time.monotonic(), 0.0))
            if tag == "param_resp":
                assert ch.peer_params is not None
                # Honour the module's block size and inter-frame gap; keep our
                # own ACK timeout, which only bounds how long *we* wait.
                ch.params = Tp20Params(
                    block_size=ch.peer_params.block_size,
                    t1=self.params.t1, t2=0xFF,
                    t3=ch.peer_params.t3, t4=0xFF)
                return ch.params
        raise Tp20Timeout("no parameter response (0xA1) from the module")

    def disconnect(self) -> None:
        if self.channel is not None and not self.channel.disconnected:
            try:
                self.channel.send_control(OP_DISCONNECT)
            except Exception:                              # pragma: no cover
                pass
            self.channel.disconnected = True

    def close(self) -> None:
        self.disconnect()
        self.link.close()

    def __enter__(self) -> "Tp20Client":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- messages ----------------------------------------------------------
    def _channel(self) -> _Channel:
        if self.channel is None:
            raise Tp20Error("channel not open -- call connect() first")
        return self.channel

    def send_message(self, payload: bytes, timeout: float | None = None) -> None:
        ch = self._channel()
        timeout = self.timeout if timeout is None else timeout
        ch.send_message(payload, deadline=time.monotonic() + timeout,
                        retries=self.retries)

    def recv_message(self, timeout: float | None = None) -> bytes:
        ch = self._channel()
        timeout = self.timeout if timeout is None else timeout
        if ch.inbox:
            return ch.inbox.pop(0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ch.pump_once(max(deadline - time.monotonic(), 0.0))
            if ch.inbox:
                return ch.inbox.pop(0)
            if ch.disconnected:
                raise Tp20Error("the module closed the channel (0xA8)")
        raise Tp20Timeout(f"no answer within {timeout:.1f} s")

    def request(self, payload: bytes, timeout: float | None = None) -> bytes:
        self.send_message(payload, timeout)
        return self.recv_message(timeout)

    def keep_alive(self, timeout: float | None = None) -> bool:
        """Send a channel test (0xA3). True if the module answered."""
        ch = self._channel()
        timeout = self.timeout if timeout is None else timeout
        ch.send_control(OP_CHANNEL_TEST)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tag = ch.pump_once(max(deadline - time.monotonic(), 0.0))
            if tag in ("param_resp", "param_req", "test"):
                return True
        return False

    def idle_seconds(self) -> float:
        ch = self._channel()
        return time.monotonic() - max(ch.last_tx, ch.last_rx)


# ---------------------------------------------------------------------------
# module side
# ---------------------------------------------------------------------------
class Tp20Server:
    """The module end: answers channel setup, then hands whole messages up.

    `poll()` returns a complete request payload or `None`; the caller answers
    with `send_message()`.  One channel at a time, which is what the real ECU
    offers a single tester.
    """

    def __init__(self, link, *, address: int = ENGINE_ADDRESS,
                 setup_id: int = CHANNEL_SETUP_ID, tester_tx_id: int = 0x740,
                 params: Tp20Params | None = None, delay_s: float = 0.0,
                 trace: list[str] | None = None):
        self.link = link
        self.address = address
        self.setup_id = setup_id
        self.tester_tx_id = tester_tx_id
        self.params = params or Tp20Params()
        self.delay_s = delay_s
        self.trace = trace
        self.channel: _Channel | None = None
        self.channels_opened = 0
        self._pending_drop = 0

    @property
    def connected(self) -> bool:
        return self.channel is not None and not self.channel.disconnected

    def drop_next_acks(self, count: int) -> None:
        """Silently swallow the next `count` ACKs this end would send."""
        self._pending_drop = count
        if self.channel is not None:
            self.channel.drop_acks = count

    def _handle_setup(self, data: bytes) -> None:
        if len(data) < 7 or data[0] != self.address or data[1] != SETUP_REQUEST:
            return
        # bytes 2-3: the id WE should listen on; 4-5: the id WE transmit on.
        our_rx = decode_channel_id(data[2], data[3])
        if our_rx is None:
            our_rx = self.tester_tx_id
        our_tx = decode_channel_id(data[4], data[5])
        if our_tx is None:
            raise Tp20Error("tester did not name an id for us to transmit on")
        lo_t, hi_t = encode_channel_id(our_tx)     # what the tester listens to
        lo_r, hi_r = encode_channel_id(our_rx)     # what the tester transmits on
        reply = bytes([0x00, SETUP_POSITIVE, lo_t, hi_t, lo_r, hi_r, data[6]])
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.trace is not None:
            self.trace.append(
                f"server tx {self.setup_id + self.address:03X} {reply.hex()}")
        self.link.send(self.setup_id + self.address, reply)
        self.channel = _Channel(self.link, our_tx, our_rx, self.params,
                                name="server", delay_s=self.delay_s)
        self.channel.trace = self.trace
        self.channel.drop_acks = self._pending_drop
        self.channels_opened += 1

    def poll(self, timeout: float = 0.05) -> bytes | None:
        """Service the bus for up to `timeout` s; return one request or None."""
        frame = self.link.recv(timeout)
        if frame is None:
            return None
        can_id, data = frame
        if can_id == self.setup_id:
            self._handle_setup(data)
            return None
        ch = self.channel
        if ch is None or can_id != ch.rx_id:
            return None
        tag = ch.handle_frame(data)
        if tag in ("test", "param_req"):
            ch.send_params(OP_PARAM_RESPONSE)
        elif tag == "disconnect":
            self.channel = None
            return None
        if ch.inbox:
            return ch.inbox.pop(0)
        return None

    def send_message(self, payload: bytes, timeout: float = 1.0,
                     retries: int = 3) -> None:
        if self.channel is None:
            raise Tp20Error("no open channel")
        self.channel.send_message(payload,
                                  deadline=time.monotonic() + timeout,
                                  retries=retries)
