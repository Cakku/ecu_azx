"""emu/start_model.py against the real start code (brief B8, issue #16).

Every test runs the firmware's own code under the Unicorn harness of `emu/`
and compares it with the pure-Python model:

  (a) the four interpolation helpers the start modules use --
      `lookup_2d_u8` (0x40D18C), `lookup_2d_g_u8_u16_u16` (0x40E72C),
      `lookup_2d_g_u8_u8_s8` (0x40DC7C) and `lookup_1d_g_u8_s8` (0x40F454) --
      on the real start maps, at, between and outside every breakpoint;
  (b) `mul_q15` (`FUN_00410060`, 0x410060) including its 0xFFFF saturation;
  (c) `%ESSTT` (`FUN_0041a268`) end to end: `ksta` 0x803028, the intermediate
      0x80302A and the published `ksta * kstaa` 0x80302C, read out of RAM the
      way `gk_rk` reads them;
  (d) `zwstt` (`FUN_00431294`) end to end -> RAM 0x802096;
  (e) the two flex-fuel insertion points: a neutral ethanol factor (0x400) at
      S1 and a neutral offset (0) at Z1 must reproduce the stock value bit for
      bit, and a non-neutral one must move it the modelled amount.

(c) is run with `emu.run()` from just after the module's `bl 0x000b8234`
critical-section entry to just before its `bl 0x000b81d8` exit: those two OS
primitives dispatch through the 0xFFFFFFF0 vector, which the harness does not
model.  Everything between them is the real code.

Run: python3 -m unittest tests.test_start_model -v
"""
from __future__ import annotations

import struct
import unittest

from tests.common import DUMP, DumpUnchanged, requires_dump

from emu import Med9Emu  # noqa: E402
from emu.start_model import (  # noqa: E402
    KFKSTT, KFKSTT_NX, KFKSTT_NY, KFKSTT_XAXIS, KFKSTT_YAXIS,
    KFWKSTN_STRUCT, KFWKSTT_STRUCT, KFZWSTN, KFZWSTN_NX, KFZWSTN_NY,
    KFZWSTN_XAXIS, KFZWSTN_YAXIS, KFZWSTT, KFZWSTT_NX, KFZWSTT_NY,
    KFZWSTT_XAXIS, KFZWSTT_YAXIS, KLZWSTT, KLZWSTT_AXIS, KLZWSTT_N,
    WKSTA_EEC_STRUCT, Start, _s8, mul_q15,
)

LOOKUP_2D_U8 = 0x40D18C
LOOKUP_2D_G_U8_U16_U16 = 0x40E72C
LOOKUP_2D_G_U8_U8_S8 = 0x40DC7C
LOOKUP_1D_G_U8_S8 = 0x40F454
MUL_Q15 = 0x410060

ESSTT_BODY = 0x41A274          # after `bl 0x000b8234` at 0x41A270
ESSTT_END = 0x41A684           # the `addi r11,r1,0x18` of the epilogue
ZWSTT_FUNC = 0x431294

# RAM the two modules touch (r13 = 0x7FFFF0)
RAM_B_STEND = 0x7FE921         # lbz r12,-0x16cf(r13): "start finished"
RAM_TMST = 0x8021F6            # lbz r12,0x2206(r13)
RAM_PRIST = 0x8031DA           # u16 rail pressure
RAM_INJ_CNT = 0x7FD298         # free-running injection counter
RAM_INJ_AT_START = 0x7FD26B    # its value latched at start
RAM_NMOT8 = 0x7FCE95           # 40 rpm per LSB
RAM_TRIM = 0x7FD067
RAM_W_EEC = 0x800EEC
RAM_KSTAA = 0x7FD27B
RAM_MODE = 0x7FCE0C            # bit 1 gates the whole ESSTT computation
RAM_HDR = 0x7FEA29             # high-pressure-start branch
RAM_KSTA = 0x803028
RAM_KSTA_MID = 0x80302A
RAM_KSTA_ADAPTED = 0x80302C

RAM_B_STEND_SEG = 0x7FECCA     # zwstt gate (copy of 0x7FE921)
RAM_ZDGZ = 0x7FCE14
RAM_T3E5 = 0x7FD3E5
RAM_ZWSTT = 0x802096

# a coarse but complete sweep: every breakpoint, the midpoints, and outside
TMST_CASES = [0, 20, 24, 28, 31, 40, 44, 55, 64, 77, 91, 104, 130, 144, 184, 197, 255]
ANZ_CASES = [0, 1, 3, 4, 6, 9, 12, 16, 18, 24, 30, 255]
NMOT_CASES = [0, 5, 8, 9, 10, 12, 15, 20, 25, 255]
ZDGZ_CASES = [0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 20, 255]


@requires_dump
class HelperTest(DumpUnchanged):
    """(a) + (b): the library functions the start modules call."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.st = Start(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def test_lookup_2d_u8_on_the_start_maps(self):
        for struct_addr, xs in ((KFWKSTT_STRUCT, ANZ_CASES),
                                (KFWKSTN_STRUCT, NMOT_CASES),
                                (WKSTA_EEC_STRUCT, [0, 40, 45, 48, 51, 55, 58, 61, 80, 255])):
            for tmst in TMST_CASES:
                for x in xs:
                    res = self.emu.call(LOOKUP_2D_U8, args=[struct_addr, tmst, x])
                    self.assertTrue(res.ok, res.summary())
                    self.assertEqual(res.r3 & 0xFF,
                                     self.st.lookup_2d_u8(struct_addr, tmst, x) & 0xFF,
                                     f"map {struct_addr:#x} tmst={tmst} x={x}")

    def test_lookup_2d_g_u8_u16_u16_on_kfkstt(self):
        ny = self.st.u8(KFKSTT_NY)
        nx = self.st.u8(KFKSTT_NX)
        for tmst in TMST_CASES:
            for prist in (0, 500, 1000, 2500, 4000, 6000, 20000, 65535):
                res = self.emu.call(LOOKUP_2D_G_U8_U16_U16,
                                    args=[ny, KFKSTT_YAXIS, nx, KFKSTT_XAXIS,
                                          KFKSTT, tmst, prist])
                self.assertTrue(res.ok, res.summary())
                self.assertEqual(res.r3 & 0xFFFF,
                                 self.st.lookup_2d_g_u8_u16_u16(ny, KFKSTT_YAXIS, nx,
                                                                KFKSTT_XAXIS, KFKSTT,
                                                                tmst, prist) & 0xFFFF,
                                 f"KFKSTT tmst={tmst} prist={prist}")

    def test_lookup_2d_g_u8_u8_s8_on_the_start_ignition_maps(self):
        cases = ((self.st.u8(KFZWSTT_NY), KFZWSTT_YAXIS, self.st.u8(KFZWSTT_NX),
                  KFZWSTT_XAXIS, KFZWSTT, ZDGZ_CASES),
                 (self.st.u8(KFZWSTN_NY), KFZWSTN_YAXIS, self.st.u8(KFZWSTN_NX),
                  KFZWSTN_XAXIS, KFZWSTN, NMOT_CASES))
        for ny, yax, nx, xax, val, ys in cases:
            for y in ys:
                for tmst in TMST_CASES:
                    res = self.emu.call(LOOKUP_2D_G_U8_U8_S8,
                                        args=[ny, yax, nx, xax, val, y, tmst])
                    self.assertTrue(res.ok, res.summary())
                    self.assertEqual(_s8(res.r3 & 0xFF),
                                     self.st.lookup_2d_g_u8_u8_s8(ny, yax, nx, xax,
                                                                  val, y, tmst),
                                     f"map {val:#x} y={y} tmst={tmst}")

    def test_lookup_1d_g_u8_s8(self):
        n = self.st.u8(KLZWSTT_N)
        for v in (0, 39, 40, 60, 80, 120, 160, 200, 239, 240, 255):
            res = self.emu.call(LOOKUP_1D_G_U8_S8, args=[n, KLZWSTT_AXIS, KLZWSTT, v])
            self.assertTrue(res.ok, res.summary())
            self.assertEqual(_s8(res.r3 & 0xFF),
                             self.st.lookup_1d_g_u8_s8(n, KLZWSTT_AXIS, KLZWSTT, v))

    def test_mul_q15_including_saturation(self):
        for a in (0, 1, 128, 0x400, 0x8000, 0xFF00, 0xFFFF, 25395):
            for b in (0, 1, 0x400, 0x8000, 0xFFFF, 32768, 12345):
                res = self.emu.call(MUL_Q15, args=[a, b])
                self.assertTrue(res.ok, res.summary())
                self.assertEqual(res.r3 & 0xFFFF, mul_q15(a, b), f"{a} * {b}")


def _esstt_mem(tmst, prist, anztist, nmot8, trim, w_eec, kstaa):
    return {
        RAM_B_STEND: b"\x00",
        RAM_MODE: b"\x00",
        RAM_HDR: b"\x00",
        RAM_TMST: bytes([tmst]),
        RAM_PRIST: struct.pack(">H", prist),
        RAM_INJ_CNT: bytes([anztist & 0xFF]),
        RAM_INJ_AT_START: b"\x00",
        RAM_NMOT8: bytes([nmot8]),
        RAM_TRIM: bytes([trim]),
        RAM_W_EEC: bytes([w_eec]),
        RAM_KSTAA: bytes([kstaa]),
    }


@requires_dump
class EssttTest(DumpUnchanged):
    """(c): `FUN_0041a268` -- the cranking fuel factor, end to end."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.st = Start(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def _run(self, **kw):
        res = self.emu.run(ESSTT_BODY, until=ESSTT_END, mem=_esstt_mem(**kw))
        self.assertEqual(res.stop_reason, "returned", res.summary())
        return {a: struct.unpack(">H", res.snapshot(a, 2))[0]
                for a in (RAM_KSTA, RAM_KSTA_MID, RAM_KSTA_ADAPTED)}

    def test_ksta_over_the_whole_grid(self):
        n = 0
        for tmst in TMST_CASES:
            for anztist in ANZ_CASES:
                got = self._run(tmst=tmst, prist=4000, anztist=anztist, nmot8=10,
                                trim=128, w_eec=61, kstaa=128)
                want = self.st.ksta(tmst, prist=4000, anztist=anztist, nmot8=10,
                                    trim_7fd067=128, w_800eec=61)
                self.assertEqual(got[RAM_KSTA], want, f"ksta tmst={tmst} anz={anztist}")
                self.assertEqual(got[RAM_KSTA_ADAPTED],
                                 self.st.ksta_adapted(tmst, kstaa=128, prist=4000,
                                                      anztist=anztist, nmot8=10,
                                                      trim_7fd067=128, w_800eec=61),
                                 f"ksta*kstaa tmst={tmst} anz={anztist}")
                n += 1
        self.assertGreater(n, 100)

    def test_ksta_with_the_other_ram_inputs_varied(self):
        for prist in (1000, 2500, 4000, 8000):
            for nmot8 in (5, 10, 15, 20):
                for trim in (64, 128, 200):
                    for kstaa in (100, 128, 160):
                        got = self._run(tmst=44, prist=prist, anztist=6, nmot8=nmot8,
                                        trim=trim, w_eec=58, kstaa=kstaa)
                        self.assertEqual(
                            got[RAM_KSTA],
                            self.st.ksta(44, prist=prist, anztist=6, nmot8=nmot8,
                                         trim_7fd067=trim, w_800eec=58))
                        self.assertEqual(
                            got[RAM_KSTA_ADAPTED],
                            self.st.ksta_adapted(44, kstaa=kstaa, prist=prist,
                                                 anztist=6, nmot8=nmot8,
                                                 trim_7fd067=trim, w_800eec=58))

    def test_start_finished_forces_unity(self):
        mem = _esstt_mem(tmst=24, prist=4000, anztist=0, nmot8=10, trim=128,
                         w_eec=61, kstaa=128)
        mem[RAM_B_STEND] = b"\x01"
        res = self.emu.run(ESSTT_BODY, until=ESSTT_END, mem=mem)
        self.assertEqual(res.stop_reason, "returned", res.summary())
        self.assertEqual(struct.unpack(">H", res.snapshot(RAM_KSTA_ADAPTED, 2))[0], 0x400)

    def test_s1_ethanol_factor_is_neutral_at_e0(self):
        """Insertion point S1: 0x400 = 1.0 must not change a single count."""
        for tmst in TMST_CASES:
            stock = self.st.ksta_adapted(tmst)
            self.assertEqual(self.st.ksta_adapted(tmst, ethanol_factor=0x400), stock)
            self.assertEqual(self.st.ksta_adapted(tmst, ethanol_factor=0x600),
                             min((stock * 0x600) >> 10, 0xFFFF))


@requires_dump
class ZwsttTest(DumpUnchanged):
    """(d) + (e): `FUN_00431294` -- the start ignition angle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.st = Start(DUMP)
        cls.emu = Med9Emu(DUMP, r2="app")

    def _run(self, zdgz, tmst, nmot8=10, t3e5=128, alt=False):
        mem = {RAM_B_STEND_SEG: b"\x00",
               RAM_MODE: bytes([2 if alt else 0]),
               RAM_TMST: bytes([tmst]),
               RAM_ZDGZ: bytes([zdgz]),
               RAM_NMOT8: bytes([nmot8]),
               RAM_T3E5: bytes([t3e5]),
               RAM_ZWSTT: b"\x00"}
        res = self.emu.call(ZWSTT_FUNC, mem=mem)
        self.assertTrue(res.ok, res.summary())
        return struct.unpack(">b", res.snapshot(RAM_ZWSTT, 1))[0]

    def test_zwstt_over_the_whole_grid(self):
        for zdgz in ZDGZ_CASES:
            for tmst in TMST_CASES:
                self.assertEqual(self._run(zdgz, tmst),
                                 self.st.zwstt(zdgz, tmst),
                                 f"zwstt zdgz={zdgz} tmst={tmst}")

    def test_zwstt_alternative_map(self):
        for nmot8 in NMOT_CASES:
            for tmst in TMST_CASES:
                self.assertEqual(self._run(0, tmst, nmot8=nmot8, alt=True),
                                 self.st.zwstt(0, tmst, nmot8=nmot8, alt_map=True),
                                 f"alt zwstt nmot8={nmot8} tmst={tmst}")

    def test_zwstt_speed_term_varied(self):
        for nmot8 in NMOT_CASES:
            for t3e5 in (0, 40, 128, 240, 255):
                self.assertEqual(self._run(5, 44, nmot8=nmot8, t3e5=t3e5),
                                 self.st.zwstt(5, 44, nmot8=nmot8, t3e5=t3e5))

    def test_z1_ethanol_offset_is_neutral_at_e0(self):
        """Insertion point Z1: offset 0 must not change a single count."""
        for zdgz in ZDGZ_CASES:
            for tmst in TMST_CASES:
                stock = self.st.zwstt(zdgz, tmst)
                self.assertEqual(self.st.zwstt(zdgz, tmst, ethanol_offset=0), stock)
                self.assertEqual(self.st.zwstt(zdgz, tmst, ethanol_offset=4),
                                 max(-0x80, min(0x7F, stock + 4)))

    def test_start_ignition_replaces_the_whole_angle(self):
        """The cold first fire is far retarded; the hot one is near TDC."""
        self.assertLess(self.st.zwstt_deg(0, -30.0), -40.0)
        self.assertGreater(self.st.zwstt_deg(0, 90.0), -5.0)


if __name__ == "__main__":
    unittest.main()
