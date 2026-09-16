#!/usr/bin/env python3
"""An emulated MED9.1.1 that speaks TP2.0/KWP2000 on a python-can bus.

Not a mock.  Every KWP service below is answered by the **real handler out of
`data/passat_azx_ori.bin`**, executed by the `emu/` Unicorn harness on one
`Med9Emu` whose RAM survives between requests -- so the simulated ECU has a
memory, the DDLI definitions really live in `ddli_def_table` (0x804038), and
the protected-window refusal comes from `kwp_upload_range_check` (0x0A3160),
not from any check written here.  That is what makes it worth testing the
logger against: if `logging/med9log.py` can drive this, the only thing left
between it and the car is the CAN adapter.

What is **not** real, and why (details in `re/findings/kwp.md` section 12):

* **the dispatcher.**  `kwp_service_dispatch` (0x13E98C) is reached through a
  function pointer and expects the OS around it.  Instead the 28-entry
  dispatch table is read out of the image (0x2B820, count from the config
  struct at 0x2BA50+0x1C) and its **own session bitmask** decides what is
  allowed -- the data is the firmware's, the loop is four lines of Python.
* **StartDiagnosticSession.**  `kwp_sid_10_h1` (0x3716C) is the K-line path:
  with request length 1 it accepts only 0x81/0x89 and otherwise walks into a
  baud-rate table and reprograms the serial hardware, which under Unicorn
  ends in the OS halt spin at 0x110F0.  The simulator applies the session map
  of kwp.md section 2 and calls the real one-line setter `kwp_session_set`
  (0x13CEE4), plus the real `kwp_sid_2C_h2` (0x35034) that wipes the dynamic
  ids on every session change.
* **the security seed.**  It comes from the PowerPC time base, which the
  Unicorn 603e never advances, so the real handler always stores 0.
  `--seed` writes a value into `kwp_sec_seed` (0x7FB774) *after* the real
  seed handler ran; the key is then checked by the real `kwp_sid_27_h1`
  against that value, exactly as `tools/kwp_seckey_verify.py` does.

A few RAM cells are animated so a log shows movement: an rpm ramp, coolant
warm-up, the Flash-1 counter of `patches/ff_counter/` and the five raster
activation counters of the two OS task sets.  `--task-set A` makes the *other*
set live, which freezes the set-B counters **and** the Flash-1 block -- the
case `logging/sessions/flash1_counter.json` check 1 has to tell apart from a
failed flash.  See :class:`AnimatedRam`.

Usage::

    python3 logging/ecu_sim.py --self-test          # handlers only, no bus
    python3 logging/ecu_sim.py --bus slcan:/dev/tty.usbmodem1411 -v
    python3 logging/ecu_sim.py --bus virtual:med9 --drop-ack 1 --delay-ms 5

**python-can's `virtual` bus does not cross process boundaries**, so a
simulator started from a second terminal on `virtual:` is invisible to the
logger.  Either put it on a real adapter (`slcan:`/`gs_usb:`, which is how you
would test a third-party tool against it) or, normally, let the logger start
it in-process::

    python3 logging/med9log.py probe --sim

which is this, from Python -- also what `tests/test_med9kwp.py` does::

    sim = EcuSimulator.on_virtual_bus("med9")
    with sim.background():
        ...                      # drive the logger against it
"""
from __future__ import annotations

import argparse
import contextlib
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "logging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp import CanLink, Tp20Params, Tp20Server, parse_bus_spec  # noqa: E402
from med9kwp.can_transport import open_link  # noqa: E402

DUMP = os.path.join(REPO, "data", "passat_azx_ori.bin")

# --- addresses (re/findings/kwp.md) ----------------------------------------
KWP_CONFIG_STRUCT = 0x2BA50          # config[0] = table base, config[0x1C] = count
KWP_TABLE_ENTRY = 20
SESSION_CURRENT = 0x803D3E           # kwp_session_current
SECURITY_STATE = 0x803D3C            # kwp_security_state
SEC_SEED = 0x7FB774                  # kwp_sec_seed
SEC_LEVEL_FLAGS = 0x7FB781
SEC_LFSR_ROUNDS = 0x7FB770
SEC_RETRY_FLAG = 0x7FB780
H_SESSION_SET = 0x13CEE4             # kwp_session_set: stb r3,0x803D3E
H_DDLI_WIPE = 0x35034                # kwp_sid_2C_h2: wipes all 10 dynamic ids

# scratch inside the external SRAM, above everything the application uses
IO_STRUCT = 0x807800
IO_BUFFER = 0x807900
IO_BUFFER_MAX = 0x100

#: patches/ff_fuel's calibration block; its magic is how a patched image
#: is recognised (patches/ff_fuel/src/ff_state.h).
FFCAL001_BASE = 0x5E2510

#: wire sub-function -> (internal session number, required security state)
SESSION_MAP = {
    0x81: (0, None),
    0x83: (3, None),
    0x85: (4, 2),        # programming; kwp.md 2 (flash), needs level 1
    0x86: (4, 3),        # upload;      needs level 2
    0x89: (5, None),
}

STATUS_POSITIVE = 1
STATUS_NEGATIVE = 2
STATUS_PENDING = (8, 9)


@dataclass
class KwpEntry:
    """One 20-byte row of the dispatch table (kwp.md 1.2)."""

    sid: int
    sub: int
    mask_a: int
    session_mask: int
    h1: int
    h2: int
    arg: int

    def allows(self, session: int) -> bool:
        return bool((1 << session) & self.session_mask)


# ---------------------------------------------------------------------------
# animated RAM
# ---------------------------------------------------------------------------
@dataclass
class AnimatedRam:
    """The cells the simulator moves, so a log is not a flat line.

    Values are deterministic functions of the simulated time, so a test can
    predict them exactly.  Everything else in RAM is whatever the emulator's
    reset left there (zeros outside flash).
    """

    #: seconds of ramp from idle to peak and back
    ramp_period: float = 20.0
    idle_rpm: float = 800.0
    peak_rpm: float = 3000.0
    #: True when the loaded image carries patches/ff_fuel (FFCAL001 at
    #: 0x5E2510).  Then 0x7FFB00 holds a live `struct ff_state` driven by
    #: `emu/models/flexfuel.py` instead of ff_counter's Flash-1 counter, so
    #: `21 6F` (measuring block 111) answers with moving numbers -- the
    #: rehearsal for issue #39.  The two patches are never co-flashed.
    flexfuel: bool = False
    #: ethanol the simulated Pico reports, in %
    flexfuel_e_pct: int = 85
    #: seconds of head start, so a one-shot `groups` request already shows a
    #: settled estimate instead of the first activation after power-up
    flexfuel_warm_s: float = 60.0
    #: which OS task set is live, "A" or "B" (re/findings/scheduler.md 11-12,
    #: brief C4): os_init installs set A and 0x11DA64 switches to set B when
    #: 0x7FEB5E != 0.  The counters of the other set stay frozen -- and so does
    #: C1's Flash-1 counter, whose hook sits in a set-B task.
    live_task_set: str = "B"
    #: static values written once at power-on: {address: (bytes)}
    statics: dict = field(default_factory=dict)

    #: the reference model, created lazily so a stock image never builds one
    _ff: object = None
    _ff_ticks: int = 0

    def flexfuel_block(self, t: float) -> bytes:
        """`struct ff_state` as the patch would have written it by time `t`."""
        from emu.models.flexfuel import Cal, FlexFuelModel, frame
        if self._ff is None:
            self._ff = FlexFuelModel(Cal())
            self._ff_ticks = 0
        want = int((t + self.flexfuel_warm_s) * 100.0)   # the 10 ms raster
        want = min(want, self._ff_ticks + 8000)          # bound the work
        while self._ff_ticks < want:
            self._ff_ticks += 1
            rx = (frame(e_pct=self.flexfuel_e_pct, t_fuel_c=25,
                        counter=(self._ff_ticks // 10) & 0xFF)
                  if self._ff_ticks % 10 == 0 else None)
            self._ff.tick(rx)
        return self._ff.full_bytes()

    def rpm(self, t: float) -> float:
        phase = (t % self.ramp_period) / self.ramp_period
        tri = 2 * phase if phase < 0.5 else 2 * (1 - phase)
        return self.idle_rpm + (self.peak_rpm - self.idle_rpm) * tri

    def apply(self, emu, t: float) -> None:
        rpm = self.rpm(t)
        # nmot_w, u16, 0.25 rpm/LSB (re/findings/scheduler.md, #44)
        emu.write(0x7FEE74, struct.pack(">H", min(int(rpm / 0.25), 0xFFFF)))
        # the measuring-block display byte for id 1: 40 rpm per count
        emu.write(0x7FCE95, bytes([min(int(rpm / 40), 0xFF)]))
        # rl, u16, 100/4096 % per LSB: follows the ramp between 18 % and 90 %
        load = 18.0 + 72.0 * (rpm - self.idle_rpm) / (self.peak_rpm - self.idle_rpm)
        emu.write(0x7FED38, struct.pack(">H", int(load * 4096 / 100)))
        # tmot, u8, T = 0.75*x - 48 degC: 20 degC warming to 90 degC over 120 s
        degc = 20.0 + 70.0 * min(t / 120.0, 1.0)
        emu.write(0x8021EF, bytes([int((degc + 48.0) / 0.75) & 0xFF]))
        emu.write(0x802228, struct.pack(">H", int((degc + 48.0) / 0.75 * 16)))
        # The raster activation counters.  Periods per brief C4
        # (re/findings/scheduler.md 11-12): 1 ms, 2 ms and 10 ms, i.e. 1000,
        # 500 and 100 counts/s -- NOT the 10/20/100 ms of scheduler.md 5.4.
        # Only the live set counts; the other stays at zero.
        set_b = self.live_task_set.upper() == "B"
        emu.write(0x7FD754, struct.pack(">I", 0 if set_b else int(t * 100)))
        emu.write(0x7FD75C, struct.pack(">I", 0 if set_b else int(t * 1000)))
        emu.write(0x7FD758, struct.pack(">I", int(t * 100) if set_b else 0))
        emu.write(0x7FD760, struct.pack(">I", int(t * 1000) if set_b else 0))
        emu.write(0x7FD778, struct.pack(">I", int(t * 500) if set_b else 0))
        # Flash 1: patches/ff_counter/ at build.ram = 0x7FFB00.  Its hook is in
        # a set-B task, so with set A live it never runs and the block stays
        # untouched -- the case the bench procedure has to be able to tell from
        # a failed flash.
        if self.flexfuel:
            emu.write(0x7FFB00, self.flexfuel_block(t if set_b else 0.0))
        else:
            emu.write(0x7FFB00, struct.pack(">I", int(t * 100) if set_b else 0))
            emu.write(0x7FFB04, struct.pack(">H", 0xFC01 if set_b else 0))
            emu.write(0x7FFB06, struct.pack(">H", 0))             # reserved

    def power_on(self, emu) -> None:
        for addr, value in self.statics.items():
            emu.write(addr, value)
        self.apply(emu, 0.0)


#: plausible constants for the addresses of logging/sessions/wave_b_confirm.json
#: that are not animated, so every row of #44 reads as something.
DEFAULT_STATICS = {
    0x8030C4: struct.pack(">H", 2400),     # ti_sum, 1 us/LSB  -> 2.4 ms
    0x8031DA: struct.pack(">H", 12000),    # prist, 0.005 bar  -> 60 bar
    0x8031F4: struct.pack(">H", 12200),    # prsoll            -> 61 bar
    0x7FEF87: bytes([0x14]),               # zw, s8, 0.75 deg  -> 15 deg
    0x7FCE57: bytes([0, 1, 0, 2, 0, 1]),   # dwkrz, six cylinders
    0x803088: struct.pack(">H", 0x0140),   # dwi
    0x80307E: struct.pack(">H", 0x00C8),   # wbho1s
    0x80316E: struct.pack(">H", 0x0200),
    0x8031F6: struct.pack(">H", 0x0010),
    0x80235A: struct.pack(">H", 0x0080),
    0x803038: struct.pack(">H", 0x0400),   # rk
    0x80302C: struct.pack(">H", 1024),     # ksta_kstaa, 1024 = 1.0
    0x802B08: struct.pack(">H", 0x0003),   # CAN 0x1A0 state word, module B
    0x802B72: struct.pack(">H", 0x0003),   # CAN 0x0C2 state word, module C
    0x802AFE: struct.pack(">H", 0x0003),   # CAN 0x050 state word, module C
    0x803EF4: bytes.fromhex("1122334455667788"),   # 0x1A0 data
    0x803F54: bytes.fromhex("99aabbccddeeff00"),   # 0x0C2 data
    0x803F60: bytes.fromhex("0f1e2d3c4b5a6978"),   # 0x050 data
}


# ---------------------------------------------------------------------------
# the handler layer
# ---------------------------------------------------------------------------
class Med9Handlers:
    """Answers a KWP request by running the firmware's own handler."""

    def __init__(self, dump_path: str = DUMP, *, seed: int | None = None,
                 animate: bool = True, ram: AnimatedRam | None = None,
                 clock=None, session_timeout_s: float | None = None):
        from emu import Med9Emu
        self.emu = Med9Emu(dump_path, r2="app")
        self.seed_override = seed
        self.animate = animate
        self.ram = ram or AnimatedRam(statics=dict(DEFAULT_STATICS))
        self.clock = clock or time.monotonic
        self.t0 = self.clock()
        #: P3: drop back to session 0 after this long without a KWP request.
        #: `None` = never, which is what an emulator does on its own; the real
        #: ECU times out in ~5 s (kwp.md 2.2, exact value not extracted).
        self.session_timeout_s = session_timeout_s
        self._last_request = self.clock()
        self.session_timeouts = 0
        self.table = self._read_table()
        self.log: list[str] = []
        #: A patched image is detected, not declared: FFCAL001 at 0x5E2510 is
        #: only there if patches/ff_fuel was applied, and then 0x7FFB00 carries
        #: `struct ff_state` rather than ff_counter's block (brief D2, #39).
        if self.emu.read(FFCAL001_BASE, 8) == b"FFCAL001":
            self.ram.flexfuel = True
        self.power_on()

    # -- setup -------------------------------------------------------------
    def _read_table(self) -> list[KwpEntry]:
        cfg = self.emu.read(KWP_CONFIG_STRUCT, 0x20)
        base = struct.unpack(">I", cfg[0:4])[0]
        count = cfg[0x1C]
        raw = self.emu.read(base, count * KWP_TABLE_ENTRY)
        out = []
        for i in range(count):
            e = raw[i * KWP_TABLE_ENTRY:(i + 1) * KWP_TABLE_ENTRY]
            mask_a = struct.unpack(">H", e[2:4])[0]
            smask, h1, h2, arg = struct.unpack(">IIII", e[4:20])
            out.append(KwpEntry(e[0], e[1], mask_a, smask, h1, h2, arg))
        return out

    def power_on(self) -> None:
        """Reset RAM to what a freshly powered ECU looks like for our purposes."""
        self.emu.write(SESSION_CURRENT, b"\x00")
        self.emu.write(SECURITY_STATE, b"\x00")
        self.emu.write(SEC_SEED, b"\x00\x00\x00\x00")
        # The application arms the security levels and the LFSR round count;
        # both are BSS in the dump.  tools/kwp_seckey_verify.py seeds the same.
        self.emu.write(SEC_LEVEL_FLAGS, bytes([0x03]))
        self.emu.write(SEC_LFSR_ROUNDS, bytes([5]))
        self.emu.write(SEC_RETRY_FLAG, b"\x00")
        self.ram.power_on(self.emu)
        self.t0 = self.clock()

    # -- state -------------------------------------------------------------
    @property
    def session(self) -> int:
        return self.emu.read(SESSION_CURRENT, 1)[0]

    @property
    def security_state(self) -> int:
        return self.emu.read(SECURITY_STATE, 1)[0]

    def sim_time(self) -> float:
        return self.clock() - self.t0

    def read_ram(self, addr: int, size: int) -> bytes:
        return self.emu.read(addr, size)

    # -- calling a real handler -------------------------------------------
    def _call(self, handler: int, sid: int, data: bytes, arg: int = 0):
        """Run `handler` with a seeded `kwp_io_struct`; return (status, bytes)."""
        if len(data) > IO_BUFFER_MAX - 8:
            raise ValueError("request longer than the simulated I/O buffer")
        s = bytearray(16)
        s[0:4] = struct.pack(">I", IO_BUFFER)
        struct.pack_into(">H", s, 6, len(data))
        s[0xA] = 0
        s[0xB] = sid & 0xFF
        s[0xC] = data[0] if data else 0        # the cached sub-function, kwp.md 12.1
        self.emu.write(IO_STRUCT, bytes(s))
        self.emu.write(IO_BUFFER, bytes(data) + b"\x00" * (IO_BUFFER_MAX - len(data)))
        res = self.emu.call(handler, args=[arg, IO_STRUCT], reset=False)
        if not res.ok:
            raise RuntimeError(
                f"handler {handler:#08x} for SID {sid:#04x} did not return "
                f"({res.stop_reason} at {res.pc:#08x}); {res.issues[:2]}")
        st = self.emu.read(IO_STRUCT, 16)
        rlen = struct.unpack(">H", st[8:10])[0]
        body = self.emu.read(IO_BUFFER, max(rlen, 1))[:rlen]
        return st[0xA], body

    # -- session -----------------------------------------------------------
    def _start_session(self, sub: int) -> list[bytes]:
        if sub not in SESSION_MAP:
            return [bytes([0x7F, 0x10, 0x11])]
        internal, needs = SESSION_MAP[sub]
        if needs is not None and self.security_state != needs:
            return [bytes([0x7F, 0x10, 0x33])]
        self.emu.call(H_SESSION_SET, args=[internal], reset=False)
        if sub in (0x85, 0x86):
            # kwp.md 2: entering the upload session resets the security state
            self.emu.write(SECURITY_STATE, b"\x00")
        # kwp.md 4.1: the dynamic ids are wiped on every session change, by
        # the real kwp_sid_2C_h2.
        self._call(H_DDLI_WIPE, 0x2C, b"\x04")
        return [bytes([0x50, sub])]

    # -- dispatch ----------------------------------------------------------
    def handle(self, request: bytes) -> list[bytes]:
        """One KWP request in, the list of KWP responses out (usually one)."""
        if not request:
            return []
        if self.animate:
            self.ram.apply(self.emu, self.sim_time())
        now = self.clock()
        if (self.session_timeout_s is not None and self.session != 0
                and now - self._last_request > self.session_timeout_s):
            # P3 expired: back to session 0, dynamic ids wiped (kwp.md 2.2)
            self.emu.call(H_SESSION_SET, args=[0], reset=False)
            self.emu.write(SECURITY_STATE, b"\x00")
            self._call(H_DDLI_WIPE, 0x2C, b"\x04")
            self.session_timeouts += 1
            self.log.append(f"session timed out after "
                            f"{now - self._last_request:.2f} s")
        self._last_request = now
        sid, data = request[0], request[1:]
        matching = [e for e in self.table if e.sid == sid]
        if not matching:
            return [bytes([0x7F, sid, 0x11])]
        allowed = [e for e in matching
                   if e.sub == 0xFF or (data and e.sub == data[0])]
        if not allowed:
            return [bytes([0x7F, sid, 0x12])]
        session = self.session
        entry = next((e for e in allowed if e.allows(session)), None)
        if entry is None:
            self.log.append(
                f"SID {sid:#04x} refused: session {session} not in mask "
                f"{allowed[0].session_mask:#06x}")
            return [bytes([0x7F, sid, 0x33])]

        if sid == 0x10:
            return self._start_session(data[0] if data else 0)

        status, body = self._call(entry.h1, sid, data, entry.arg)
        if sid == 0x27 and data[:1] == b"\x03" and self.seed_override is not None \
                and status == STATUS_POSITIVE:
            # substitute the time-base seed the emulator cannot produce
            self.emu.write(SEC_SEED, struct.pack(">I", self.seed_override))
            body = body[:1] + struct.pack(">I", self.seed_override) + body[5:]
        if status == STATUS_POSITIVE:
            return [bytes([(sid + 0x40) & 0xFF]) + body]
        if status == STATUS_NEGATIVE:
            return [bytes([0x7F, sid, body[0] if body else 0x10])]
        if status in STATUS_PENDING:
            # kwp.md 3.2: a wrong key defers, arms the lockout and answers
            # invalidKey.  Send the pending answer first, as the ECU does.
            return [bytes([0x7F, sid, 0x78]), bytes([0x7F, sid, 0x35])]
        return [bytes([0x7F, sid, 0x10])]


# ---------------------------------------------------------------------------
# the bus-facing simulator
# ---------------------------------------------------------------------------
class EcuSimulator:
    """`Tp20Server` + :class:`Med9Handlers`, with a poll loop you can thread."""

    def __init__(self, link: CanLink, handlers: Med9Handlers | None = None, *,
                 address: int = 0x01, delay_ms: float = 0.0,
                 drop_ack: int = 0, seed: int | None = None,
                 session_timeout_s: float | None = None, animate: bool = True,
                 dump: str = DUMP,
                 trace: list[str] | None = None, verbose: bool = False):
        self.link = link
        #: `dump` is how a PATCHED image is driven end to end: the handlers are
        #: the firmware's own, so `21 <group>` on patches/ff_fuel's image runs
        #: the patch's measuring handlers (brief D2, issue #39).
        self.handlers = handlers or Med9Handlers(
            dump, seed=seed, animate=animate,
            session_timeout_s=session_timeout_s)
        self.server = Tp20Server(link, address=address,
                                 params=Tp20Params(),
                                 delay_s=delay_ms / 1000.0, trace=trace)
        if drop_ack:
            self.server.drop_next_acks(drop_ack)
        self.verbose = verbose
        self.requests = 0
        self._stop = threading.Event()
        self.error: BaseException | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def on_virtual_bus(cls, channel: str = "med9", **kw) -> "EcuSimulator":
        link = open_link(parse_bus_spec(f"virtual:{channel}"))
        link.set_accept(None)
        return cls(link, **kw)

    # -- loop --------------------------------------------------------------
    def poll(self, timeout: float = 0.05) -> bool:
        """Service the bus once.  True if a request was answered."""
        request = self.server.poll(timeout)
        if request is None:
            return False
        self.requests += 1
        try:
            responses = self.handlers.handle(request)
        except Exception as exc:                            # pragma: no cover
            responses = [bytes([0x7F, request[0], 0x10])]
            self.handlers.log.append(f"handler crash: {exc!r}")
        if self.verbose:
            print(f"  -> {request.hex()}  <- "
                  + " ".join(r.hex() for r in responses), flush=True)
        for r in responses:
            self.server.send_message(r)
        return True

    def serve_forever(self, timeout: float = 0.05) -> None:
        try:
            while not self._stop.is_set():
                self.poll(timeout)
        except BaseException as exc:                         # pragma: no cover
            self.error = exc
            raise

    def stop(self) -> None:
        self._stop.set()

    @contextlib.contextmanager
    def background(self, timeout: float = 0.02):
        """Run `serve_forever` in a daemon thread for the duration of a block.

        The protocol core stays thread-free; this is only how a *test* (or
        `med9log.py --sim`) gets an ECU and a tester into the same process.
        """
        thread = threading.Thread(target=self.serve_forever, args=(timeout,),
                                  name="ecu_sim", daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self.stop()
            thread.join(timeout=5.0)

    def close(self) -> None:
        self.stop()
        self.link.close()


# ---------------------------------------------------------------------------
# self-test: the handlers alone, no bus
# ---------------------------------------------------------------------------
def self_test(dump_path: str = DUMP) -> bool:
    """Run every service the logger needs through the real handlers."""
    from med9kwp.kwp import key_level2

    h = Med9Handlers(dump_path, seed=0x12345678, animate=False)
    ok = True
    results: list[tuple[str, str, bool]] = []

    def step(label: str, request: bytes, expect_prefix: bytes | None = None):
        nonlocal ok
        answers = h.handle(request)
        got = answers[-1] if answers else b""
        good = expect_prefix is None or got.startswith(expect_prefix)
        ok = ok and good
        results.append((label, got.hex(), good))
        return got

    step("21 F0 before a session", b"\x21\xf0", b"\x7f\x21\x33")
    step("10 89 session", b"\x10\x89", b"\x50\x89")
    step("3E (no sub-function)", b"\x3e", b"\x7e")
    step("3E 01 -> NRC 0x12", b"\x3e\x01", b"\x7f\x3e\x12")
    h.emu.write(0x7FFB00, bytes.fromhex("000005CBFC010000"))
    step("2C F0 04 clear", b"\x2c\xf0\x04", b"\x6c\xf0")
    step("2C F0 03 def", b"\x2c\xf0\x03\x01\x06\x7f\xfb\x00", b"\x6c\xf0")
    step("21 F0 read", b"\x21\xf0", b"\x61\xf0\x00\x00\x05\xcb\xfc\x01")
    step("21 01 group", b"\x21\x01", b"\x61\x01")
    step("21 8C group 140", b"\x21\x8c", b"\x7f\x21\x31")
    step("35 without session 0x86", b"\x35\x7f\x80\x00\x00\x00\x01\x00",
         b"\x7f\x35\x33")
    seed_answer = step("27 03 seed", b"\x27\x03", b"\x67\x03\x12\x34\x56\x78")
    seed = int.from_bytes(seed_answer[2:6], "big") if len(seed_answer) >= 6 else 0
    step("27 04 key", b"\x27\x04" + key_level2(seed).to_bytes(4, "big"),
         b"\x67\x04\x34")
    step("10 86 upload session", b"\x10\x86", b"\x50\x86")
    h.emu.write(0x7F8000, bytes(range(256)))
    step("35 RAM ok", b"\x35\x7f\x80\x00\x00\x00\x01\x00", b"\x75\x3f")
    step("36 block", b"\x36", bytes([0x76]) + bytes(range(62)))
    step("37 exit", b"\x37", b"\x77")
    step("35 protected window", b"\x35\x7f\x9e\x00\x00\x00\x02\x00",
         b"\x7f\x35\x31")
    step("21 F0 after session change", b"\x21\xf0", b"\x7f\x21\x12")

    width = max(len(r[0]) for r in results)
    for label, hexed, good in results:
        print(f"  {label:<{width}}  {hexed:<28} {'ok' if good else 'MISMATCH'}")
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Emulated MED9.1.1 speaking TP2.0/KWP2000 on a CAN bus.")
    ap.add_argument("--bus", default="virtual:med9",
                    help="interface:channel (default virtual:med9)")
    ap.add_argument("--dump", default=DUMP, help="firmware image to run")
    ap.add_argument("--address", type=lambda s: int(s, 0), default=0x01,
                    help="logical address to answer on (default 0x01, engine)")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=0x12345678,
                    help="SecurityAccess seed to report (the time base is 0 "
                         "under Unicorn); 0 keeps the handler's own value")
    ap.add_argument("--drop-ack", type=int, default=0, metavar="N",
                    help="silently drop the next N TP2.0 ACKs")
    ap.add_argument("--delay-ms", type=float, default=0.0,
                    help="delay every frame this long")
    ap.add_argument("--no-animate", action="store_true",
                    help="freeze the animated RAM cells")
    ap.add_argument("--task-set", choices=("A", "B"), default="B",
                    help="which OS task set is live (scheduler.md 11-12). "
                         "With A, the set-B counters and C1's Flash-1 block "
                         "stay at zero -- rehearse that case before the bench")
    ap.add_argument("--session-timeout", type=float, default=0.0, metavar="S",
                    help="drop back to session 0 after S seconds without a KWP "
                         "request (0 = never; the real ECU is about 5 s)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the handlers directly and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return 0 if self_test(args.dump) else 1

    handlers = Med9Handlers(
        args.dump, seed=args.seed or None, animate=not args.no_animate,
        session_timeout_s=args.session_timeout or None,
        ram=AnimatedRam(live_task_set=args.task_set,
                        statics=dict(DEFAULT_STATICS)))
    link = open_link(parse_bus_spec(args.bus))
    link.set_accept(None)
    sim = EcuSimulator(link, handlers, address=args.address,
                       delay_ms=args.delay_ms, drop_ack=args.drop_ack,
                       verbose=args.verbose)
    print(f"MED9 simulator on {link.description}, logical address "
          f"{args.address:#04x}, seed {args.seed:#010x}")
    print("ctrl-C to stop")
    try:
        sim.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{sim.requests} requests served")
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
