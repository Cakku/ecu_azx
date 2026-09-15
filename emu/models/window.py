#!/usr/bin/env python3
"""Bit-exact Python model of the MED9.1.1 injection-window check (`%AWEA`).

Brief B9, issue #17.  Models the angle-synchronous half of `%AWEA` in
`data/passat_azx_ori.bin` (03H906032 / 1037382557):

| model            | ECU function | what it is                                  |
|------------------|--------------|---------------------------------------------|
| `dwi`            | 0x0041B9C0   | injection duration `ti` -> crank angle       |
| `window_clamp`   | 0x0041B9C0   | the injection-window check and its clamp     |
| `k_nmot`         | 0x0041B9A4   | the angle-per-time constant from `nmot`      |
| `rl_max_window`  | 0x00454B28   | the window expressed as a charge limit       |

Units, both **VERIFIED-STATIC** in `re/findings/rail.md` sections 8 and 14:
the angle LSB is **3/128 degCA** (one cycle = 0x7800 = 720 degCA) and `ti` is
**1 us** per LSB.  The u8 angle maps carry 0.75 degCA per count, hence the
`* 0x20` everywhere.

The window itself is two flat curves in this dataset, read out of the image
rather than hard-coded: `KLWBHO1SMX` (0x5D3BE8) -> 0x7FD290 = 67 counts =
50.25 degCA of required end-of-injection margin, and `KLWBHO1SLT` (0x5D3BDB)
-> 0x7FD28F = 200 counts, i.e. a latest start of 360.0 degCA.

Usage::

    from emu.models.window import WindowModel
    m = WindowModel("data/passat_azx_ori.bin")
    m.k_nmot(nmot_w=24000)                       # 6000 min^-1 -> 12583
    m.dwi(ti=4500, k=12583)                      # injection duration in angle LSB
    m.window_clamp(wbho1s=14080, ti=4500, k=12583)   # -> (dwi, wbho1s_out)
    m.max_ti(wbho1s=14080, k=12583)              # the ti at which the clamp engages

Self-check against the real code (needs unicorn)::

    python3 -m emu.models.window --verify
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "tools"))
import med9lib  # noqa: E402

# --- addresses, all VERIFIED-STATIC (re/findings/rail.md sections 8-10) ------

AWEA_TI_TO_ANGLE = 0x41B9C0   # void awea_ti_to_angle(void), all state in RAM
AWEA_K_NMOT = 0x41B9A4        # the eight instructions that build k_nmot

K_NMOT_MUL = 0x8638           # 34360, the literal at 0x41B9AC
CYCLE = 0x7800                # 30720 angle LSB = 720 degCA
ANGLE_BASE = 0x2300           # 8960 angle LSB = 210.0 degCA, the u8 map base
MAP_COUNT = 0x20              # 32 angle LSB = 0.75 degCA per u8 map count

DWBHO1SMN = 0x5D396D          # u8 window scalar added to the new start angle
DWBHO1SMX = 0x5D396E          # u8 window scalar added to the trigger threshold
KVWBHRL = 0x5D396F            # u8 = 13, window -> relative charge
KLWBHO1SMX_N = 0x5D3BE8       # {u8 n; u8 pad; u16 axis[n]; u8 val[n]} -> 0x7FD290
KLWBHO1SLT_N = 0x5D3BDB       # {u8 n; u8 axis[n]; u8 val[n]}          -> 0x7FD28F

# RAM cells of the angle-synchronous half
RAM_TI_HOM = 0x8030E8         # u16 ti of the homogeneous injection
RAM_K_NMOT = 0x803072         # u16 angle per unit time
RAM_DWI = 0x803088            # u16 result: ti as an angle
RAM_WBHO1S_SET = 0x803078     # s16 start-of-injection setpoint from awea_angles
RAM_WBHO1S = 0x80307E         # s16 start-of-injection actually used
RAM_INJ_TYPE = 0x803092       # u16 bit mask of the active injection types
RAM_DYN_PREV = 0x80306A       # s16 previous dynamic correction
RAM_DYN = 0x803064            # s16 dynamic correction
RAM_NMOT = 0x7FEE74           # u16 nmot_w, 0.25 min^-1 per LSB
RAM_MARGIN = 0x7FD290         # u8 required end-of-injection margin, 0.75 degCA
RAM_LATEST = 0x7FD28F         # u8 latest permitted start, 0.75 degCA
RAM_B_WBH_INVALID = 0x7FEA48  # u8 = (prist > PRWBHMX)
RAM_B_DYN = 0x7FEA45          # u8 dynamic-correction latch
RAM_B_RUN = 0x7FE91F          # u8 engine-running flag
RAM_FUEL_FAULT = 0x80201E     # u8 fuel-system fault byte, bit 5 arms the check
RAM_MODE_BITS = 0x7FD04D      # u8, bit 6 skips the remaining injection types


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _clamp_s16(v: int) -> int:
    return -0x8000 if v < -0x8000 else (0x7FFF if v > 0x7FFF else v)


class WindowModel:
    """Every calibration value is read out of the image, never hard-coded."""

    def __init__(self, image: str = "data/passat_azx_ori.bin"):
        with open(image, "rb") as fh:
            self.img = fh.read()

    # --- raw reads ------------------------------------------------------
    def u8(self, cpu: int) -> int:
        return self.img[med9lib.cpu_to_file(cpu)]

    def u16(self, cpu: int) -> int:
        o = med9lib.cpu_to_file(cpu)
        return int.from_bytes(self.img[o:o + 2], "big")

    # --- the two window terms, as awea_angles computes them -------------
    def margin_counts(self) -> int:
        """0x7FD290: KLWBHO1SMX is flat in this dataset, so any axis value
        gives the same answer; we take the first (and assert flatness)."""
        n = self.u8(KLWBHO1SMX_N)
        vals = [self.u8(KLWBHO1SMX_N + 2 + 2 * n + i) for i in range(n)]
        assert len(set(vals)) == 1, f"KLWBHO1SMX is not flat: {vals}"
        return vals[0]

    def latest_counts(self) -> int:
        """0x7FD28F: KLWBHO1SLT, same story."""
        n = self.u8(KLWBHO1SLT_N)
        vals = [self.u8(KLWBHO1SLT_N + 1 + n + i) for i in range(n)]
        assert len(set(vals)) == 1, f"KLWBHO1SLT is not flat: {vals}"
        return vals[0]

    # --- the arithmetic -------------------------------------------------
    def k_nmot(self, nmot_w: int) -> int:
        """0x41B9A4-0x41B9B8: k = (nmot_w * 34360) >> 16, kept as u16."""
        return ((nmot_w & 0xFFFF) * K_NMOT_MUL >> 16) & 0xFFFF

    def dwi(self, ti: int, k: int) -> int:
        """0x41BB54-0x41BB70: dwi = min((ti * k) >> 13, 0x7FFF)."""
        v = ((ti & 0xFFFF) * (k & 0xFFFF)) >> 13
        return v if v < 0x7FFF else 0x7FFF

    def window_clamp(self, wbho1s: int, ti: int, k: int,
                     margin: int | None = None,
                     latest: int | None = None) -> tuple[int, int]:
        """0x41BB54-0x41BC30.  Returns (dwi, wbho1s_out).

        `wbho1s` is the start-of-injection angle going in (RAM 0x80307E after
        the dynamic-correction block), everything in angle LSB of 3/128 degCA.
        """
        margin = self.margin_counts() if margin is None else margin
        latest = self.latest_counts() if latest is None else latest
        d = self.dwi(ti, k)
        out = _s16(wbho1s)
        trigger = _s16((self.u8(DWBHO1SMX) + margin) * MAP_COUNT)
        if _clamp_s16(out - d) <= trigger:
            start = _clamp_s16(_s16((margin + self.u8(DWBHO1SMN)) * MAP_COUNT) + d)
            cap = _s16(latest * MAP_COUNT + ANGLE_BASE)
            out = min(start, cap)
        return d, out

    # --- derived quantities the flex-fuel work needs --------------------
    def max_ti(self, wbho1s: int, k: int) -> int:
        """The largest `ti` that still fits the window at this start angle."""
        if k == 0:
            return 0xFFFF
        margin = self.margin_counts()
        room = _s16(wbho1s) - _s16((self.u8(DWBHO1SMX) + margin) * MAP_COUNT)
        if room <= 0:
            return 0
        return min((room << 13) // k, 0xFFFF)

    def rl_max_window(self, k: int, latest: int | None = None,
                      margin: int | None = None) -> int:
        """0x454B28-0x454B70: the window as a relative-charge limit.

        This is what a *fault* arms; with the fuel fault bit 0x80201E clear
        the ECU writes 0xFFFF instead (re/findings/rail.md section 10).
        """
        margin = self.margin_counts() if margin is None else margin
        latest = self.latest_counts() if latest is None else latest
        if k == 0:
            return 0xFFFF
        n = (latest - margin + ANGLE_BASE // MAP_COUNT) * self.u8(KVWBHRL)
        return min((n << 15) // k, 0xFFFF)

    # --- unit helpers ---------------------------------------------------
    @staticmethod
    def angle_deg(lsb: int) -> float:
        return lsb * 3.0 / 128.0

    @staticmethod
    def deg_angle(deg: float) -> int:
        return int(round(deg * 128.0 / 3.0))


def _u16b(v: int) -> bytes:
    return (v & 0xFFFF).to_bytes(2, "big")


def verify(image: str = "data/passat_azx_ori.bin", verbose: bool = True) -> int:
    """Drive the real 0x41B9C0 in the Unicorn harness and compare bit-exactly.

    The dynamic-correction block that runs before the window check is made
    deterministic by setting 0x80306A = 0: |0| < 0x5C713E (= 427) makes
    0x7FEA46 true, which clears the latch 0x7FEA45, so the start angle passes
    through unchanged and only the window clamp is exercised.
    """
    sys.path.insert(0, os.path.join(_HERE, "..", ".."))
    from emu import Med9Emu

    model = WindowModel(image)
    emu = Med9Emu(image, r2="app")
    margin = model.margin_counts()
    latest = model.latest_counts()
    bad = 0
    n = 0

    nmots = [800, 3200, 8000, 12000, 16000, 24000, 26000]         # 0.25 rpm/LSB
    tis = [0, 500, 900, 2000, 3000, 4500, 6000, 7800, 9000,
           12000, 20000, 40000, 65535]
    # start angles: the KFWBHO1SW range (210..330 degCA) and both edges
    starts = [model.deg_angle(d) for d in (0, 50, 60, 100, 210, 240, 280,
                                           330, 360, 390, 420)]

    for nmot in nmots:
        k = model.k_nmot(nmot)
        for start in starts:
            for ti in tis:
                mem = {
                    RAM_INJ_TYPE: _u16b(0x0001),      # homogeneous active
                    RAM_MODE_BITS: bytes([0x40]),     # skip the other types
                    RAM_DYN_PREV: _u16b(0),           # -> 0x7FEA46 true
                    RAM_B_DYN: b"\x00",
                    RAM_B_WBH_INVALID: b"\x00",       # window model valid
                    RAM_B_RUN: b"\x01",               # engine running
                    RAM_FUEL_FAULT: b"\x00",
                    RAM_WBHO1S_SET: _u16b(start),
                    RAM_WBHO1S: _u16b(start),
                    RAM_DYN: _u16b(0),
                    RAM_TI_HOM: _u16b(ti),
                    RAM_K_NMOT: _u16b(k),
                    RAM_MARGIN: bytes([margin]),
                    RAM_LATEST: bytes([latest]),
                }
                r = emu.call(AWEA_TI_TO_ANGLE, args=[], mem=mem)
                e_dwi = int.from_bytes(r.snapshot(RAM_DWI, 2), "big")
                e_out = _s16(int.from_bytes(r.snapshot(RAM_WBHO1S, 2), "big"))
                m_dwi, m_out = model.window_clamp(start, ti, k, margin, latest)
                n += 1
                if (e_dwi, e_out) != (m_dwi & 0xFFFF, m_out):
                    bad += 1
                    if verbose and bad < 20:
                        print(f"window_clamp(nmot={nmot},start={start},"
                              f"ti={ti},k={k}): ecu=({e_dwi},{e_out}) "
                              f"model=({m_dwi},{m_out})")

    # --- k_nmot, against the real eight instructions ----------------------
    for nmot in nmots + [0, 1, 65535]:
        r = emu.run(AWEA_K_NMOT, until=AWEA_K_NMOT + 0x18,
                    mem={RAM_NMOT: _u16b(nmot)})
        e_k = int.from_bytes(r.snapshot(RAM_K_NMOT, 2), "big")
        n += 1
        if e_k != model.k_nmot(nmot):
            bad += 1
            if verbose and bad < 20:
                print(f"k_nmot({nmot}): ecu={e_k} model={model.k_nmot(nmot)}")

    if verbose:
        print(f"{n - bad}/{n} cases match" if bad else f"all {n} cases match")
    return bad


def _report(image: str) -> None:
    m = WindowModel(image)
    margin, latest = m.margin_counts(), m.latest_counts()
    print(f"margin 0x7FD290 = {margin} counts = "
          f"{margin * 0.75:.2f} degCA (KLWBHO1SMX 0x5D3BE8, flat)")
    print(f"latest 0x7FD28F = {latest} counts = "
          f"{latest * 0.75 + ANGLE_BASE * 3 / 128:.2f} degCA (KLWBHO1SLT 0x5D3BDB, flat)")
    print(f"KVWBHRL 0x5D396F = {m.u8(KVWBHRL)},  "
          f"DWBHO1SMN/MX = {m.u8(DWBHO1SMN)}/{m.u8(DWBHO1SMX)}")
    print()
    print("  rpm   wbho1s     max ti    at which the clamp engages")
    for rpm in (1000, 2000, 3000, 4000, 5000, 6000):
        k = m.k_nmot(rpm * 4)
        for deg in (210.0, 270.0, 330.0):
            a = m.deg_angle(deg)
            print(f" {rpm:5d}   {deg:6.1f}   {m.max_ti(a, k):6d} us"
                  f"   ({m.angle_deg(m.dwi(m.max_ti(a, k), k)):6.1f} degCA)")


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(1 if verify() else 0)
    _report(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
            else "data/passat_azx_ori.bin")
