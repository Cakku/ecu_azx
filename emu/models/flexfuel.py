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

# --- E1 (#34): the ignition blend -------------------------------------------
DZW_N = 8              # ff_dzw_map is DZW_N rows (nmot) x DZW_N columns (rl)
FZW_MAX = 255          # the u8 ceiling of ff_fzw_curve; 255/256 = 0.996
FZW_ONE = 256          # the divisor, so f_zw = ff_fzw_curve[..] / 256
FZW_PLATEAU = 8        # curve index where f_zw reaches its plateau: E50
DZW_HARD_MAX = 16      # |dzw_e| ceiling in CODE, 12.00 degCA, whatever the cal

#: ff_dzw_map's nmot breakpoints: every other breakpoint of the stock KFZW
#: nmot axis 0x5C7736 (indices 0,3,5,7,9,11,13,15), u16 in nmot_w units of
#: 0.25 rpm -- 520, 1000, 2000, 2920, 3720, 4520, 5520, 6520 rpm.
DZW_NMOT_AXIS = (2080, 4000, 8000, 11680, 14880, 18080, 22080, 26080)

#: ff_dzw_map's rl breakpoints: eight of the twelve breakpoints of the stock
#: KFZW rl axis 0x5C7758 (indices 0,2,4,5,6,8,10,11), u16 in rl_w units of
#: 100/4096 % -- 10.16, 21.09, 31.25, 41.41, 52.34, 72.66, 93.75, 103.91 %.
DZW_RL_AXIS = (416, 864, 1280, 1696, 2144, 2976, 3840, 4256)

CORE_OFF = 0x08    # first checksummed byte of the state block
CORE_LEN = 0x24    # +0x08..+0x2B; the annex above it has other writers
CORE2_OFF = 0x40   # E2 (#35): the second checksummed range, past the annex
CORE2_LEN = 0x04   # +0x40..+0x43
STATE_LEN = 0x44   # sizeof(struct ff_state); E2 grew it from 0x40
BLOCK_LEN = 0x44

# --- E2 (#35): the start enrichment -------------------------------------
FST_N = 6              # ff_fst_map is FST_N ethanol rows x FST_N tmst columns
FST_ONE = 1024         # ff_fst_map[..] / 1024 is the factor
FST_HARD_MAX = 2560    # the CODE ceiling on fst_q10, 2.50x (see ff_state.h)
ZWST_HARD_MAX = 8      # the CODE ceiling on zwst_add, 6.00 degCA

#: `ff_fst_e_axis`, ethanol volume percent.  85 because E85 is the target.
FST_E_AXIS = (0, 20, 40, 60, 85, 100)

#: `ff_fst_tmst_axis`, in `tmst` counts of 0.75 degC with a -48 degC offset:
#: -30, -15, 0, +20.25, +39.75, +90 degC.  Six of the twelve breakpoints of the
#: stock KFWKSTT tmst axis 0x5C6C62, so every cell lines up with a stock row
#: (re/findings/start.md section 9.4).  117 is also the shipped
#: `ff_zwst_tmax`, so the temperature at which the start ADVANCE switches off
#: is a breakpoint of the start FUEL map rather than a number between two.
FST_TMST_AXIS = (24, 44, 64, 91, 117, 184)


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


def fzw_curve_default(n: int = CURVE_N, plateau: int = FZW_PLATEAU) -> list[int]:
    """The shipped `ff_fzw_curve`, 1/256, docs/05 section 3.4 (brief E1).

    "0 at E0, 1 at about E40-50 where MBT is usually reached": a straight ramp
    from 0 at E0 to the u8 maximum at E50 (`plateau` = index 8), flat above it.
    `fzw[0] == 0` is the ignition counterpart of `F(0) == 1024`; it is what
    makes E0 bit-identical no matter what `ff_dzw_map` contains.
    """
    return [min(round(i * FZW_MAX / plateau), FZW_MAX) for i in range(n)]


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
    # --- E1 (#34): the ignition blend, appended by FFCAL001 v2 -------------
    zw_enable: int = 0
    dzw_max: int = 8
    fzw_curve: list[int] = field(default_factory=fzw_curve_default)
    dzw_map: list[int] = field(default_factory=lambda: [0] * (DZW_N * DZW_N))
    dzw_nmot_axis: list[int] = field(default_factory=lambda: list(DZW_NMOT_AXIS))
    dzw_rl_axis: list[int] = field(default_factory=lambda: list(DZW_RL_AXIS))
    # --- E2 (#35): the start enrichment, appended by FFCAL001 v3 ----------
    st_enable: int = 0
    zwst_enable: int = 0
    fst_max: int = 2048
    zwst_max: int = 4
    zwst_tmax: int = 117           # tmst count; 39.75 degC
    fst_map: list[int] = field(
        default_factory=lambda: [FST_ONE] * (FST_N * FST_N))
    fst_e_axis: list[int] = field(default_factory=lambda: list(FST_E_AXIS))
    fst_tmst_axis: list[int] = field(default_factory=lambda: list(FST_TMST_AXIS))
    fzwst_curve: list[int] = field(default_factory=lambda: [0] * FST_N)

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

    def dzw_ceiling(self) -> int:
        """|dzw_e| ceiling, clamped in CODE to DZW_HARD_MAX (brief E1)."""
        return min(self.dzw_max, DZW_HARD_MAX)

    def fst_ceiling(self) -> int:
        """`fst_q10` ceiling, clamped in CODE to FST_HARD_MAX (brief E2)."""
        return max(min(self.fst_max, FST_HARD_MAX), FST_ONE)

    def zwst_ceiling(self) -> int:
        """`zwst_add` ceiling, clamped in CODE to ZWST_HARD_MAX (brief E2)."""
        return min(self.zwst_max, ZWST_HARD_MAX)


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
    # +0x29 / +0x2A: D1 reserved them inside the checksummed core; E1 (#34)
    # gave them names.  No offset moved and the block is still 0x40 bytes.
    dzw_e: int = 0        # s8, 0.75 degCA per count, positive = advance
    fzw_q8: int = 0       # u16, 1/256, the blend factor the offset was scaled by
    # annex (not checksummed; D2 owns most of it)
    rk_calls: int = 0
    e_persist: int = 0
    persist_state: int = 0
    persist_err: int = 0
    diag_e_pct: int = 0
    diag_f_pct: int = 0
    diag_t_degc: int = 0
    persist_wait: int = 0
    persist_writes: int = 0
    persist_fails: int = 0
    # core 2 (+0x40..+0x43, checksummed) -- E2 (#35)
    fst_q10: int = FST_ONE   # u16, 1/1024: the start fuel factor f_st(E, tmst)
    zwst_add: int = 0        # s8, 0.75 degCA per count: the start advance
    st_reserved: int = 0     # always 0; reserved for brief E5


def _tdiv(n: int, d: int) -> int:
    """C99 integer division: truncate toward zero (Python's // floors)."""
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def _sat16(v: int) -> int:
    return 0xFFFF if v > 0xFFFF else v


def _sat8(v: int) -> int:
    return 0xFF if v > 0xFF else v


def _s8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


def axis_key8(axis, value: int) -> int:
    """`(index << 16) | frac` for a strictly increasing u16 breakpoint list.

    The stock `axis_search_u16_hint` (0x40C9CC) with the hint fixed at 0 --
    `emu/zw_model.py` models it and `tests/test_zw_model.py` proves that model
    against the real function for every breakpoint and every hint.  Below the
    first breakpoint and on or above the last one the fraction is 0, which is
    what keeps `interp8_s8` from ever reading past the end of the map.

    `src/ff_ign.c`'s `ff_axis8()` is a fixed-length rewrite of exactly this,
    because patch code may not contain a loop whose trip count is data.
    """
    n = len(axis)
    if value <= axis[0]:
        return 0
    if value >= axis[n - 1]:
        return (n - 1) << 16
    for i in range(n - 1):
        if axis[i] <= value < axis[i + 1]:
            span = axis[i + 1] - axis[i]
            frac = ((value - axis[i]) << 16) // span if span > 0 else 0
            return (i << 16) | frac
    return 0                      # a non-monotonic axis: the safe cell


def axis_key6(axis, value: int, mul: int = 1) -> int:
    """`(index << 16) | frac` for a 6-point u8 axis (brief E2).

    `src/ff_start.c`'s `ff_axis6()`, which is `ff_axis8()` over a u8 breakpoint
    list.  `mul` puts the axis and the value into the same units: the tmst axis
    is searched with 1, the ethanol axis with 16 because `e_filt` is in 1/16 %
    while the axis is in whole percent.  The comparison stays exact -- a
    breakpoint at 85 % is exactly `e_filt` 1360.
    """
    n = len(axis)
    if value <= axis[0] * mul:
        return 0
    if value >= axis[n - 1] * mul:
        return (n - 1) << 16
    for i in range(n - 1):
        lo, hi = axis[i] * mul, axis[i + 1] * mul
        if lo <= value < hi:
            return (i << 16) | (((value - lo) << 16) // (hi - lo))
    return 0                      # a non-monotonic axis: the safe cell


def interp6_u16(values, key_y: int, key_x: int, nx: int = FST_N) -> int:
    """`ff_interp6()` of src/ff_start.c: bilinear over an nx-wide u16 map.

    Same index order and same arithmetic as `interp8_s8`, only over u16 cells
    and with every corner clamped to FST_HARD_MAX **before** it is used -- which
    is what keeps the widest intermediate product at 2560 * 65535, inside s32,
    on the ECU.  The result is floored at FST_ONE: `f_st` only ever enriches.
    """
    def cell(p: int) -> int:
        return min(values[p], FST_HARD_MAX)

    fx, ix = key_x & 0xFFFF, key_x >> 16
    fy, iy = key_y & 0xFFFF, key_y >> 16
    p = ix + nx * iy
    v = cell(p)
    if fx:
        v = v + ((fx * (cell(p + 1) - v)) >> 16)
    if fy:
        v2 = cell(p + nx)
        if fx:
            v2 = v2 + ((fx * (cell(p + nx + 1) - v2)) >> 16)
        v = v + ((fy * (v2 - v)) >> 16)
    return max(v, FST_ONE)


def interp8_s8(values, key_y: int, key_x: int, nx: int = DZW_N) -> int:
    """`interp_2d_s8` (0x40C3B4) over an nx-wide s8 map: `values[iy*nx + ix]`."""
    fx, ix = key_x & 0xFFFF, key_x >> 16
    fy, iy = key_y & 0xFFFF, key_y >> 16
    p = ix + nx * iy
    v = _s8(values[p])
    if fx:
        v = v + ((fx * (_s8(values[p + 1]) - v)) >> 16)
    if fy:
        v2 = _s8(values[p + nx])
        if fx:
            v2 = v2 + ((fx * (_s8(values[p + nx + 1]) - v2)) >> 16)
        v = v + ((fy * (v2 - v)) >> 16)
    return _s8(v)


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
        st.fst_q10 = FST_ONE            # E2: the same, for the start
        st.status = 0xFF
        st.cal_ok = 1 if c.valid else 0
        st.cal_mode = c.mode if c.valid else 0
        if st.cal_ok and c.mode == 1:
            self.can_init_calls += 1    # can_init_mb(15), idempotent (can.md §7)
        self.diag_publish()
        self.seal()

    def state_valid(self) -> bool:
        st = self.state
        return (st.magic == self.MAGIC and st.length == self.LENGTH
                and st.csum == self.checksum())

    def checksum(self) -> int:
        """Bit-complement of the 16-bit sum of the two checksummed ranges.

        +0x08..+0x2B (D1's core) and +0x40..+0x43 (E2's), in that order, which
        is exactly the order `ff_core_csum()` sums them in.  The annex between
        them has other writers and stays outside.
        """
        return (~sum(self.core_bytes() + self.core2_bytes())) & 0xFFFF

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
        out += bytes((st.frame_bad, st.dzw_e & 0xFF))
        out += st.fzw_q8.to_bytes(2, "big")
        assert len(out) == CORE_LEN
        return bytes(out)

    def core2_bytes(self) -> bytes:
        """The second checksummed range, +0x40..+0x43 (brief E2)."""
        st = self.state
        out = (st.fst_q10.to_bytes(2, "big")
               + bytes((st.zwst_add & 0xFF, st.st_reserved & 0xFF)))
        assert len(out) == CORE2_LEN
        return out

    def seal(self) -> None:
        self.state.csum = self.checksum()

    def block_bytes(self) -> bytes:
        """The 0x2C bytes of header + first core, as they sit at PATCH_RAM.

        This is what `tests/test_ff_fuel_patch.py` compares the emulated RAM
        against, tick by tick.  The annex (+0x2C..+0x3F) is deliberately left
        out: the segment task and brief D2 write it, not the periodic tick.
        E2's second core (+0x40..+0x43) is not contiguous with this, so it has
        its own `core2_bytes()`; `full_bytes()` shows all 0x44.
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

    # -- E1 (#34): the ignition blend -------------------------------------
    def fzw_of(self, e_filt: int) -> int:
        """`f_zw(E)` from `ff_fzw_curve`, 1/256 -- the same shape as `f_of`."""
        curve = self.cal.fzw_curve
        if e_filt >= E_FILT_MAX:
            f = curve[CURVE_N - 1]
        else:
            i = e_filt // CURVE_STEP
            fr = e_filt % CURVE_STEP
            a, b = curve[i], curve[i + 1]
            f = a + _tdiv((b - a) * fr, CURVE_STEP)
        return min(max(f, 0), FZW_MAX)

    def dzw_of(self, nmot_w: int, rl_w: int) -> int:
        """`ff_dzw_map(nmot_w, rl_w)` in s8 counts of 0.75 degCA.

        Bit for bit what `ff_axis8` + `ff_interp8` do in `src/ff_ign.c`, which
        is in turn the arithmetic of the stock `axis_search_u16_hint`
        (0x40C9CC) and `interp_2d_s8` (0x40C3B4) that `emu/zw_model.py` models
        and `tests/test_zw_model.py` proves against the real functions.
        `interp_2d_s8` indexes `val[iy * nx + ix]`, so y is nmot and x is rl.
        """
        ky = axis_key8(self.cal.dzw_nmot_axis, nmot_w)
        kx = axis_key8(self.cal.dzw_rl_axis, rl_w)
        return interp8_s8(self.cal.dzw_map, ky, kx)

    def zw_update(self, nmot_w: int, rl_w: int) -> None:
        """Recompute `dzw_e` and `fzw_q8`; called at the end of every activation.

        `dzw_e` is **0** whenever the feature is disabled, the calibration is
        not usable, the mode is not one that has a believable ethanol estimate,
        or the estimate is E0.  There is no hold and no ramp on the way out:
        the activation on which the mode leaves OK/HOLD/OVERRIDE is already the
        activation on which the offset is 0 (the #37 ignition rule).
        """
        st, c = self.state, self.cal
        if (not c.zw_enable or not st.cal_ok or st.e_filt == 0
                or st.mode not in (MODE_OK, MODE_HOLD, MODE_OVERRIDE)):
            st.fzw_q8 = 0
            st.dzw_e = 0
            return
        st.fzw_q8 = self.fzw_of(st.e_filt)
        p = st.fzw_q8 * self.dzw_of(nmot_w, rl_w)
        q = (p + FZW_ONE // 2) // FZW_ONE if p >= 0 else -((-p + FZW_ONE // 2)
                                                           // FZW_ONE)
        ceil_ = c.dzw_ceiling()
        st.dzw_e = min(max(q, -ceil_), ceil_)

    def zwgru_offset(self) -> int:
        """What the 0x41D40C trampoline adds, given the block as it stands."""
        st = self.state
        if st.magic != self.MAGIC:
            return 0                               # no valid state -> stock
        return st.dzw_e

    # -- E2 (#35): the start enrichment ------------------------------------
    def fst_of(self, e_filt: int, tmst: int) -> int:
        """`f_st(E, tmst)` from `ff_fst_map`, Q10: `src/ff_start.c`'s producer.

        `interp6_u16` indexes `val[iy * 6 + ix]`, so y is the ETHANOL row and x
        the `tmst` column -- the map reads in the XDF the way the calibration
        is thought about, one row per fuel.
        """
        ky = axis_key6(self.cal.fst_e_axis, e_filt, 16)
        kx = axis_key6(self.cal.fst_tmst_axis, tmst, 1)
        return interp6_u16(self.cal.fst_map, ky, kx)

    def fzwst_of(self, e_filt: int) -> int:
        """The start-advance curve over the SAME ethanol axis as the map rows."""
        key = axis_key6(self.cal.fst_e_axis, e_filt, 16)
        f, i = key & 0xFFFF, key >> 16
        a = _s8(self.cal.fzwst_curve[i] & 0xFF)
        if f == 0:
            return a
        b = _s8(self.cal.fzwst_curve[i + 1] & 0xFF)
        return a + ((f * (b - a)) >> 16)

    def start_update(self, tmst: int) -> None:
        """Recompute `fst_q10` and `zwst_add`; the end of every activation.

        The two halves of the #37 asymmetry, side by side:

        * the FUEL factor runs wherever `ff_tick()` computes `f_q10` from
          `e_filt` -- OK, HOLD, FAULT and OVERRIDE -- so it inherits the FAULT
          hold and the decay towards `e_key` without a rule of its own, and it
          is neutral (1024) exactly where `f_q10` is forced to 1024;
        * the start ADVANCE is 0 on the very activation the mode leaves
          OK/HOLD/OVERRIDE, with no hold and no ramp, and additionally 0 at and
          above `ff_zwst_tmax`.  The knock retard is bypassed during the start,
          so nothing downstream would take a stale advance back.
        """
        st, c = self.state, self.cal
        if (not c.st_enable or not st.cal_ok or st.e_filt == 0
                or st.mode in (MODE_INIT, MODE_OFF)):
            st.fst_q10 = FST_ONE
        else:
            st.fst_q10 = min(self.fst_of(st.e_filt, tmst), c.fst_ceiling())

        if (not c.zwst_enable or not st.cal_ok or st.e_filt == 0
                or st.mode not in (MODE_OK, MODE_HOLD, MODE_OVERRIDE)
                or tmst >= c.zwst_tmax):
            st.zwst_add = 0
            return
        st.zwst_add = min(max(self.fzwst_of(st.e_filt), 0), c.zwst_ceiling())

    def ksta_scale(self, ksta: int) -> int:
        """What either S1 stub publishes at 0x80302C, given the block as it is.

        `ksta` is the halfword the stock `sth` would have stored; the stub takes
        exactly those 16 bits (`clrlwi`) and saturates the product at 0xFFFF,
        which is the fixed point `re/findings/start.md` section 3.3 fixes for
        the cell.
        """
        st = self.state
        if st.magic != self.MAGIC:
            return ksta & 0xFFFF                   # no valid state -> stock
        if st.fst_q10 <= FST_ONE:
            return ksta & 0xFFFF                   # neutral -> bit-identical
        return min(((ksta & 0xFFFF) * st.fst_q10) >> 10, 0xFFFF)

    def zwstt_offset(self, zwstt: int) -> int:
        """What the 0x431384 stub stores, given the stock s8 it displaced.

        The stub range-checks the byte UNSIGNED against ZWST_HARD_MAX, which
        rejects every negative value too, and then re-does the s8 clamp the
        stock code applies *before* the store.
        """
        st = self.state
        stock = _s8(zwstt & 0xFF)
        if st.magic != self.MAGIC:
            return stock
        add = st.zwst_add & 0xFF
        if add > ZWST_HARD_MAX:                    # incl. every negative byte
            return stock
        return min(stock + add, 127)

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
    def _finish(self, nmot_w: int, rl_w: int, tmst: int = 0) -> None:
        """`ff_finish()`: the tail of every activation, on every path."""
        self.zw_update(nmot_w, rl_w)
        self.start_update(tmst)
        self.diag_publish()
        self.seal()

    def tick(self, rx: bytes | None = None, *, src: int = SRC_B,
             can_id_echo: int | None = None,
             nmot_w: int = 0, rl_w: int = 0, tmst: int = 0) -> State:
        """One periodic-hook activation.

        `rx` is the frame `can_rx_poll(15)` would report as fresh (DLC 8), or
        None for "nothing new since the last call".  `nmot_w` (0x7FEE74) and
        `rl_w` (0x7FEFB2) are the two RAM words the ignition blend reads and
        `tmst` (0x8021F6) is the u8 count the start enrichment reads; all three
        default to 0, which is both what an un-written emulator RAM holds and,
        for the two maps, the bottom-left cell.
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
                self._finish(nmot_w, rl_w, tmst)
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
            self._finish(nmot_w, rl_w, tmst)
            return st

        if st.cal_mode == 2:                       # bench override
            st.mode = MODE_OVERRIDE
            st.age_ticks = 0
            self._move(c.override() * 16, filtered=True)
            st.f_q10 = self.f_of(st.e_filt)
            self._finish(nmot_w, rl_w, tmst)
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
        self._finish(nmot_w, rl_w, tmst)
        return st

    # -- the measuring block (brief D2, issue #39) ------------------------
    def diag_publish(self) -> None:
        """`ff_diag_publish()`: the three annex words the handlers read.

        Annex, so it is outside the checksum and `block_bytes()` does not show
        it; `full_bytes()` does.
        """
        st = self.state
        st.diag_e_pct = min((st.e_filt + 8) // 16, 100)
        st.diag_f_pct = (st.f_q10 * 100) >> 10
        st.diag_t_degc = st.t_fuel

    def triples(self) -> list[tuple[int, int, int]]:
        """The four `(formula, A, B)` triples of measuring block 111.

        Exactly what `ff_diag_e_pct`, `ff_diag_f_pct`, `ff_diag_t_degc` and
        `ff_diag_mode` emit (patches/ff_fuel/src/ff_diag.c); the handlers check
        the block header only, so this does too.
        """
        st = self.state
        if st.magic != self.MAGIC or st.length != STATE_LEN:
            return [(0x25, 0, 0)] * 4
        clamp = lambda v: min(max(v, 0), 0xFF)              # noqa: E731
        t = ((0x25, 0, 0) if st.status == 0xFF
             else (0x05, 10, clamp(st.diag_t_degc + 100 - 40)))
        return [(0x21, 100, clamp(st.diag_e_pct)),
                (0x21, 100, clamp(st.diag_f_pct)),
                t,
                (0x36, clamp(st.persist_state), clamp(st.mode))]

    def triples_zw(self, dwkrz=(0,) * 6, zw_latch: int = 0) -> list[tuple]:
        """The four `(formula, A, B)` triples of measuring block 108 (brief E1).

        `dwkrz` is the six-byte stock array at 0x7FCE57 and `zw_latch` the
        stock byte 0x7FD31B: fields 3 and 4 read them live in the handler, so
        they are arguments here rather than state.  Those two fields do NOT
        depend on our block header -- they are stock values, and a tester
        chasing knock must still see them when our block is invalid.

        Fields 2 and 3 use **formula 0x22 with A = 0x4B**, which is byte for
        byte what the six stock knock-retard handlers at 0x039CD0-0x039D48 emit
        (`li r3,0x22; lbz r5,dwkrz; li r4,0x4B; addi r5,r5,0x80`): the reading
        is `0.01 * 75 * (B - 128)` = 0.75 degCA per count, the ignition
        resolution of `re/findings/ignition.md` section 6.
        """
        st = self.state
        clamp = lambda v: min(max(v, 0), 0xFF)              # noqa: E731
        ours = st.magic == self.MAGIC and st.length == STATE_LEN
        fzw_pct = min((st.fzw_q8 * 100 + 128) >> 8, 100)
        return [
            (0x21, 100, fzw_pct) if ours else (0x25, 0, 0),
            (0x22, 0x4B, clamp(st.dzw_e + 128)) if ours else (0x25, 0, 0),
            (0x22, 0x4B, clamp(max(_s8(b) for b in dwkrz) + 128)),
            (0x36, 0, zw_latch & 3),
        ]

    def triples_st(self, tmst: int = 0, ksta: int = 0x400) -> list[tuple]:
        """The four `(formula, A, B)` triples of measuring block 69 (brief E2).

        `tmst` is the stock byte at 0x8021F6 and `ksta` the stock word at
        0x80302C: fields 3 and 4 read them live in the handler, so they are
        arguments here rather than state, and neither depends on our block
        header -- a tester watching a cold start must still see them.

        Field 1 is a plain percent (formula 0x21, A = 100); FST_HARD_MAX is
        chosen so it always fits the byte.  Field 2 is the ignition formula
        E1 settled on, 0x22 with A = 0x4B = 0.75 degCA per count.  Field 3 is
        formula 0x05 with A = 10, i.e. whole degrees C, converted here from
        the 0.75 degC / -48 degC count and rounded to nearest.  Field 4 is a
        RAW COUNT (formula 0x36): `ksta_adapted` runs to 22.8x = 2280 % in the
        stock dataset alone, so a percent byte would saturate -- see
        `src/ff_start.c`.
        """
        st = self.state
        clamp = lambda v: min(max(v, 0), 0xFF)              # noqa: E731
        ours = st.magic == self.MAGIC and st.length == STATE_LEN
        pct = (st.fst_q10 * 100 + FST_ONE // 2) // FST_ONE
        degc = ((tmst & 0xFF) * 3 + 2) // 4 - 48
        return [
            (0x21, 100, clamp(pct)) if ours else (0x25, 0, 0),
            (0x22, 0x4B, clamp(st.zwst_add + 128)) if ours else (0x25, 0, 0),
            (0x05, 10, clamp(degc + 100)),
            (0x36, (ksta >> 8) & 0xFF, ksta & 0xFF),
        ]

    def full_bytes(self) -> bytes:
        """All 68 bytes at PATCH_RAM: header, core, annex and E2's core 2."""
        st = self.state
        return self.block_bytes() + struct.pack(
            ">IHBBHHHHHH", st.rk_calls, st.e_persist, st.persist_state,
            st.persist_err, st.diag_e_pct, st.diag_f_pct, st.diag_t_degc,
            st.persist_wait, st.persist_writes, st.persist_fails
        ) + self.core2_bytes()

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
