"""Python model of the MED9.1.1 base-ignition path (brief B7, issue #15).

Re-implements, exactly as the ECU does it in fixed point:

* ``axis_search_u16_hint`` (0x40C9CC) -- the u16 breakpoint search that
  returns ``(index << 16) | frac``;
* ``interp_2d_s8`` (0x40C3B4) -- the bilinear value-array interpolator,
  ``val[iy * nx + ix]``, values s8;
* ``zwgru_kfzw_lookup`` (`FUN_0041d334`, 0x41D334) -- ``KFZW(nmot_w, rl_w)``
  with the map at 0x5C75FE and the axis blocks at 0x5C7736 / 0x5C7758;
* ``zwgru_build`` (`FUN_0041d38c`, 0x41D38C) -- the base-angle sum and its
  s8 saturation, i.e. where an additive ethanol offset would go
  (`re/findings/ignition.md` section 11).

Everything is integer arithmetic on the raw calibration bytes; the physical
units (0.75 deg CA per LSB, 0.25 rpm per nmot LSB, 100 %/4096 per rl LSB) are
applied only by the helpers at the bottom.

`tests/test_zw_model.py` checks this module against the real code under the
Unicorn harness of `emu/`.

Usage::

    from emu.zw_model import Ignition
    ign = Ignition("data/passat_azx_ori.bin")
    ign.kfzw(nmot_w=8000, rl_w=2144)        # raw s8 counts
    ign.kfzw_deg(nmot_rpm=2000, rl_pct=52.3)
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import med9lib  # noqa: E402

# --- addresses, all VERIFIED-STATIC (re/findings/ignition.md) ---------------
KFZW = 0x5C75FE          # 16 rows (nmot) x 12 cols (rl), s8
KFZW_NMOT_AXIS = 0x5C7736   # {u16 n=16; u16 bp[16]}
KFZW_RL_AXIS = 0x5C7758     # {u16 n=12; u16 bp[12]}
KFDZW_KG = 0x5C753E      # 16x12 s8 delta, same axes
KFDZW_KG_LIMIT = 0x5C753D   # s8 upper clamp on the delta
ZWGRU_CODEWORD = 0x5C753C   # bit 7 gates the tester-adaptation term
ZW_BANK_OFFSET = 0x5C753A   # s8 added to every bank's final angle

KFZWOP = 0x5CA3F1        # 16 rows (nmot) x 11 cols (rl), s8 -- torque model
KFZWOP_NY = 0x5CA3D4     # u8 = 16
KFZWOP_NX = 0x5CA3D5     # u8 = 11
KFZWOP_NMOT_AXIS = 0x5CA3D6   # 16 u8, 40 rpm per LSB
KFZWOP_RL_AXIS = 0x5CA3E6     # 11 u8, 100/128 % per LSB

ZW_LSB_DEG = 0.75        # deg CA per count (VERIFIED-STATIC, driver 0x41CD9C)
NMOT_LSB_RPM = 0.25      # nmot_w
RL_LSB_PCT = 100.0 / 4096.0   # rl_w
NMOT8_LSB_RPM = 40.0     # the 8-bit nmot used by KFZWOP
RL8_LSB_PCT = 100.0 / 128.0   # the 8-bit rl used by KFZWOP


def _s8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


def sat_s8(v: int) -> int:
    """The `cmpwi 0x7f / cmpwi -0x80` clamp the firmware uses everywhere."""
    if v > 0x7F:
        return 0x7F
    if v < -0x80:
        return -0x80
    return v


def axis_search_u16_hint(axis: list[int], value: int, hint_index: int = 0) -> int:
    """`axis_search_u16_hint` at 0x40C9CC -> (index << 16) | frac.

    `axis` is the breakpoint list (the count word is not part of it).  The
    hint only speeds the search up; the result does not depend on it, which
    is what `tests/test_zw_model.py` checks.
    """
    n = len(axis)
    frac = 0
    idx = 0
    if axis[0] < value:
        idx = n - 1
        if value < axis[n - 1]:
            idx = hint_index
            if axis[idx] < value:
                while axis[idx + 1] <= value:
                    idx += 1
            elif value < axis[idx]:
                while value < axis[idx]:
                    idx -= 1
            if axis[idx] != value:
                frac = ((value - axis[idx]) * 0x10000) // (axis[idx + 1] - axis[idx])
    return (idx << 16) | frac


def interp_2d_s8(values: list[int], nx: int, key_y: int, key_x: int) -> int:
    """`interp_2d_s8` at 0x40C3B4.  `values[iy * nx + ix]`, s8 cells."""
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
    return _s8(v)


class Ignition:
    """The base-ignition path, read straight out of the dump."""

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

    def u16_axis(self, block_addr: int) -> list[int]:
        """`{u16 n; u16 bp[n]}` self-describing axis block."""
        n = self.u16(block_addr)
        off = self._at(block_addr + 2)
        return list(struct.unpack_from(">%dH" % n, self.data, off))

    def u8_axis(self, addr: int, n: int) -> list[int]:
        off = self._at(addr)
        return list(self.data[off:off + n])

    def s8_map(self, addr: int, ny: int, nx: int) -> list[int]:
        off = self._at(addr)
        return [_s8(b) for b in self.data[off:off + ny * nx]]

    # --- KFZW -------------------------------------------------------------
    @property
    def kfzw_nmot_axis(self) -> list[int]:
        return self.u16_axis(KFZW_NMOT_AXIS)

    @property
    def kfzw_rl_axis(self) -> list[int]:
        return self.u16_axis(KFZW_RL_AXIS)

    @property
    def kfzw_values(self) -> list[int]:
        return self.s8_map(KFZW, len(self.kfzw_nmot_axis), len(self.kfzw_rl_axis))

    def kfzw(self, nmot_w: int, rl_w: int, hint_y: int = 0, hint_x: int = 0) -> int:
        """`FUN_0041d334` -- KFZW(nmot_w, rl_w) in raw s8 counts."""
        ky = axis_search_u16_hint(self.kfzw_nmot_axis, nmot_w, hint_y)
        kx = axis_search_u16_hint(self.kfzw_rl_axis, rl_w, hint_x)
        return interp_2d_s8(self.kfzw_values, len(self.kfzw_rl_axis), ky, kx)

    def kfzw_deg(self, nmot_rpm: float, rl_pct: float) -> float:
        """KFZW in degrees BTDC for physical inputs."""
        nmot_w = int(round(nmot_rpm / NMOT_LSB_RPM))
        rl_w = int(round(rl_pct / RL_LSB_PCT))
        return self.kfzw(nmot_w, rl_w) * ZW_LSB_DEG

    # --- KFZWOP (torque model; never shifted for flex fuel) ---------------
    def kfzwop(self, nmot8: int, rl8: int) -> int:
        ny = self.u8(KFZWOP_NY)
        nx = self.u8(KFZWOP_NX)
        yax = self.u8_axis(KFZWOP_NMOT_AXIS, ny)
        xax = self.u8_axis(KFZWOP_RL_AXIS, nx)
        ky = axis_search_u16_hint(yax, nmot8, 0)
        kx = axis_search_u16_hint(xax, rl8, 0)
        return interp_2d_s8(self.s8_map(KFZWOP, ny, nx), nx, ky, kx)

    def kfzwop_deg(self, nmot_rpm: float, rl_pct: float) -> float:
        return self.kfzwop(int(round(nmot_rpm / NMOT8_LSB_RPM)),
                           int(round(rl_pct / RL8_LSB_PCT))) * ZW_LSB_DEG

    # --- zwgru ------------------------------------------------------------
    def zwgru(self, nmot_w: int, rl_w: int, *, dzw_kg: int = 0,
              adapt_04: int = 0, adapt_06: int = 0, adapt_07: int = 0,
              dzwwl: int = 0, dzw_extra: int = 0, ethanol_offset: int = 0) -> int:
        """`FUN_0041d38c` -- the base-angle sum, in raw s8 counts.

        The three tester-adaptation bytes are **not** consecutive: the code
        reads `0x14(r13)` = 0x800004, `0x16(r13)` = 0x800006 and
        `0x17(r13)` = 0x800007 (disassembly 0x41D3C8-0x41D3E4).  `dzwwl` is
        DAT_007fd313 (0x7FD313) and `dzw_extra` DAT_007fd337 (0x7FD337).

        `ethanol_offset` is the additive term the flex-fuel patch of
        `re/findings/ignition.md` section 11 inserts at 0x41D40C -- with the
        default 0 the function is bit-identical to the stock one.
        """
        acc = self.kfzw(nmot_w, rl_w)
        if (self.u8(ZWGRU_CODEWORD) & 0x80) == 0:
            acc += adapt_06 - adapt_07
        acc += dzw_kg + adapt_04 + dzwwl + dzw_extra
        acc += ethanol_offset          # <- the insertion point
        return sat_s8(acc)


def _report(dump: str = "data/passat_azx_ori.bin") -> None:
    ign = Ignition(dump)
    nmot = ign.kfzw_nmot_axis
    rl = ign.kfzw_rl_axis
    print("KFZW 0x%06X  %d x %d, s8, %.2f deg/LSB" % (KFZW, len(nmot), len(rl), ZW_LSB_DEG))
    print("  nmot axis 0x%06X: %s rpm"
          % (KFZW_NMOT_AXIS, [round(v * NMOT_LSB_RPM) for v in nmot]))
    print("  rl   axis 0x%06X: %s %%"
          % (KFZW_RL_AXIS, [round(v * RL_LSB_PCT, 1) for v in rl]))
    print()
    print("  %-7s" % "rpm\\%rl" + "".join("%7.1f" % (v * RL_LSB_PCT) for v in rl))
    vals = ign.kfzw_values
    for iy, ny in enumerate(nmot):
        row = vals[iy * len(rl):(iy + 1) * len(rl)]
        print("  %-7d" % round(ny * NMOT_LSB_RPM)
              + "".join("%7.2f" % (v * ZW_LSB_DEG) for v in row))
    print()
    print("interpolated spot checks (deg BTDC):")
    for rpm, pct in ((1800, 35.0), (3000, 60.0), (4500, 90.0), (6000, 100.0)):
        print("  KFZW(%d rpm, %.0f %%) = %6.2f   KFZWOP = %6.2f"
              % (rpm, pct, ign.kfzw_deg(rpm, pct), ign.kfzwop_deg(rpm, pct)))


if __name__ == "__main__":
    _report(sys.argv[1] if len(sys.argv) > 1 else "data/passat_azx_ori.bin")
