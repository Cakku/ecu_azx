"""emu/zw_model.py against the real ignition code (brief B7, issue #15).

Every test runs the firmware's own function under the Unicorn harness of
`emu/` and compares it with the pure-Python model:

  (a) `axis_search_u16_hint` (0x40C9CC) on the KFZW nmot and rl axes, for
      values below, on, between and above the breakpoints, and for every
      possible hint index;
  (b) `interp_2d_s8` (0x40C3B4) on the real KFZW value array;
  (c) `zwgru_kfzw_lookup` (`FUN_0041d334`, 0x41D334) end to end, reading
      nmot_w and rl_w out of RAM the way the ECU does;
  (d) `zwgru_build` (`FUN_0041d38c`, 0x41D38C) -- the base-angle sum and its
      s8 saturation, with and without the additive term that the flex-fuel
      patch would insert at 0x41D40C.

Run: python3 -m unittest tests.test_zw_model -v
"""
from __future__ import annotations

import struct
import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

from emu import Med9Emu  # noqa: E402
from emu.zw_model import (  # noqa: E402
    Ignition, axis_search_u16_hint, interp_2d_s8, sat_s8,
    KFZW, KFZW_NMOT_AXIS, KFZW_RL_AXIS, ZWGRU_CODEWORD,
)

AXIS_SEARCH_U16_HINT = 0x40C9CC
INTERP_2D_S8 = 0x40C3B4
ZWGRU_KFZW_LOOKUP = 0x41D334
ZWGRU_BUILD = 0x41D38C

# RAM the two ZWGRU functions touch (r13 = 0x7FFFF0)
RAM_NMOT_W = 0x7FEE74        # lhz r4,-0x117c(r13)
RAM_RL_W = 0x7FEFB2          # lhz r4,-0x103e(r13)
RAM_HINT_Y = 0x7FD5EC        # lwz r5,-0x2a04(r13)
RAM_HINT_X = 0x7FD5F0        # lwz r5,-0x2a00(r13)
RAM_RECALC_ENABLE = 0x7FEA72  # lbz r12,-0x157e(r13)
RAM_KFZW_RESULT = 0x7FD316   # stb r3,-0x2cda(r13)
RAM_DELTA_RESULT = 0x7FD314  # stb r3,-0x2cdc(r13)
RAM_DZWWL = 0x7FD313         # lbz r12,-0x2cdd(r13)
RAM_DZW_EXTRA = 0x7FD337     # lbz r10,-0x2cb9(r13)
RAM_ADAPT_04 = 0x800004      # lbz r11,0x14(r13)
RAM_ADAPT_06 = 0x800006      # lbz r12,0x16(r13)
RAM_ADAPT_07 = 0x800007      # lbz r11,0x17(r13)
RAM_ZWGRU = 0x7FD315         # stb r3,-0x2cdb(r13)


def _u32(v: int) -> int:
    return v & 0xFFFFFFFF


@requires_dump
class AxisSearchTest(DumpUnchanged):
    """(a) the breakpoint search, against 0x40C9CC."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ign = Ignition(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def _cases(self, axis):
        """Values below, on, just after, midway between and above the axis."""
        vals = [0, axis[0] - 1, axis[0], axis[0] + 1]
        for a, b in zip(axis, axis[1:]):
            vals += [a, a + 1, (a + b) // 2, b - 1]
        vals += [axis[-1], axis[-1] + 1, 0xFFFF]
        return sorted({v for v in vals if 0 <= v <= 0xFFFF})

    def _check_axis(self, block_addr, axis):
        for value in self._cases(axis):
            for hint in range(len(axis) - 1):
                res = self.emu.call(AXIS_SEARCH_U16_HINT,
                                    args=[block_addr, value, hint << 16])
                self.assertTrue(res.ok, res.summary())
                want = axis_search_u16_hint(axis, value, hint)
                self.assertEqual(res.r3, _u32(want),
                                 "axis %#x value %d hint %d: ecu %#010x model %#010x"
                                 % (block_addr, value, hint, res.r3, want))

    def test_nmot_axis(self):
        self._check_axis(KFZW_NMOT_AXIS, self.ign.kfzw_nmot_axis)

    def test_rl_axis(self):
        self._check_axis(KFZW_RL_AXIS, self.ign.kfzw_rl_axis)

    def test_hint_does_not_change_the_result(self):
        """The hint is only a search seed; every hint must give the same key."""
        axis = self.ign.kfzw_nmot_axis
        for value in (2100, 8000, 13279, 26079):
            keys = {axis_search_u16_hint(axis, value, h) for h in range(len(axis) - 1)}
            self.assertEqual(len(keys), 1, "value %d gave %r" % (value, keys))


@requires_dump
class Interp2dTest(DumpUnchanged):
    """(b) the value-array interpolator, against 0x40C3B4, on the real KFZW."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ign = Ignition(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def test_every_corner_and_a_few_fractions(self):
        values = self.ign.kfzw_values
        nx = len(self.ign.kfzw_rl_axis)
        ny = len(self.ign.kfzw_nmot_axis)
        keys = []
        for iy in range(ny - 1):
            for ix in range(nx - 1):
                for fy, fx in ((0, 0), (0, 0x8000), (0x8000, 0), (0x4000, 0xC000)):
                    keys.append(((iy << 16) | fy, (ix << 16) | fx))
        # the top row / last column, where no +1 neighbour exists
        keys.append((((ny - 1) << 16), ((nx - 1) << 16)))
        for key_y, key_x in keys:
            res = self.emu.call(INTERP_2D_S8, args=[KFZW, nx, key_y, key_x])
            self.assertTrue(res.ok, res.summary())
            got = struct.unpack(">b", struct.pack(">I", res.r3)[3:])[0]
            want = interp_2d_s8(values, nx, key_y, key_x)
            self.assertEqual(got, want,
                             "key_y=%#010x key_x=%#010x: ecu %d model %d"
                             % (key_y, key_x, got, want))


@requires_dump
class ZwgruTest(DumpUnchanged):
    """(c) and (d): the two ZWGRU functions end to end."""

    GRID = [(nmot_w, rl_w)
            for nmot_w in (1000, 2080, 2500, 8000, 12000, 15000, 20000, 26080, 30000)
            for rl_w in (200, 416, 700, 1280, 2000, 2560, 3424, 4256, 5000)]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ign = Ignition(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def _ram(self, nmot_w, rl_w, hint_y=0, hint_x=0):
        return {
            RAM_NMOT_W: struct.pack(">H", nmot_w),
            RAM_RL_W: struct.pack(">H", rl_w),
            RAM_HINT_Y: struct.pack(">I", hint_y << 16),
            RAM_HINT_X: struct.pack(">I", hint_x << 16),
        }

    def test_kfzw_lookup(self):
        for nmot_w, rl_w in self.GRID:
            res = self.emu.call(ZWGRU_KFZW_LOOKUP, mem=self._ram(nmot_w, rl_w))
            self.assertTrue(res.ok, res.summary())
            got = struct.unpack(">b", struct.pack(">I", res.r3)[3:])[0]
            want = self.ign.kfzw(nmot_w, rl_w)
            self.assertEqual(got, want,
                             "KFZW(nmot_w=%d, rl_w=%d): ecu %d model %d"
                             % (nmot_w, rl_w, got, want))

    def test_kfzw_lookup_writes_the_axis_hints_back(self):
        nmot_w, rl_w = 8000, 2144
        res = self.emu.call(ZWGRU_KFZW_LOOKUP, mem=self._ram(nmot_w, rl_w))
        self.assertTrue(res.ok, res.summary())
        key_y = struct.unpack(">I", res.snapshot(RAM_HINT_Y, 4))[0]
        key_x = struct.unpack(">I", res.snapshot(RAM_HINT_X, 4))[0]
        self.assertEqual(key_y, axis_search_u16_hint(self.ign.kfzw_nmot_axis, nmot_w, 0))
        self.assertEqual(key_x, axis_search_u16_hint(self.ign.kfzw_rl_axis, rl_w, 0))

    def test_zwgru_build(self):
        """The base-angle sum with the corrections zeroed except one each."""
        codeword = self.ign.u8(ZWGRU_CODEWORD)
        for nmot_w, rl_w in self.GRID[::3]:
            for dzwwl, dzw_extra, adapt in ((0, 0, (0, 0, 0)),
                                            (4, 0, (0, 0, 0)),
                                            (0, -3, (0, 0, 0)),
                                            (2, -2, (5, 9, 3)),
                                            (-10, 6, (-4, -7, 11))):
                mem = self._ram(nmot_w, rl_w)
                mem[RAM_RECALC_ENABLE] = b"\x01"
                mem[RAM_DZWWL] = struct.pack(">b", dzwwl)
                mem[RAM_DZW_EXTRA] = struct.pack(">b", dzw_extra)
                mem[RAM_ADAPT_04] = struct.pack(">b", adapt[0])
                mem[RAM_ADAPT_06] = struct.pack(">b", adapt[1])
                mem[RAM_ADAPT_07] = struct.pack(">b", adapt[2])
                res = self.emu.call(ZWGRU_BUILD, mem=mem)
                self.assertTrue(res.ok, res.summary())
                got = struct.unpack(">b", res.snapshot(RAM_ZWGRU, 1))[0]
                delta = struct.unpack(">b", res.snapshot(RAM_DELTA_RESULT, 1))[0]
                want = self.ign.zwgru(nmot_w, rl_w, dzw_kg=delta,
                                      adapt_04=adapt[0], adapt_06=adapt[1],
                                      adapt_07=adapt[2], dzwwl=dzwwl,
                                      dzw_extra=dzw_extra)
                self.assertEqual(got, want,
                                 "zwgru(nmot_w=%d, rl_w=%d, dzwwl=%d, extra=%d,"
                                 " adapt=%r, codeword=%#04x): ecu %d model %d"
                                 % (nmot_w, rl_w, dzwwl, dzw_extra, adapt,
                                    codeword, got, want))

    def test_ethanol_offset_is_a_plain_add_before_the_clamp(self):
        """The model's `ethanol_offset` must equal shifting dzwwl by the same
        amount -- that is what makes 0x41D40C a valid insertion point."""
        for nmot_w, rl_w in self.GRID[::5]:
            base = self.ign.zwgru(nmot_w, rl_w)
            for off in (-8, -1, 0, 1, 2, 8, 120):
                with_off = self.ign.zwgru(nmot_w, rl_w, ethanol_offset=off)
                as_dzwwl = self.ign.zwgru(nmot_w, rl_w, dzwwl=off)
                self.assertEqual(with_off, as_dzwwl)
                self.assertEqual(with_off, sat_s8(base + off) if abs(base + off) <= 0x7F
                                 else sat_s8(base + off))


if __name__ == "__main__":
    unittest.main()
