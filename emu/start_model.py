"""Python model of the MED9.1.1 start path (brief B8, issue #16).

Re-implements, in the ECU's own fixed point, the two computations that decide
what a cold start looks like:

* **`ksta`** -- the cranking fuel factor of `%ESSTT` (`FUN_0041a268`,
  0x41A268): ``KFKSTT(tmst, prist) * KFWKSTT(tmst, anztist) * KFWKSTN(tmst,
  nmot) * <trim> * <weighting>``, then the start-quantity adaptation `kstaa`.
  The result, RAM 0x80302C, is u16 with **1024 = 1.0** and is the factor
  `gk_rk` (0x41AA48) multiplies `rl` by while the start has not finished.
  This is insertion point **S1** of `re/findings/start.md` section 3.3.
* **`zwstt`** -- the start ignition angle of `FUN_00431294` (0x431294):
  ``KFZWSTT(zdgz, tmst) + KFZWSTN(nmot, tmst) + KLZWSTT(t3e5)``, s8 at
  0.75 deg CA per LSB, RAM 0x802096.  While the start has not finished this
  byte *replaces* the whole per-bank ignition angle in `zwbas_per_bank`
  (0x41D10C), knock retard included.  Insertion point **Z1**.

Both take an optional ``ethanol_factor`` / ``ethanol_offset`` argument that
models exactly what the flex-fuel patch would add at S1 / Z1, so the patch
can be calibrated before it is written.

Everything is integer arithmetic on the raw calibration bytes of
`data/passat_azx_ori.bin`; physical units are applied only by the ``*_deg``
and ``*_c`` helpers.

`tests/test_start_model.py` checks this module against the real functions
under the Unicorn harness of `emu/`.

Usage::

    from emu.start_model import Start
    st = Start("data/passat_azx_ori.bin")
    st.ksta(tmst=64, prist=4000, anztist=0)          # raw, 1024 = 1.0
    st.ksta_factor(tmot_c=-20.0, anztist=0)          # float, 1.0 = no enrichment
    st.zwstt(zdgz=0, tmst=64)                        # raw s8 counts
    st.zwstt_deg(zdgz=0, tmot_c=-20.0)               # deg CA, + = before TDC
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import med9lib  # noqa: E402

# --- calibration addresses, all VERIFIED-STATIC (re/findings/start.md) -----
KFKSTT_NY = 0x5C6E12         # u8 = 12   (tmst breakpoints)
KFKSTT_NX = 0x5C6E13         # u8 = 2    (prist breakpoints)
KFKSTT_YAXIS = 0x5C6E14      # 12 x u8, tmst
KFKSTT_XAXIS = 0x5C6E20      # 2  x u16, prist
KFKSTT = 0x5C6E24            # 12 x 2 u16, 1024 = 1.0

KFWKSTT_STRUCT = 0x5C6C60    # { u8 ny=12; u8 nx=14; u8 y[12]; u8 x[14]; u8 v[168] }
KFWKSTN_STRUCT = 0x5C6C46    # { u8 ny=4;  u8 nx=4;  u8 y[4];  u8 x[4];  u8 v[16] }
WKSTA_EEC_STRUCT = 0x5D3739  # { u8 ny=6;  u8 nx=4;  ... } weighting over 0x800EEC

KSTA_CODEWORD = 0x5C6F0C     # bit 3 clear -> ksta is floored at 1.0 before kstaa

# start ignition
KFZWSTT_NY = 0x5C7B52        # u8 = 8  (zdgz breakpoints)
KFZWSTT_NX = 0x5C7B53        # u8 = 8  (tmst breakpoints)
KFZWSTT_YAXIS = 0x5C7B54
KFZWSTT_XAXIS = 0x5C7B5C
KFZWSTT = 0x5C7B64           # 8 x 8 s8, 0.75 deg CA per LSB

KFZWSTN_NY = 0x5C7B18        # u8 = 3  (nmot)
KFZWSTN_NX = 0x5C7B19        # u8 = 6  (tmst)
KFZWSTN_YAXIS = 0x5C7B1A
KFZWSTN_XAXIS = 0x5C7B1D
KFZWSTN = 0x5C7B23           # 3 x 6 s8

KFZWSTALT_NY = 0x5C7B35      # the 0x7FCE0C bit-1 alternative
KFZWSTALT_NX = 0x5C7B36
KFZWSTALT_YAXIS = 0x5C7B37
KFZWSTALT_XAXIS = 0x5C7B3A
KFZWSTALT = 0x5C7B40

KLZWSTT_N = 0x5C7BA4         # u8 = 6
KLZWSTT_AXIS = 0x5C7BA5
KLZWSTT = 0x5C7BAB           # 6 x s8, all zero in this dataset

# --- fixed point ----------------------------------------------------------
KSTA_ONE = 0x400             # 1024 = 1.0 for 0x803028 / 0x80302C
Q7_ONE = 128                 # KFWKSTT, kstaa, the 0x5D3739 weighting
Q8_ONE = 256                 # KFWKSTN
ZW_LSB_DEG = 0.75            # deg CA per s8 count (B7, re/findings/ignition.md)
TMOT_LSB_C = 0.75            # deg C per u8 count
TMOT_OFFSET_C = -48.0
NMOT8_LSB_RPM = 40.0


def _s8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


def sat_s8(v: int) -> int:
    return 0x7F if v > 0x7F else (-0x80 if v < -0x80 else v)


def sat_u16(v: int) -> int:
    """The firmware's `cmplwi 0xfffe / bgt` clamp: >0xFFFE becomes 0xFFFF."""
    return 0xFFFF if v > 0xFFFE else v


def mul_q15(a: int, b: int) -> int:
    """`FUN_00410060` (0x410060): min((a*b) >> 15, 0xFFFF), truncated to u16."""
    return sat_u16((a * b) >> 15) & 0xFFFF


def axis_search(axis: list[int], value: int, hint_index: int = 0) -> int:
    """The Bosch breakpoint search -> (index << 16) | frac.

    Identical arithmetic for the u8 and u16 axis variants (0x40C578 /
    0x40C6D8 and their `_hint` twins); the hint only shortens the walk.
    """
    n = len(axis)
    frac = 0
    idx = 0
    if axis[0] < value:
        idx = n - 1
        if value < axis[n - 1]:
            idx = min(max(hint_index, 0), n - 2)
            if axis[idx] < value:
                while axis[idx + 1] <= value:
                    idx += 1
            elif value < axis[idx]:
                while value < axis[idx]:
                    idx -= 1
            if axis[idx] != value:
                frac = ((value - axis[idx]) * 0x10000) // (axis[idx + 1] - axis[idx])
    return (idx << 16) | frac


def interp_2d(values: list[int], nx: int, key_y: int, key_x: int, *, signed: bool) -> int:
    """`interp_2d_u8` (0x40C334) / `interp_2d_s8` (0x40C3B4) / the u16 twins.

    `values[iy * nx + ix]`, bilinear with 16-bit fractions.
    """
    fx = key_x & 0xFFFF
    ix = key_x >> 16
    fy = key_y & 0xFFFF
    iy = key_y >> 16
    p = ix + nx * iy
    v = values[p]
    if fx:
        v = v + ((fx * (values[p + 1] - v)) >> 16)
    if fy:
        v2 = values[p + nx]
        if fx:
            v2 = v2 + ((fx * (values[p + nx + 1] - v2)) >> 16)
        v = v + ((fy * (v2 - v)) >> 16)
    return _s8(v) if signed else v


class Start:
    """The cranking fuel factor and the start ignition angle, from the dump."""

    def __init__(self, dump_path: str | Path = "data/passat_azx_ori.bin"):
        self.data = med9lib.load_dump(str(dump_path))

    # --- raw calibration access -------------------------------------------
    def _at(self, cpu_addr: int) -> int:
        return med9lib.cpu_to_file(cpu_addr)

    def u8(self, addr: int) -> int:
        return self.data[self._at(addr)]

    def s8(self, addr: int) -> int:
        return _s8(self.u8(addr))

    def u16(self, addr: int) -> int:
        return struct.unpack_from(">H", self.data, self._at(addr))[0]

    def u8s(self, addr: int, n: int) -> list[int]:
        off = self._at(addr)
        return list(self.data[off:off + n])

    def s8s(self, addr: int, n: int) -> list[int]:
        return [_s8(b) for b in self.u8s(addr, n)]

    def u16s(self, addr: int, n: int) -> list[int]:
        off = self._at(addr)
        return list(struct.unpack_from(">%dH" % n, self.data, off))

    # --- the two lookup shapes the start modules use ----------------------
    def lookup_2d_u8(self, struct_addr: int, vy: int, vx: int) -> int:
        """`lookup_2d_u8` (0x40D18C): self-describing { ny; nx; y[]; x[]; v[] }."""
        ny = self.u8(struct_addr)
        nx = self.u8(struct_addr + 1)
        yax = self.u8s(struct_addr + 2, ny)
        xax = self.u8s(struct_addr + 2 + ny, nx)
        val = self.u8s(struct_addr + 2 + ny + nx, ny * nx)
        return interp_2d(val, nx, axis_search(yax, vy), axis_search(xax, vx), signed=False)

    def lookup_2d_g_u8_u16_u16(self, ny: int, yaxis: int, nx: int, xaxis: int,
                               val: int, vy: int, vx: int) -> int:
        """`lookup_2d_g_u8_u16_u16` (0x40E72C): u8 row axis, u16 column axis, u16 cells."""
        yax = self.u8s(yaxis, ny)
        xax = self.u16s(xaxis, nx)
        values = self.u16s(val, ny * nx)
        return interp_2d(values, nx, axis_search(yax, vy), axis_search(xax, vx), signed=False)

    def lookup_2d_g_u8_u8_s8(self, ny: int, yaxis: int, nx: int, xaxis: int,
                             val: int, vy: int, vx: int) -> int:
        """`lookup_2d_g_u8_u8_s8` (0x40DC7C): u8 axes, s8 cells."""
        yax = self.u8s(yaxis, ny)
        xax = self.u8s(xaxis, nx)
        values = self.s8s(val, ny * nx)
        return interp_2d(values, nx, axis_search(yax, vy), axis_search(xax, vx), signed=True)

    def lookup_1d_g_u8_s8(self, n: int, axis: int, val: int, v: int) -> int:
        """`lookup_1d_g_u8_s8` (0x40F454): u8 axis, s8 cells."""
        ax = self.u8s(axis, n)
        values = self.s8s(val, n)
        key = axis_search(ax, v)
        i = key >> 16
        f = key & 0xFFFF
        out = values[i]
        if f:
            out = out + ((f * (values[i + 1] - out)) >> 16)
        return _s8(out)

    # --- %ESSTT -----------------------------------------------------------
    def kfkstt(self, tmst: int, prist: int) -> int:
        return self.lookup_2d_g_u8_u16_u16(self.u8(KFKSTT_NY), KFKSTT_YAXIS,
                                           self.u8(KFKSTT_NX), KFKSTT_XAXIS,
                                           KFKSTT, tmst, prist)

    def kfwkstt(self, tmst: int, anztist: int) -> int:
        return self.lookup_2d_u8(KFWKSTT_STRUCT, tmst, anztist)

    def kfwkstn(self, tmst: int, nmot8: int) -> int:
        return self.lookup_2d_u8(KFWKSTN_STRUCT, tmst, nmot8)

    def ksta(self, tmst: int, prist: int = 4000, anztist: int = 0, nmot8: int = 10,
             trim_7fd067: int = Q7_ONE, w_800eec: int = 61) -> int:
        """`FUN_0041a268` -> RAM 0x803028, u16, 1024 = 1.0.

        `trim_7fd067` (0x7FD067) and `w_800eec` (0x800EEC) are the two RAM
        inputs that are not calibration; both are 1.0-neutral at their
        defaults.
        """
        v = sat_u16((self.kfwkstt(tmst, anztist) * self.kfkstt(tmst, prist)) >> 7)
        v = sat_u16((self.kfwkstn(tmst, nmot8) * (v & 0xFFFF)) >> 8)
        v = mul_q15(v & 0xFFFF, (trim_7fd067 & 0xFF) << 8)
        w = self.lookup_2d_u8(WKSTA_EEC_STRUCT, tmst, w_800eec)
        return mul_q15(v, (w & 0xFF) << 8)

    def ksta_adapted(self, tmst: int, kstaa: int = Q7_ONE, ethanol_factor: int = 0x400,
                     **kw) -> int:
        """`FUN_0041a268` tail -> RAM 0x80302C, u16, 1024 = 1.0.

        `ethanol_factor` is the Q10 multiplier a flex-fuel patch would apply
        at insertion point S1 (the `sth` at 0x41A680 / 0x41A808); 0x400 = 1.0
        leaves the stock value bit-identical.
        """
        ksta = self.ksta(tmst, **kw)
        if self.u8(KSTA_CODEWORD) & 8 == 0:
            ksta = max(ksta, KSTA_ONE)
        out = mul_q15((kstaa & 0xFF) << 8, ksta)
        if ethanol_factor != KSTA_ONE:
            out = sat_u16((out * ethanol_factor) >> 10) & 0xFFFF
        return out

    # --- start ignition ---------------------------------------------------
    def zwstt(self, zdgz: int, tmst: int, nmot8: int = 10, t3e5: int = 128,
              alt_map: bool = False, ethanol_offset: int = 0) -> int:
        """`FUN_00431294` -> RAM 0x802096, raw s8 counts (0.75 deg CA per LSB).

        `alt_map` selects the `0x7FCE0C & 2` branch.  `ethanol_offset` is what
        a patch would add at insertion point Z1 (the `stb` at 0x431384).
        """
        if not alt_map:
            a = self.lookup_2d_g_u8_u8_s8(self.u8(KFZWSTT_NY), KFZWSTT_YAXIS,
                                          self.u8(KFZWSTT_NX), KFZWSTT_XAXIS,
                                          KFZWSTT, zdgz, tmst)
            b = self.lookup_2d_g_u8_u8_s8(self.u8(KFZWSTN_NY), KFZWSTN_YAXIS,
                                          self.u8(KFZWSTN_NX), KFZWSTN_XAXIS,
                                          KFZWSTN, nmot8, tmst)
            v = sat_s8(a + b)
        else:
            v = self.lookup_2d_g_u8_u8_s8(self.u8(KFZWSTALT_NY), KFZWSTALT_YAXIS,
                                          self.u8(KFZWSTALT_NX), KFZWSTALT_XAXIS,
                                          KFZWSTALT, nmot8, tmst)
        c = self.lookup_1d_g_u8_s8(self.u8(KLZWSTT_N), KLZWSTT_AXIS, KLZWSTT, t3e5)
        return sat_s8(sat_s8(c + _s8(v)) + ethanol_offset)

    # --- physical-unit helpers -------------------------------------------
    @staticmethod
    def tmot_raw(tmot_c: float) -> int:
        return max(0, min(255, int(round((tmot_c - TMOT_OFFSET_C) / TMOT_LSB_C))))

    @staticmethod
    def tmot_c(raw: int) -> float:
        return raw * TMOT_LSB_C + TMOT_OFFSET_C

    def ksta_factor(self, tmot_c: float, anztist: int = 0, prist: int = 4000,
                    nmot_rpm: float = 400.0, **kw) -> float:
        """The cranking enrichment factor as a plain number (1.0 = none)."""
        return self.ksta_adapted(self.tmot_raw(tmot_c), anztist=anztist, prist=prist,
                                 nmot8=int(round(nmot_rpm / NMOT8_LSB_RPM)),
                                 **kw) / KSTA_ONE

    def zwstt_deg(self, zdgz: int, tmot_c: float, nmot_rpm: float = 400.0, **kw) -> float:
        """The start ignition angle in deg CA (positive = before TDC)."""
        return self.zwstt(zdgz, self.tmot_raw(tmot_c),
                          nmot8=int(round(nmot_rpm / NMOT8_LSB_RPM)), **kw) * ZW_LSB_DEG


def _main() -> int:
    st = Start(sys.argv[1] if len(sys.argv) > 1 else "data/passat_azx_ori.bin")
    print("cranking factor ksta*kstaa (1.0 = no enrichment), nmot 400 rpm, prist 4000")
    print("  tmot C :  " + "".join(f"{c:>8.0f}" for c in (-30, -20, -10, 0, 20, 40, 60, 90)))
    for anz in (0, 4, 8, 12, 18, 24):
        row = "".join(f"{st.ksta_factor(c, anztist=anz):>8.2f}"
                      for c in (-30, -20, -10, 0, 20, 40, 60, 90))
        print(f"  inj {anz:>3}: {row}")
    print()
    print("start ignition zwstt (deg CA, + = before TDC)")
    print("  tmot C :  " + "".join(f"{c:>8.0f}" for c in (-30, -20, -10, 0, 20, 40, 60, 90)))
    for z in (0, 3, 5, 7, 9, 12):
        row = "".join(f"{st.zwstt_deg(z, c):>8.2f}"
                      for c in (-30, -20, -10, 0, 20, 40, 60, 90))
        print(f"  zdgz {z:>2}: {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
