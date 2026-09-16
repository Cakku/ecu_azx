#!/usr/bin/env python3
"""Reference model of the ff_fuel flex-fuel patch (brief D1, issues #32/#37).

This is the *specification* of `patches/ff_fuel/src/ff_fuel.c`: frame
validation, the OK/HOLD/FAULT state machine, the filter and the slew limiter,
the F curve and the `rk` scaling, in the same integer arithmetic the patch
uses.  `tests/test_ff_fuel_patch.py` runs the compiled patch in the Unicorn
harness and compares the RAM state block against this model tick by tick, so
if the two disagree one of them is wrong and the test says which.

Unlike `emu/models/injection.py` this models *our* code, not the ECU's, so
nothing here is reverse-engineered.  What is taken from the RE is only:

* the frame layout (`docs/05_flexfuel_design.md` §2, `re/findings/can.md` §7);
* `rk` at 0x803038 being u16 and the factor being Q10
  (`re/findings/injection.md` §6.3);
* the real raster period, 10 ms (`re/findings/scheduler.md` §11).

Fixed point
-----------
``e_filt``  u16, **1/16 %**, 0..1600.  6.25 % = one curve breakpoint = 100.
``e_frac``  u16, 1/1024 of one ``e_filt`` count.  The pair is a 26-bit value
            in units of 1/16384 %; without it the 2 %/s slew limit
            (0.32 counts per 10 ms activation) would truncate to zero and
            freeze the filter.  ``e_filt`` alone is what everything reads.
``f_q10``   u16, 1/1024, clamped to [1024, 2048] in code whatever the
            calibration says.  1024 is bit-identical to stock.

Time
----
Every calibration constant is in physical units (ms, s, %/s) and is converted
with ``ff_tick_ms`` — the real period of the periodic hook, itself a
calibration value (10 ms, `scheduler.md` §11).  Moving the hook to another
raster is therefore a calibration change, not a rebuild.

Usage::

    from emu.models.flexfuel import FlexFuelModel, Cal, frame
    m = FlexFuelModel(Cal())
    m.tick(frame(e_pct=85, status=0, counter=1))       # one 10 ms activation
    print(m.state.e_filt, m.state.f_q10, m.state.mode)

    python3 -m emu.models.flexfuel            # print the curve and a step response
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --- modes (the values written to the state block) ---------------------------
MODE_INIT = 0
MODE_OK = 1
MODE_HOLD = 2
MODE_FAULT = 3
MODE_OFF = 4
MODE_OVERRIDE = 5

MODE_NAME = {MODE_INIT: "INIT", MODE_OK: "OK", MODE_HOLD: "HOLD",
             MODE_FAULT: "FAULT", MODE_OFF: "OFF", MODE_OVERRIDE: "OVERRIDE"}

# --- the two periodic hook sources (scheduler.md §11.4, §8.1) ----------------
SRC_A = 1          # 0x432940, task set A  (0x4328E4)
SRC_B = 2          # 0x12067C, task set B  (0x1205A0)
OWNER_SWITCH = 3   # consecutive foreign calls before ownership moves

E_FILT_MAX = 1600  # 100 % in 1/16 %
F_MIN, F_MAX = 1024, 2048
FRAC = 1024        # sub-count resolution of e_frac

CURVE_N = 17       # ff_F_curve points, one every 6.25 % = 100 counts
CURVE_STEP = 100

CORE_OFF = 0x08    # first checksummed byte of the state block
CORE_LEN = 0x24    # +0x08..+0x2B; the annex above it has other writers
BLOCK_LEN = 0x40


# ---------------------------------------------------------------- the frame --
def frame(e_pct: int = 0, t_fuel_c: int = 20, freq_hz: int = 100,
          counter: int = 0, fw: int = 1, status: int = 0) -> bytes:
    """One Pico node frame, id 0x0EC (docs/05 §2)."""
    return bytes((e_pct & 0xFF, (t_fuel_c + 40) & 0xFF, (freq_hz // 2) & 0xFF,
                  counter & 0xFF, 0, 0, fw & 0xFF, status & 0xFF))


# ------------------------------------------------------------- the F formula --
def fuel_mass_factor(v: float) -> float:
    """docs/05 §3.3: required fuel **mass** factor at volume fraction `v`.

    Densities 0.745 (gasoline) and 0.789 (ethanol) kg/L, stoichiometric AFR
    14.7 and 9.0 by mass.  F(0) == 1.0 exactly.
    """
    w = 0.789 * v / (0.789 * v + 0.745 * (1.0 - v))
    afr = 1.0 / (w / 9.0 + (1.0 - w) / 14.7)
    return 14.7 / afr


def f_curve_from_formula(n: int = CURVE_N) -> list[int]:
    """The shipped `ff_F_curve`, Q10, one point every 100/(n-1) % ethanol."""
    out = []
    for i in range(n):
        v = i / (n - 1)
        out.append(1024 if i == 0 else int(round(fuel_mass_factor(v) * 1024.0)))
    return out


# ------------------------------------------------------------- calibration ---
@dataclass
class Cal:
    """FFCAL001 as the patch reads it (see patches/ff_fuel/ffcal001.py)."""
    can_id: int = 0x0EC
    timeout_ms: int = 1000
    hold_s: int = 60
    filter_tau_ms: int = 3000
    slew_pct_s: int = 2
    tick_ms: int = 10
    mode: int = 1
    e_override: int = 0
    stall_max: int = 3
    persist_enable: int = 0
    persist_hyst_pct: int = 5
    persist_block: int = 8
    persist_offset: int = 0
    persist_rate_s: int = 60
    f_curve: list[int] = field(default_factory=f_curve_from_formula)
    valid: bool = True

    # The clamps the patch applies to whatever the calibration says.
    def tick(self) -> int:
        return min(max(self.tick_ms, 1), 1000)

    def tau(self) -> int:
        return max(self.filter_tau_ms, self.tick())

    def slew(self) -> int:
        """Maximum |dE| per activation, in 1/16384 %."""
        pct_s = min(max(self.slew_pct_s, 1), 100)
        return (pct_s * 16 * FRAC * self.tick()) // 1000

    def hold_ticks(self) -> int:
        return (self.hold_s * 1000) // self.tick()

    def stall(self) -> int:
        return max(self.stall_max, 1)

    def override(self) -> int:
        return min(self.e_override, 100)


# ------------------------------------------------------------ the RAM state --
@dataclass
class State:
    """`struct ff_state` — patches/ff_fuel/src/ff_state.h, 64 bytes."""
    # header
    magic: int = 0
    length: int = 0
    csum: int = 0
    # core (checksummed, written only by the periodic tick)
    e_filt: int = 0
    f_q10: int = F_MIN
    mode: int = MODE_INIT
    status: int = 0xFF
    e_raw: int = 0
    t_fuel: int = 0
    frame_ctr: int = 0
    fw_ver: int = 0
    cal_mode: int = 0
    cal_ok: int = 0
    age_ticks: int = 0
    hold_ticks: int = 0
    frames: int = 0
    faults: int = 0
    stall: int = 0
    src_owner: int = 0
    src_seen: int = 0
    src_foreign: int = 0
    ticks: int = 0
    e_key: int = 0
    e_frac: int = 0
    frame_bad: int = 0
    reserved_core0: int = 0
    reserved_core1: int = 0
    # annex (not checksummed; D2 owns most of it)
    rk_calls: int = 0
    e_persist: int = 0
    persist_state: int = 0
    persist_err: int = 0
    diag_e_pct: int = 0
    diag_f_pct: int = 0
    diag_t_degc: int = 0


def _tdiv(n: int, d: int) -> int:
    """C99 integer division: truncate toward zero (Python's // floors)."""
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def _sat16(v: int) -> int:
    return 0xFFFF if v > 0xFFFF else v


def _sat8(v: int) -> int:
    return 0xFF if v > 0xFF else v


class FlexFuelModel:
    """One instance == one ECU.  `tick()` is one activation of the 10 ms hook."""

    def __init__(self, cal: Cal | None = None, *, cold: bool = True):
        self.cal = cal or Cal()
        self.state = State()
        self.can_init_calls = 0
        self.poll_calls = 0
        if cold:
            self.init_state()

    # -- the block header ------------------------------------------------
    MAGIC = 0x46463031          # "FF01"
    LENGTH = BLOCK_LEN

    def init_state(self) -> None:
        """What `ff_state_init()` does: zero the 64 bytes, then seed them."""
        c = self.cal
        self.state = State()
        st = self.state
        st.magic, st.length = self.MAGIC, self.LENGTH
        st.mode = MODE_FAULT            # FAULT until the first valid frame
        st.f_q10 = F_MIN                # E0 behaviour, bit-identical to stock
        st.status = 0xFF
        st.cal_ok = 1 if c.valid else 0
        st.cal_mode = c.mode if c.valid else 0
        if st.cal_ok and c.mode == 1:
            self.can_init_calls += 1    # can_init_mb(15), idempotent (can.md §7)
        self.seal()

    def state_valid(self) -> bool:
        st = self.state
        return (st.magic == self.MAGIC and st.length == self.LENGTH
                and st.csum == self.checksum())

    def checksum(self) -> int:
        """Bit-complement of the 16-bit sum of the core bytes (+0x08..+0x27)."""
        return (~sum(self.core_bytes())) & 0xFFFF

    def core_bytes(self) -> bytes:
        st = self.state
        out = bytearray()
        out += st.e_filt.to_bytes(2, "big")
        out += st.f_q10.to_bytes(2, "big")
        out += bytes((st.mode, st.status, st.e_raw, st.t_fuel,
                      st.frame_ctr, st.fw_ver, st.cal_mode, st.cal_ok))
        out += st.age_ticks.to_bytes(2, "big")
        out += st.hold_ticks.to_bytes(2, "big")
        out += st.frames.to_bytes(2, "big")
        out += st.faults.to_bytes(2, "big")
        out += bytes((st.stall, st.src_owner, st.src_seen, st.src_foreign))
        out += st.ticks.to_bytes(4, "big")
        out += st.e_key.to_bytes(2, "big")
        out += st.e_frac.to_bytes(2, "big")
        out += bytes((st.frame_bad, st.reserved_core0))
        out += st.reserved_core1.to_bytes(2, "big")
        assert len(out) == CORE_LEN
        return bytes(out)

    def seal(self) -> None:
        self.state.csum = self.checksum()

    def block_bytes(self) -> bytes:
        """The 0x28 bytes of header + core, exactly as they sit at PATCH_RAM.

        This is what `tests/test_ff_fuel_patch.py` compares the emulated RAM
        against, tick by tick.  The annex (+0x2C..+0x3F) is deliberately left
        out: the segment task and brief D2 write it, not the periodic tick.
        """
        st = self.state
        return struct.pack(">IHH", st.magic, st.length, st.csum) + self.core_bytes()

    # -- the F curve ------------------------------------------------------
    def f_of(self, e_filt: int) -> int:
        curve = self.cal.f_curve
        if e_filt >= E_FILT_MAX:
            f = curve[CURVE_N - 1]
        else:
            i = e_filt // CURVE_STEP
            fr = e_filt % CURVE_STEP
            a, b = curve[i], curve[i + 1]
            f = a + _tdiv((b - a) * fr, CURVE_STEP)
        return min(max(f, F_MIN), F_MAX)

    # -- the filter and the slew limiter ----------------------------------
    def _move(self, target_e16: int, *, filtered: bool) -> None:
        """Move E towards `target_e16` (1/16 %) by one activation."""
        c, st = self.cal, self.state
        now = st.e_filt * FRAC + st.e_frac
        target = target_e16 * FRAC
        d = target - now
        if d == 0:
            return
        if filtered:
            step = _tdiv(d * c.tick(), c.tau())
            if step == 0:
                step = 1 if d > 0 else -1
        else:
            step = d
        slew = c.slew()
        step = min(step, slew)
        step = max(step, -slew)
        now += step
        now = min(max(now, 0), E_FILT_MAX * FRAC)
        st.e_filt = now // FRAC
        st.e_frac = now % FRAC

    # -- one activation ---------------------------------------------------
    def tick(self, rx: bytes | None = None, *, src: int = SRC_B,
             can_id_echo: int | None = None) -> State:
        """One periodic-hook activation.

        `rx` is the frame `can_rx_poll(15)` would report as fresh (DLC 8), or
        None for "nothing new since the last call".
        """
        st = self.state
        if not self.state_valid():
            self.init_state()
            st = self.state

        # --- which of the two hooks owns the tick (scheduler.md §8.1) ----
        st.src_seen |= src
        if st.src_owner == 0:
            st.src_owner = src
        if st.src_owner != src:
            st.src_foreign = _sat8(st.src_foreign + 1)
            if st.src_foreign < OWNER_SWITCH:
                self.seal()
                return st
            st.src_owner, st.src_foreign = src, 0
        else:
            st.src_foreign = 0

        # cal_ok is latched by init_state(): FFCAL001 lives in flash and its
        # checksum is 230 bytes long, so the patch checks it once.
        c = self.cal
        st.ticks = (st.ticks + 1) & 0xFFFFFFFF
        st.cal_mode = c.mode if st.cal_ok else 0

        if not st.cal_ok or st.cal_mode == 0:
            st.mode = MODE_OFF
            st.f_q10 = F_MIN
            self.seal()
            return st

        if st.cal_mode == 2:                       # bench override
            st.mode = MODE_OVERRIDE
            st.age_ticks = 0
            self._move(c.override() * 16, filtered=True)
            st.f_q10 = self.f_of(st.e_filt)
            self.seal()
            return st

        # --- normal operation --------------------------------------------
        self.poll_calls += 1
        if rx is not None:
            assert len(rx) == 8
            bad = bool(can_id_echo is not None and can_id_echo != c.can_id)
            st.stall = _sat8(st.stall + 1) if rx[3] == st.frame_ctr else 0
            st.frame_ctr, st.status = rx[3], rx[7]
            st.e_raw, st.t_fuel, st.fw_ver = rx[0], rx[1], rx[6]
            if rx[0] > 100 or rx[7] in (1, 3) or st.stall >= c.stall():
                bad = True
            # a latch, not an event: it has to survive the nine activations
            # between two 10 Hz frames (ff_state.h)
            st.frame_bad = 1 if bad else 0
            if not bad:
                st.age_ticks = 0
                st.frames = _sat16(st.frames + 1)

        st.age_ticks = _sat16(st.age_ticks + 1)
        timed_out = st.age_ticks * c.tick() > c.timeout_ms

        if st.frame_bad or timed_out or st.frames == 0:
            new_mode = MODE_FAULT
        elif st.status == 2:
            new_mode = MODE_HOLD
        else:
            new_mode = MODE_OK

        if new_mode == MODE_FAULT and st.mode != MODE_FAULT:
            st.faults = _sat16(st.faults + 1)
            st.hold_ticks = min(c.hold_ticks(), 0xFFFF)
        st.mode = new_mode

        if new_mode == MODE_OK:
            self._move(st.e_raw * 16, filtered=True)
        elif new_mode == MODE_FAULT:
            if st.hold_ticks:
                st.hold_ticks -= 1                 # F and E are both held
            else:
                self._move(st.e_key, filtered=False)   # decay at ff_slew
        # HOLD: E is frozen, so F is frozen too

        st.f_q10 = self.f_of(st.e_filt)
        self.seal()
        return st

    # -- the segment-synchronous half -------------------------------------
    def rk_scale(self, rk: int) -> int:
        """What the 0x42247C stub does to `rk` (injection.md §6.3)."""
        st = self.state
        self.state.rk_calls = (st.rk_calls + 1) & 0xFFFFFFFF
        if st.magic != self.MAGIC:
            return rk                              # no valid state -> stock
        f = st.f_q10
        if f <= F_MIN:
            return rk                              # bit-identical fast path
        if f > F_MAX:
            f = F_MAX
        return min((rk * f) >> 10, 0xFFFF)


# ------------------------------------------------------------------- main ----
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--e", type=int, default=85)
    a = ap.parse_args(argv)

    curve = f_curve_from_formula()
    print("ff_F_curve (Q10), one point every 6.25 % ethanol:")
    for i, v in enumerate(curve):
        print(f"  E{i * 100 // (CURVE_N - 1):3d} %   {v:5d}   {v / 1024:.4f}")

    m = FlexFuelModel()
    per_s = 1000 // m.cal.tick()
    n = int(a.seconds * per_s)
    print(f"\nstep to E{a.e} at t = 0 ({m.cal.tick()} ms per activation)")
    print("   t/s  mode      E_filt %   F      rk 2000 ->")
    for k in range(n):
        rx = frame(e_pct=a.e, counter=(k // 10) & 0xFF) if k % 10 == 0 else None
        st = m.tick(rx, can_id_echo=m.cal.can_id)
        if k % per_s == 0 or k == n - 1:
            print(f"  {k / per_s:5.1f}  {MODE_NAME[st.mode]:<9} "
                  f"{st.e_filt / 16:7.2f}   {st.f_q10:5d}  {m.rk_scale(2000):6d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
