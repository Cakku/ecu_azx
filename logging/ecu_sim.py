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
import collections
import contextlib
import json
import math
import os
import struct
import sys
import tempfile
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

#: what both 10 ms background tasks call to drain the EEP_CONF request queue
#: (re/findings/eeprom.md section 8.4)
NVM_PUMP_WRAPPER = 0x061944
#: the relative fuel mass the segment stub scales (injection.md section 6.1)
RK_FUEL_MASS = 0x803038
#: what the stock word at 0x42247C branches to, and what ff_fuel_rk_hook
#: tail-branches to once it has scaled `rk` (injection.md section 6.2).  A
#: STOCK baseline run calls this directly, so the two runs of the E0
#: equivalence comparison execute the same stock code.
RKSPLIT = 0x41C3A0

#: Stack pointer for the hook calls.  `Med9Emu.call()` parks r1 at the boot
#: stack top 0x7FEFFC, and a C function's frame there runs straight over
#: application variables -- `zwist_display_b1` (0x7FEF87) is 0x75 bytes below
#: it, and a stock-vs-patched comparison then differs by the frame rather than
#: by the patch.  On the real part every task has its own stack; the OS task
#: stacks are 0x7FF3C0-0x7FF76F (`re/findings/scheduler.md`, and the
#: patches/ff_fuel README puts its RAM block deliberately above them), so the
#: runner uses the top of that region.
TASK_STACK_TOP = 0x7FF768

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
    #: number of cylinders, for the segment rate the `rk` hook is called at
    cylinders: int = 6
    #: False once a :class:`PatchRunner` owns 0x7FFB00, so the stand-in stops
    #: writing a block the patch's own hooks are maintaining
    owns_patch_ram: bool = True

    #: the reference model, created lazily so a stock image never builds one
    _ff: object = None
    _ff_ticks: int = 0
    #: {address: the bytes this stand-in last wrote there}, for the cells that
    #: step aside once something else writes them (see `apply`)
    _owned: dict = field(default_factory=dict)

    def _own(self, emu, addr: int, size: int) -> bool:
        """True while `addr` still holds what this stand-in last put there."""
        last = self._owned.get(addr)
        return last is None or emu.read(addr, size) == last

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

    def load_pct(self, t: float) -> float:
        """rl, 18 % at idle to 90 % at peak -- the ramp everything follows."""
        rpm = self.rpm(t)
        return 18.0 + 72.0 * (rpm - self.idle_rpm) / (self.peak_rpm - self.idle_rpm)

    def rk_base(self, t: float) -> int:
        """`rk` (0x803038) as the stock segment task would have produced it.

        The stub at 0x42247C scales this cell **in place**, so a simulator
        that calls the hook more than once has to re-produce the upstream
        value first or `rk` would compound.  It is a plain function of load,
        which is all the E0-equivalence comparison of `procedure.md` section 4
        needs: at E0 the hook returns without writing, so the stock and the
        patched run must show the identical trace.
        """
        return min(int(512 + 24.0 * self.load_pct(t)), 0xFFFF)

    def dwkrz(self, t: float) -> bytes:
        """Six per-cylinder knock retards, 0x7FCE57..0x7FCE5C.

        `re/findings/ignition.md` section 5: the array is **s8 and <= 0** --
        the knock controller only ever takes advance away.  A constant
        positive value (which is what this file shipped until 2026-09-17)
        makes a `21 6C` rehearsal read something the real controller cannot
        produce, and `logging/sessions/ff_fuel.json` check 8a is exactly about
        these bytes.  Cylinders 2 and 5 are the two that knock first here, and
        the retard grows with load and decays between events, which is the
        shape `wkrm` (0x7FCE76, the mean) is debounced from.
        """
        load = self.load_pct(t)
        depth = max((load - 45.0) / 45.0, 0.0)           # nothing below 45 %
        wobble = 0.5 + 0.5 * math.sin(2.0 * math.pi * t / 3.0)
        counts = []
        for cyl, weight in enumerate((0.2, 1.0, 0.4, 0.0, 0.8, 0.3)):
            v = -int(round(8.0 * depth * weight * wobble))
            counts.append(max(v, -32) & 0xFF)            # -24 degCA at worst
        return bytes(counts)

    def apply(self, emu, t: float) -> None:
        rpm = self.rpm(t)
        # nmot_w, u16, 0.25 rpm/LSB (re/findings/scheduler.md, #44)
        emu.write(0x7FEE74, struct.pack(">H", min(int(rpm / 0.25), 0xFFFF)))
        # the measuring-block display byte for id 1: 40 rpm per count
        emu.write(0x7FCE95, bytes([min(int(rpm / 40), 0xFF)]))
        # rl, u16, 100/4096 % per LSB: follows the ramp between 18 % and 90 %
        load = self.load_pct(t)
        emu.write(0x7FED38, struct.pack(">H", int(load * 4096 / 100)))
        # rl_w (0x7FEFB2), the KFZW column axis and ff_dzw_map's x input
        emu.write(0x7FEFB2, struct.pack(">H", int(load * 4096 / 100)))
        # rk (0x803038): re-produced every activation, see rk_base()
        emu.write(0x803038, struct.pack(">H", self.rk_base(t)))
        # dwkrz (0x7FCE57..5C) and its mean wkrm (0x7FCE76), both s8 and <= 0.
        # These two yield to anyone who writes them: a test or a bench operator
        # poking a knock pattern into the simulator must not have it wiped out
        # by the next activation, which is how they behaved when they were
        # static values rather than an animation.
        knock = self.dwkrz(t)
        signed = [b - 256 if b & 0x80 else b for b in knock]
        mean = bytes([int(sum(signed) / len(signed)) & 0xFF])
        if self._own(emu, 0x7FCE57, 6):
            emu.write(0x7FCE57, knock)
            self._owned[0x7FCE57] = knock
        if self._own(emu, 0x7FCE76, 1):
            emu.write(0x7FCE76, mean)
            self._owned[0x7FCE76] = mean
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
        if not self.owns_patch_ram:
            return                      # a PatchRunner keeps 0x7FFB00 itself
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
    # dwkrz (0x7FCE57) is ANIMATED now, not static: it used to sit at
    # {0,1,0,2,0,1}, and +2 is a value the knock controller cannot produce
    # (ignition.md section 5: the array is s8 and <= 0).  AnimatedRam.dwkrz().
    0x803088: struct.pack(">H", 0x0140),   # dwi
    0x80307E: struct.pack(">H", 0x00C8),   # wbho1s
    0x80316E: struct.pack(">H", 0x0200),
    0x8031F6: struct.pack(">H", 0x0010),
    0x80235A: struct.pack(">H", 0x0080),
    0x80302C: struct.pack(">H", 1024),     # ksta_kstaa, 1024 = 1.0
    0x802B08: struct.pack(">H", 0x0003),   # CAN 0x1A0 state word, module B
    0x802B72: struct.pack(">H", 0x0003),   # CAN 0x0C2 state word, module C
    0x802AFE: struct.pack(">H", 0x0003),   # CAN 0x050 state word, module C
    0x803EF4: bytes.fromhex("1122334455667788"),   # 0x1A0 data
    0x803F54: bytes.fromhex("99aabbccddeeff00"),   # 0x0C2 data
    0x803F60: bytes.fromhex("0f1e2d3c4b5a6978"),   # 0x050 data
}


# ---------------------------------------------------------------------------
# running the patch: a simulated clock that drives the real hooks
# ---------------------------------------------------------------------------
def apply_patch_to_temp(patch_dir: str, dump_path: str = DUMP) -> str:
    """Apply a patch directory to a temporary image and return its path.

    `tools/patch_apply.py`'s checked-in `changes` are complete, so this needs
    no cross compiler (`docs/03_tooling.md` section 5).
    """
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import patch_apply                                   # noqa: E402

    import pathlib
    data, report, _warnings = patch_apply.apply_patch(
        pathlib.Path(dump_path), pathlib.Path(patch_dir))
    if not report["ok"]:
        raise RuntimeError(f"{patch_dir} did not apply: {report['issues']}")
    out = os.path.join(tempfile.mkdtemp(prefix="ecu_sim_"),
                       os.path.basename(os.path.normpath(patch_dir)) + ".bin")
    with open(out, "wb") as fh:
        fh.write(bytes(data))
    return out


class PatchRunner:
    """Calls `patches/ff_fuel`'s own hooks on the simulator's `Med9Emu`.

    Time here is **simulated**.  `advance(t)` runs whatever activations the
    patch would have had between the last call and simulated second `t`:

    * the 10 ms raster hook of the live task set, `1000 / ff_tick_ms` times
      per simulated second (100, `re/findings/scheduler.md` section 11);
    * the segment hook at 0x42247C once per simulated ignition segment, whose
      rate follows the animated rpm (`rpm * cylinders / 120` for a four-stroke
      engine), with `rk` re-produced upstream first (:meth:`AnimatedRam.rk_base`);
    * `nvm_pump_wrapper` (0x061944) once per 10 ms activation, because that is
      where the two stock background tasks call it (`eeprom.md` section 8.4)
      and without it a queued EEPROM commit never completes.

    The ignition stub at 0x41D40C is **not** called: it is a mid-function
    trampoline that returns into `zwgru_build`, so calling it in isolation
    would execute that function's tail with register state nobody produced.
    `ff_dzw_e` and `ff_fzw_q8` are computed by the 10 ms producer anyway, so
    measuring block 108 is live without it.
    """

    #: how much simulated time one `advance()` may make up in one go, so a
    #: slow host cannot stall the CAN bus while it catches up
    MAX_CATCHUP_S = 0.5

    def __init__(self, emu, patch_dir: str | None, *, task_set: str = "A",
                 ram: "AnimatedRam | None" = None, segments: bool = True,
                 nvm_pump: bool = True, tick_ms: float = 10.0):
        from emu.toucan import SLOT15, RxMailbox

        self.emu = emu
        self.patch_dir = patch_dir
        if patch_dir is None:
            #: STOCK mode: no patch, so the only thing to run per segment is
            #: what the unmodified word at 0x42247C calls.  This is what makes
            #: a stock baseline comparable with a --sim-patch run instead of
            #: differing by every cell rksplit touches.
            self.syms = {}
            self.hook = None
            self.rk_hook = RKSPLIT
        else:
            with open(os.path.join(patch_dir, "patch.json"), encoding="utf-8") as fh:
                build = json.load(fh)["build"]
            self.syms = {k: int(v, 0) for k, v in build["symbols"].items()}
            self.hook = self.syms["ff_fuel_hook_a" if task_set.upper() == "A"
                                  else "ff_fuel_hook_b"]
            self.rk_hook = self.syms.get("ff_fuel_rk_hook")
        self.task_set = task_set.upper()
        self.ram = ram
        self.segments_enabled = segments and self.rk_hook is not None
        self.nvm_pump = nvm_pump
        self.tick_s = tick_ms / 1000.0
        self.mailbox = RxMailbox(emu, SLOT15)
        self.can_id = (int.from_bytes(emu.read(FFCAL001_BASE + 0x0C, 2), "big")
                       if patch_dir is not None else 0x0EC)

        self.sim_t = 0.0
        self.ticks = 0
        self.segments = 0
        self.frames_in = 0
        self.errors: list[str] = []
        self._seg_accum = 0.0
        #: Frames wait in a short queue rather than overwriting one slot.  A
        #: real mailbox does overwrite, but simulated time moves in bursts of
        #: up to MAX_CATCHUP_S while the sender thread runs on the wall clock,
        #: so one slot would drop most of a burst and the ECU would see 2 Hz
        #: instead of 10.  The queue is bounded, so a runner that falls badly
        #: behind still drops the oldest, exactly as the hardware would.
        self._pending: "collections.deque[bytes]" = collections.deque(maxlen=8)
        self._lock = threading.Lock()
        self.mailbox.idle()

    # -- the bus side ------------------------------------------------------
    def on_frame(self, can_id: int, data: bytes) -> None:
        """Every frame the simulator sees; ours is copied into the mailbox."""
        if can_id == self.can_id and len(data) == 8:
            with self._lock:
                self._pending.append(bytes(data))
                self.frames_in += 1

    def _take_frame(self) -> bytes | None:
        with self._lock:
            return self._pending.popleft() if self._pending else None

    # -- the clock ---------------------------------------------------------
    def advance(self, target_s: float) -> None:
        """Run the hooks forward to simulated second `target_s`."""
        if target_s <= self.sim_t:
            return
        target_s = min(target_s, self.sim_t + self.MAX_CATCHUP_S)
        while self.sim_t + self.tick_s <= target_s:
            self.sim_t += self.tick_s
            self._one_tick()

    def _one_tick(self) -> None:
        if self.ram is not None:
            self.ram.apply(self.emu, self.sim_t)
        frame = self._take_frame()
        if frame is not None:
            self.mailbox.arm(frame)
        else:
            self.mailbox.idle()
        if self.hook is not None:
            self._call(self.hook, "10 ms hook")
        self.ticks += 1
        if self.nvm_pump:
            self._call(NVM_PUMP_WRAPPER, "nvm pump", max_insns=4_000_000)
        if self.segments_enabled:
            self._run_segments()

    def _run_segments(self) -> None:
        rpm = self.ram.rpm(self.sim_t) if self.ram is not None else 0.0
        cylinders = self.ram.cylinders if self.ram is not None else 6
        self._seg_accum += rpm * cylinders / 120.0 * self.tick_s
        n = int(self._seg_accum)
        self._seg_accum -= n
        base = (self.ram.rk_base(self.sim_t) if self.ram is not None else 1024)
        for _ in range(min(n, 64)):
            self.emu.write(RK_FUEL_MASS, struct.pack(">H", base))
            self._call(self.rk_hook, "rk hook")
            self.segments += 1

    def _call(self, addr: int, what: str, max_insns: int = 2_000_000) -> None:
        res = self.emu.call(addr, regs={"r1": TASK_STACK_TOP}, reset=False,
                            max_insns=max_insns)
        if not res.ok and len(self.errors) < 20:
            self.errors.append(
                f"{what} at {addr:#08x}: {res.stop_reason} at {res.pc:#08x} "
                f"{res.issues[:1]}")

    def status(self) -> str:
        return (f"sim {self.sim_t:.2f} s, {self.ticks} activations, "
                f"{self.segments} segments, {self.frames_in} frames in"
                + (f", {len(self.errors)} hook errors" if self.errors else ""))


class FrameTap:
    """A `CanLink` proxy that shows every received frame to a callback.

    `Tp20Server` drops anything that is not its own channel, so the ethanol
    frame would otherwise be invisible.  Wrapping the link keeps
    `logging/med9kwp/` free of any knowledge of the patch.
    """

    def __init__(self, link, on_frame):
        self._link = link
        self._on_frame = on_frame

    def recv(self, timeout: float):
        frame = self._link.recv(timeout)
        if frame is not None:
            self._on_frame(frame[0], frame[1])
        return frame

    def __getattr__(self, name):
        return getattr(self._link, name)


# ---------------------------------------------------------------------------
# the handler layer
# ---------------------------------------------------------------------------
class Med9Handlers:
    """Answers a KWP request by running the firmware's own handler."""

    def __init__(self, dump_path: str = DUMP, *, seed: int | None = None,
                 animate: bool = True, ram: AnimatedRam | None = None,
                 clock=None, session_timeout_s: float | None = None,
                 patch_dir: str | None = None, eeprom: str | None = None,
                 time_scale: float = 1.0, run_patch: bool = True,
                 stock_tasks: bool = False, wip_polls: int = 0):
        from emu import Med9Emu
        if patch_dir:
            dump_path = apply_patch_to_temp(patch_dir, dump_path)
        self.dump_path = dump_path
        self.emu = Med9Emu(dump_path, r2="app")
        self.seed_override = seed
        self.animate = animate
        self.ram = ram or AnimatedRam(statics=dict(DEFAULT_STATICS))
        self.clock = clock or time.monotonic
        #: simulated seconds per wall-clock second (see logging/README.md)
        self.time_scale = time_scale
        self.t0 = self.clock()
        self.eeprom_path = eeprom
        self.eeprom = None
        self.qspi = None
        self.runner: PatchRunner | None = None
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
        if eeprom:
            self._install_eeprom(eeprom, wip_polls)
        if (patch_dir and run_patch) or stock_tasks:
            self.runner = PatchRunner(self.emu, patch_dir if run_patch else None,
                                      task_set=self.ram.live_task_set,
                                      ram=self.ram if self.animate else None,
                                      nvm_pump=self.eeprom is not None)
            #: with the hooks running, `ff_state` is the patch's own, so the
            #: AnimatedRam stand-in must keep its hands off 0x7FFB00
            if patch_dir:
                self.ram.flexfuel = False
                self.ram.owns_patch_ram = False
        self.power_on()

    # -- the EEPROM device -------------------------------------------------
    def _install_eeprom(self, path: str, wip_polls: int) -> None:
        """Attach `emu/qspi_eeprom.py` and run the start-up block read."""
        from emu import qspi_eeprom as qe

        if not os.path.exists(path):
            image = qe.factory_image(DUMP)
            with open(path, "wb") as fh:
                fh.write(image)
            self.log.append(f"created a factory EEPROM image at {path}")
        self.eeprom = qe.M95160.load(path, wip_polls=wip_polls)
        self.qspi, _binding = qe.install_eeprom(self.emu, device=self.eeprom)
        if not qe.cold_start(self.emu):
            self.log.append("WARNING: nvm_read_all_blocks never reached idle")

    def save_eeprom(self, path: str | None = None) -> str | None:
        """Write the device image back, so a restart sees what was stored."""
        target = path or self.eeprom_path
        if self.eeprom is None or target is None:
            return None
        self.eeprom.save(target)
        return target

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
        """Simulated seconds since power-on.

        Without a :class:`PatchRunner` this is wall-clock time times
        ``time_scale``.  With one it is the runner's own clock, which only
        moves in 10 ms activations and can fall behind the wall clock on a
        slow host -- so every animated cell and every hook always see the same
        instant (`logging/README.md`, "bench rehearsal").
        """
        if self.runner is not None:
            return self.runner.sim_t
        return (self.clock() - self.t0) * self.time_scale

    def wall_target(self) -> float:
        return (self.clock() - self.t0) * self.time_scale

    def step(self) -> None:
        """Let the patch's hooks catch up with the wall clock."""
        if self.runner is not None:
            self.runner.advance(self.wall_target())

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
        self.step()
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
                 dump: str = DUMP, patch_dir: str | None = None,
                 eeprom: str | None = None,
                 trace: list[str] | None = None, verbose: bool = False):
        #: `dump` is how a PATCHED image is driven end to end: the handlers are
        #: the firmware's own, so `21 <group>` on patches/ff_fuel's image runs
        #: the patch's measuring handlers (brief D2, issue #39).  `patch_dir`
        #: goes one further and runs the patch's own hooks (brief E4).
        self.handlers = handlers or Med9Handlers(
            dump, seed=seed, animate=animate, patch_dir=patch_dir,
            eeprom=eeprom, session_timeout_s=session_timeout_s)
        runner = self.handlers.runner
        self.link = FrameTap(link, runner.on_frame) if runner else link
        link = self.link
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

    @property
    def runner(self) -> "PatchRunner | None":
        return self.handlers.runner

    # -- loop --------------------------------------------------------------
    def poll(self, timeout: float = 0.05) -> bool:
        """Service the bus once.  True if a request was answered."""
        request = self.server.poll(timeout)
        # the patch keeps ticking whether or not a tester is talking to us
        self.handlers.step()
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
        self.handlers.save_eeprom()
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
    ap.add_argument("--sim-patch", metavar="DIR", default=None,
                    help="apply a patch directory (patches/ff_fuel) to a "
                         "temporary image AND run its hooks on a simulated "
                         "10 ms raster -- the bench rehearsal of brief E4")
    ap.add_argument("--sim-stock-tasks", action="store_true",
                    help="without --sim-patch: still run the stock code the "
                         "patch's hooks replace (rksplit per segment, the NVM "
                         "pump per 10 ms), so a STOCK baseline log is "
                         "comparable with a --sim-patch run")
    ap.add_argument("--eeprom", metavar="FILE", default=None,
                    help="back the QSPI EEPROM with this 2 KB image "
                         "(emu/qspi_eeprom.py); created from the firmware's "
                         "own block defaults if it does not exist, and "
                         "written back when the simulator stops")
    ap.add_argument("--wip-polls", type=int, default=0, metavar="N",
                    help="make an EEPROM page write report WIP for N status "
                         "polls (0 = instant, which is what a time base that "
                         "never advances gives us anyway)")
    ap.add_argument("--time-scale", type=float, default=1.0, metavar="X",
                    help="simulated seconds per wall-clock second (default 1)")
    ap.add_argument("--task-set", choices=("A", "B"), default="A",
                    help="which OS task set is live (scheduler.md 11-12, "
                         "11.8: set A is live by necessity, so it is the "
                         "default here). With A, the set-B counters and C1's "
                         "Flash-1 block stay at zero")
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
        patch_dir=args.sim_patch, eeprom=args.eeprom,
        stock_tasks=args.sim_stock_tasks,
        time_scale=args.time_scale, wip_polls=args.wip_polls,
        ram=AnimatedRam(live_task_set=args.task_set,
                        statics=dict(DEFAULT_STATICS)))
    link = open_link(parse_bus_spec(args.bus))
    link.set_accept(None)
    sim = EcuSimulator(link, handlers, address=args.address,
                       delay_ms=args.delay_ms, drop_ack=args.drop_ack,
                       verbose=args.verbose)
    print(f"MED9 simulator on {link.description}, logical address "
          f"{args.address:#04x}, seed {args.seed:#010x}")
    if handlers.runner is not None:
        r = handlers.runner
        print(f"running {args.sim_patch} on task set {r.task_set}: 10 ms hook "
              f"{r.hook:#08x}, segment hook {r.rk_hook:#08x}, "
              f"CAN id {r.can_id:#05x}, {args.time_scale:g} simulated s / "
              f"wall s")
    if handlers.eeprom is not None:
        print(f"EEPROM: {args.eeprom} (2 KB M95160, written back on exit)")
    print("ctrl-C to stop")
    try:
        sim.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{sim.requests} requests served")
        if handlers.runner is not None:
            print(handlers.runner.status())
            for line in handlers.runner.errors[:5]:
                print("  !", line)
    finally:
        sim.close()
    for line in handlers.log[-5:]:
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
