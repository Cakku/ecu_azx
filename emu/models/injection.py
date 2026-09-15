#!/usr/bin/env python3
"""Bit-exact Python model of the MED9.1.1 fuel-mass -> injection-time chain.

Brief B6, issue #14.  Models three ECU functions of
`data/passat_azx_ori.bin` (03H906032 / 1037382557):

| model                | ECU function | what it is                                |
|----------------------|--------------|-------------------------------------------|
| `fkkvs_func`         | 0x000AC4B8   | `frt`, `fcorr` (FKKVS) and the dead time  |
| `rk2ti`              | 0x000AC370   | `ti` from `rk_i` and those three          |
| `rkti_dp_angle`      | 0x000AC42C   | `dp` from `prist` and the back-pressure   |

plus the three Bosch interpolation helpers they use
(`axis_search_u16_hint` 0x40C9CC, `lookup_1d_u16` 0x40EFAC,
`lookup_2d_u16` 0x40D2F8, whose conventions B5 documented in
`re/findings/calibration_maps.md`).

Every calibration address is read out of the dump, never hard-coded as data,
so the model follows a re-calibrated binary.  Full derivation, evidence and
the meaning of each constant: `re/findings/injection.md`.

Usage::

    from emu.models.injection import InjectionModel
    m = InjectionModel("data/passat_azx_ori.bin")
    m.rk2ti(0, rk_i=2000, frt=1356, tv=400, fcorr=0x8000)        # -> ti
    m.fkkvs_func(rk_i=2000, dp=10000, nmot=6000, use_fkkvs=1)    # -> (frt, fcorr, tv)
    m.injection_time(rk_i=2000, dp=10000, nmot=6000)             # the whole chain

Self-check against the real code (needs unicorn)::

    python3 -m emu.models.injection --verify
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "tools"))
import med9lib  # noqa: E402

# --- addresses, all VERIFIED-STATIC (re/findings/injection.md section 1) -----

RK2TI = 0x0AC370            # u16 rk2ti(inhibit, rk_i, frt, tv, fcorr)
FKKVS_FUNC = 0x0AC4B8       # void fkkvs_func(rk_i, dp, *tv, *frt, use_fkkvs, *fcorr)
RKTI_DP_ANGLE = 0x0AC42C    # u16 rkti_dp_angle(prist, wkr, pbr_scale)

KRKATE = 0x5D3DBC           # u16 injector constant (3858 in this dataset)
DP_AXIS = 0x5D3DBE          # {u16 n; u16 axis[n]} shared by KLTIKRPR and the dead time
KLTIKRPR = 0x5C72F8         # u16 val[n]   flow correction over dp
TV_DP = 0x5C7310            # s16 val[n]   injector dead time over dp
FKKVS = 0x5C71F8            # 8x8 u16 map, (ti_eff, nmot), Q15
KLHDEV = 0x5C729C           # 10-point u16 curve over ti_raw, Q15
TIMINP = 0x5C7328           # u16 minimum injection time (900)
KLPBR = 0x5C72C6            # 12-point curve, cylinder back pressure over angle

RAM_TI_RAW = 0x8030E0       # rk2ti writes its unclamped ti_raw here
RAM_TI_EFF = 0x8030E2       # fkkvs_func writes the FKKVS y input here
RAM_DP = 0x8030CE           # fkkvs_func keeps its dp argument here
RAM_AXIS_KEY = 0x7FD5B8     # the shared dp axis search key (hint for the next call)
RAM_NMOT = 0x7FEE74         # u16, 1 LSB = 0.25 min^-1
RAM_RK = 0x803038           # u16 relative fuel mass, the segment value
RAM_TI_SUM = 0x8030C4       # u32, the VCDS group 002.3 injection time


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _asr(v: int, n: int) -> int:
    """Arithmetic shift right of a 32-bit value, as `srawi` does it."""
    return _s32(v) >> n


class InjectionModel:
    """The rk -> ti chain of one MED9 image."""

    def __init__(self, image: str = "data/passat_azx_ori.bin"):
        self.data = med9lib.load_dump(image)

    # -- raw reads ---------------------------------------------------------
    def u16(self, cpu: int) -> int:
        off = med9lib.cpu_to_file(cpu)
        return (self.data[off] << 8) | self.data[off + 1]

    def s16(self, cpu: int) -> int:
        return _s16(self.u16(cpu))

    # -- the Bosch interpolation helpers -----------------------------------
    def axis_search_u16(self, axis_struct: int, x: int) -> int:
        """`axis_search_u16[_hint]` 0x40C6D8 / 0x40C9CC.

        Returns `(index << 16) | frac`; the hint only changes how the index is
        found, never the result, so one model covers both.
        """
        n = self.u16(axis_struct)
        a0 = self.u16(axis_struct + 2)
        if a0 >= x:
            return 0
        last = self.u16(axis_struct + 2 * n)
        if last <= x:
            return (n - 1) << 16
        i = 0
        while self.u16(axis_struct + 2 + 2 * (i + 1)) <= x:
            i += 1
        lo = self.u16(axis_struct + 2 + 2 * i)
        hi = self.u16(axis_struct + 2 + 2 * (i + 1))
        frac = ((x - lo) << 16) // (hi - lo)
        return (i << 16) | (frac & 0xFFFF)

    def interp_values(self, val_addr: int, key: int, signed: bool = False) -> int:
        """The `v0 + ((frac * (v1 - v0)) >> 16)` step, inlined at 0x0AC50C
        and 0x0AC5A4 and used by every 1-D helper."""
        idx = (key >> 16) & 0xFFFF
        frac = key & 0xFFFF
        rd = self.s16 if signed else self.u16
        v0 = rd(val_addr + 2 * idx)
        v1 = rd(val_addr + 2 * idx + 2)
        return v0 + _asr(frac * (v1 - v0), 16)

    def lookup_1d_u16(self, struct_addr: int, x: int) -> int:
        """`lookup_1d_u16` 0x40EFAC: `{u16 n; u16 axis[n]; u16 val[n]}`."""
        n = self.u16(struct_addr)
        key = self.axis_search_u16(struct_addr, x)
        return self.interp_values(struct_addr + 2 + 2 * n, key)

    def lookup_1d_g(self, n: int, axis: int, val: int, x: int,
                    axis_signed: bool = False, val_signed: bool = False) -> int:
        """The shared-axis 1-D family (`lookup_1d_g_*`), e.g. 0x40F6C4."""
        rd = (lambda a: _s16(self.u16(a))) if axis_signed else self.u16
        if rd(axis) >= x:
            key = 0
        elif rd(axis + 2 * (n - 1)) <= x:
            key = (n - 1) << 16
        else:
            i = 0
            while rd(axis + 2 * (i + 1)) <= x:
                i += 1
            lo, hi = rd(axis + 2 * i), rd(axis + 2 * (i + 1))
            key = (i << 16) | ((((x - lo) << 16) // (hi - lo)) & 0xFFFF)
        return self.interp_values(val, key, signed=val_signed)

    def lookup_2d_u16(self, struct_addr: int, vy: int, vx: int) -> int:
        """`lookup_2d_u16` 0x40D2F8:
        `{u16 ny; u16 nx; u16 yaxis[ny]; u16 xaxis[nx]; u16 val[ny*nx]}`,
        bilinear, `val[iy * nx + ix]` (B5, `re/findings/calibration_maps.md`)."""
        ny = self.u16(struct_addr)
        nx = self.u16(struct_addr + 2)
        yaxis = struct_addr + 4
        xaxis = yaxis + 2 * ny
        val = xaxis + 2 * nx
        ky = self._axis_key(yaxis, ny, vy)
        kx = self._axis_key(xaxis, nx, vx)
        iy, fy = ky >> 16, ky & 0xFFFF
        ix, fx = kx >> 16, kx & 0xFFFF

        def cell(r, c):
            return self.u16(val + 2 * (r * nx + c))

        v00 = cell(iy, ix)
        v01 = cell(iy, ix + 1) if ix + 1 < nx else v00
        row0 = v00 + _asr(fx * (v01 - v00), 16)
        if iy + 1 < ny:
            v10 = cell(iy + 1, ix)
            v11 = cell(iy + 1, ix + 1) if ix + 1 < nx else v10
            row1 = v10 + _asr(fx * (v11 - v10), 16)
        else:
            row1 = row0
        return row0 + _asr(fy * (row1 - row0), 16)

    def _axis_key(self, axis: int, n: int, x: int) -> int:
        if self.u16(axis) >= x:
            return 0
        if self.u16(axis + 2 * (n - 1)) <= x:
            return (n - 1) << 16
        i = 0
        while self.u16(axis + 2 * (i + 1)) <= x:
            i += 1
        lo, hi = self.u16(axis + 2 * i), self.u16(axis + 2 * (i + 1))
        return (i << 16) | ((((x - lo) << 16) // (hi - lo)) & 0xFFFF)

    # -- the three modelled ECU functions ----------------------------------
    def fkkvs_func(self, rk_i: int, dp: int, nmot: int,
                   use_fkkvs: int = 1) -> tuple:
        """`fkkvs_func` 0x000AC4B8 -> `(frt, fcorr, tv)`.

        `frt = min((KRKATE * KLTIKRPR(dp)) >> 14, 0xFFFF)`,
        `fcorr = FKKVS(ti_eff, nmot)` in Q15 (0x8000 when `use_fkkvs == 0`),
        `tv` = the dead-time curve on the *same* dp key.
        """
        key = self.axis_search_u16(DP_AXIS, dp & 0xFFFF)
        kltikrpr = self.interp_values(KLTIKRPR, key)
        frt = (self.u16(KRKATE) * (kltikrpr & 0xFFFF)) >> 14
        frt = min(frt, 0xFFFF)
        if use_fkkvs:
            ti_eff = min(((rk_i & 0xFFFF) * frt) >> 9, 0xFFFF)
            fcorr = self.lookup_2d_u16(FKKVS, ti_eff, nmot & 0xFFFF) & 0xFFFF
        else:
            fcorr = 0x8000
        tv = self.interp_values(TV_DP, key, signed=True) & 0xFFFF
        return frt, fcorr, tv

    def rk2ti(self, inhibit: int, rk_i: int, frt: int, tv: int,
              fcorr: int) -> int:
        """`rk2ti` 0x000AC370 -> `ti`.  The `mullw` at 0x0AC39C is the
        flex-fuel multiplication point (`re/findings/injection.md` section 6)."""
        if inhibit != 0:
            return 0
        prod = ((rk_i & 0xFFFF) * (frt & 0xFFFF)) & 0xFFFFFFFF
        ti_raw = (prod >> 9) & 0x7FFFFF
        clamped = min(ti_raw, 0xFFFF)
        hdev = self.lookup_1d_u16(KLHDEV, clamped) & 0xFFFF
        f = ((hdev * (fcorr & 0xFFFF)) & 0xFFFFFFFF) >> 15
        f &= 0x1FFFF
        if ti_raw > 0xFFFF:
            base, shift = (ti_raw >> 8) & 0xFFFFFF, 7
        else:
            base, shift = ti_raw, 15
        ti = ((base * f) & 0xFFFFFFFF) >> shift
        # `tv` reaches r6 as a full 32-bit word; every caller loads it with
        # `lhz`, so it is the zero-extended u16 of the dead-time curve.
        ti = _sat_add_s32(_s32(ti), _s32(tv))
        if ti <= 0:
            ti = 0
        timinp = self.u16(TIMINP)
        if timinp >= ti:
            ti = timinp
        return ti

    def rkti_dp_angle(self, prist: int, wkr: int, pbr_scale: int) -> int:
        """`rkti_dp_angle` 0x000AC42C: the angle-dependent differential
        pressure, `clamp(prist - ((KLPBR(|wkr| clamped) * pbr_scale) >> 17))`."""
        x = abs(_s32(wkr))
        x = min(x, 0x7FFF)
        n = self.u16(KLPBR)
        pbr = self.lookup_1d_g(n, KLPBR + 2, KLPBR + 2 + 2 * n, x,
                               axis_signed=True)
        v = ((pbr & 0xFFFF) * (pbr_scale & 0xFFFF)) & 0xFFFFFFFF
        v = (v >> 17) & 0x7FFF
        d = (prist & 0xFFFF) - v
        if d < 0:
            return 0
        return min(d, 0xFFFF)

    # -- the whole chain ---------------------------------------------------
    def injection_time(self, rk_i: int, dp: int, nmot: int,
                       use_fkkvs: int = 1, inhibit: int = 0) -> int:
        """`ti` for one injection, as the ECU computes it across the two
        rasters (`fkkvs_func` time-synchronous, `rk2ti` segment-synchronous)."""
        frt, fcorr, tv = self.fkkvs_func(rk_i, dp, nmot, use_fkkvs)
        return self.rk2ti(inhibit, rk_i, frt, tv, fcorr)

    def flexfuel_injection_time(self, rk_i: int, dp: int, nmot: int,
                                factor_q10: int, use_fkkvs: int = 1) -> int:
        """The proposed patch: scale `rk_i` by a Q10 factor before `rk2ti`
        (`factor_q10 = 1024` reproduces `injection_time` exactly)."""
        rk_scaled = min(((rk_i & 0xFFFF) * factor_q10) >> 10, 0xFFFF)
        return self.injection_time(rk_scaled, dp, nmot, use_fkkvs)


def _sat_add_s32(a: int, b: int) -> int:
    """`0x0040FCB8`: signed 32-bit saturating add."""
    r = a + b
    if a < 0:
        if b < r:
            return -0x80000000
    elif r < b:
        return 0x7FFFFFFF
    return _s32(r)


# --- self-check against the real code ---------------------------------------

def verify(image: str = "data/passat_azx_ori.bin", verbose: bool = True) -> int:
    """Run the ECU functions in the Unicorn harness and compare bit-exactly.

    Returns the number of mismatches (0 = pass).
    """
    sys.path.insert(0, os.path.join(_HERE, "..", ".."))
    from emu import Med9Emu

    model = InjectionModel(image)
    emu = Med9Emu(image, r2="app")
    bad = 0
    n = 0

    # --- rk2ti -----------------------------------------------------------
    rks = [0, 1, 17, 200, 340, 500, 1000, 2000, 4096, 9999, 20000,
           25000, 40000, 65535]
    frts = [1, 100, 1356, 2000, 4088, 12000, 28344, 65535]
    tvs = [0, 316, 400, 950, 0xFFFF]
    fcorrs = [0x8000, 0x8148, 0x8666, 0x4000, 0xFFFF]
    for rk in rks:
        for frt in frts:
            for tv in tvs:
                for fc in fcorrs:
                    want = emu.call(RK2TI, args=[0, rk, frt, tv, fc])
                    got = model.rk2ti(0, rk, frt, tv, fc)
                    n += 1
                    if (want.r3 & 0xFFFFFFFF) != (got & 0xFFFFFFFF):
                        bad += 1
                        if verbose and bad < 20:
                            print(f"rk2ti({rk},{frt},{tv},{fc:#x}): "
                                  f"ecu={want.r3:#x} model={got:#x}")
    # inhibit path
    want = emu.call(RK2TI, args=[1, 2000, 1356, 400, 0x8000])
    n += 1
    if want.r3 != model.rk2ti(1, 2000, 1356, 400, 0x8000):
        bad += 1
        if verbose:
            print("rk2ti inhibit path differs")

    # --- fkkvs_func ------------------------------------------------------
    out_tv, out_frt, out_fcorr = 0x800100, 0x800104, 0x800108
    dps = [0, 100, 400, 700, 1200, 2801, 4200, 6119, 10000, 15000,
           20000, 24000, 30000, 65535]
    nmots = [0, 3200, 3201, 5000, 8000, 12345, 24000, 40000]
    for dp in dps:
        for nm in nmots:
            for rk in (0, 500, 2000, 9999, 40000):
                for uf in (0, 1):
                    r = emu.call(FKKVS_FUNC,
                                 args=[rk, dp, out_tv, out_frt, uf, out_fcorr],
                                 mem={RAM_NMOT: nm.to_bytes(2, "big"),
                                      RAM_AXIS_KEY: b"\x00\x00\x00\x00"})
                    e_tv = int.from_bytes(r.snapshot(out_tv, 2), "big")
                    e_frt = int.from_bytes(r.snapshot(out_frt, 2), "big")
                    e_fc = int.from_bytes(r.snapshot(out_fcorr, 2), "big")
                    m_frt, m_fc, m_tv = model.fkkvs_func(rk, dp, nm, uf)
                    n += 1
                    if (e_tv, e_frt, e_fc) != (m_tv & 0xFFFF, m_frt & 0xFFFF,
                                               m_fc & 0xFFFF):
                        bad += 1
                        if verbose and bad < 20:
                            print(f"fkkvs_func(rk={rk},dp={dp},nmot={nm},"
                                  f"uf={uf}): ecu=({e_frt:#x},{e_fc:#x},"
                                  f"{e_tv:#x}) model=({m_frt:#x},{m_fc:#x},"
                                  f"{m_tv:#x})")

    # --- rkti_dp_angle ---------------------------------------------------
    for prist in (0, 1000, 12000, 24000, 65535):
        for wkr in (0, 500, 2048, 4000, 7552, 9000, -3000):
            for scale in (0, 1000, 20000, 65535):
                r = emu.call(RKTI_DP_ANGLE,
                             args=[prist, wkr & 0xFFFFFFFF, scale])
                m = model.rkti_dp_angle(prist, wkr, scale)
                n += 1
                if (r.r3 & 0xFFFF) != (m & 0xFFFF):
                    bad += 1
                    if verbose and bad < 20:
                        print(f"rkti_dp_angle({prist},{wkr},{scale}): "
                              f"ecu={r.r3:#x} model={m:#x}")

    if verbose:
        print(f"{n - bad}/{n} cases match" if bad else f"all {n} cases match")
    return bad


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(1 if verify() else 0)
    m = InjectionModel(sys.argv[1] if len(sys.argv) > 1
                       else "data/passat_azx_ori.bin")
    print(f"KRKATE  = {m.u16(KRKATE)}")
    print(f"TIMINP  = {m.u16(TIMINP)}")
    for dp in (400, 2800, 10000, 24000):
        frt, fcorr, tv = m.fkkvs_func(2000, dp, 6000)
        print(f"dp={dp:6d}  frt={frt:6d}  fcorr={fcorr:#06x}  tv={tv:5d}  "
              f"ti(rk=2000)={m.rk2ti(0, 2000, frt, tv, fcorr)}")
