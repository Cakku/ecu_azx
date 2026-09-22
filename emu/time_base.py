#!/usr/bin/env python3
"""A running PowerPC Time Base for `read_time_base` (0x478460).

Unicorn's 603e never advances TBU/TBL, so every `mftb` in the image reads 0.
For most of the firmware that is harmless; for **`kwp_sid_27_h1`'s level-1
seed path it is fatal**.  `re/findings/kwp.md` section 3 gives the seed source
as the time base; the loop that produces it is

```
00036328  bl    0x478460            ; r3 = TBU, r4 = TBL
0003632C  lwz   r12,-0x487c(r13)    ; the previous seed, 0x7FB774
00036330  cmplw r4,r12 ; beq 0x36328    ; retry while it equals the last one
00036338  rlwinm. r12,r4,0,0,7 ; beq 0x36328  ; retry while the top byte is 0
```

so with a frozen time base `27 01` never returns: the emulator stops at the
instruction limit inside `read_time_base`.  That is why
`logging/ecu_sim.py` used to hand-seed `kwp_sec_lfsr_rounds` (0x7FB770) = 5 --
the value the seed path itself writes at 0x03635C -- instead of letting the
real handler arm it (`re/findings/boot.md` section 6.5 item 1).

This module does for the time base what `emu/os_clock.py` does for the
scheduler: it rewrites the three time-base reads **inside `read_time_base`
only** into `lwz rX, disp(r13)` against a scratch cell pair, and lets the
caller drive that pair.  Nothing is written to the image on disk; the rewrite
lives in the emulator's copy of the on-chip flash and has to be re-applied
after `Med9Emu.reset()` (:meth:`VirtualTimeBase.reinstall`).

Usage::

    from emu.time_base import VirtualTimeBase
    tb = VirtualTimeBase(emu)
    tb.advance(sim_seconds)          # before each handler call

    python3 -m emu.time_base --self-test
"""
from __future__ import annotations

import struct

#: `read_time_base` (re/symbols.csv): `mftbu r5` / `mftb r4` / `mftbu r3`,
#: then a compare-and-retry so the 64-bit value is read consistently.
READ_TIME_BASE = 0x478460
MFTBU_1 = READ_TIME_BASE + 0x0C          # 0x47846C  mftbu r5
MFTB_LO = READ_TIME_BASE + 0x10          # 0x478470  mftb  r4
MFTBU_2 = READ_TIME_BASE + 0x14          # 0x478474  mftbu r3

#: the r13 small-data base the application runs with (boot.md section 2.3)
R13_SDA = 0x7FFFF0

#: scratch pair {TBU, TBL} in the external SRAM, above everything the
#: application, `logging/ecu_sim.py` (0x807800-0x807C7F) and
#: `emu/qspi_eeprom.py`'s trampolines use.  `emu/os_clock.py` parks its own
#: virtual time base at the same address; the two are never installed on one
#: emulator.
TB_CELL = 0x807F00

#: 285 ns per tick -- the OS kernel configuration word at +0xAC, the same
#: constant `emu/os_clock.py` uses and the one brief C4 measured the raster
#: periods against (1 ms = 3508 ticks).
NS_PER_TICK = 285
TICKS_PER_S = 1_000_000_000 / NS_PER_TICK        # about 3.509 MHz

#: Where the virtual time base starts.  It is **not** zero on purpose: the
#: retry at 0x36338 refuses a seed whose top byte is 0, so a real ECU whose
#: time base has just been zeroed spins in that loop for the first
#: 0x01000000 ticks = 4.8 s.  Starting a quarter of the way through the 32-bit
#: low word keeps every simulated session out of that dead zone (the next one
#: begins 0xC0000000 ticks = about 918 simulated seconds later) without
#: pretending the dead zone does not exist on the part.
EPOCH = 0x4000_0000


def _lwz(rd: int, disp: int, ra: int = 13) -> int:
    """`lwz rD, disp(rA)` -- the D-form the mftb words are replaced with."""
    return 0x80000000 | (rd << 21) | (ra << 16) | (disp & 0xFFFF)


class VirtualTimeBase:
    """Make `read_time_base` return a value that moves.

    `emu` is a :class:`emu.core.Med9Emu`.  The three replacement instructions
    are D-form loads off r13, so they need the cell to sit within a signed
    16-bit displacement of `R13_SDA` -- which is checked in `__init__`.
    """

    def __init__(self, emu, cell: int = TB_CELL, epoch: int = EPOCH):
        self.emu = emu
        self.cell = cell
        self.epoch = epoch
        self.hi_disp = cell - R13_SDA
        self.lo_disp = cell + 4 - R13_SDA
        for disp in (self.hi_disp, self.lo_disp):
            if not -0x8000 <= disp < 0x8000:
                raise ValueError(
                    f"{cell:#x} is not reachable from r13 = {R13_SDA:#x}")
        self.ticks = epoch
        self.reinstall()

    # -- installation ------------------------------------------------------
    def reinstall(self) -> None:
        """(Re-)apply the rewrite; needed after every `Med9Emu.reset()`.

        Install it **before** anything runs `read_time_base`: Unicorn caches a
        translated block, so a rewrite of code that has already executed is
        only picked up if the cache is dropped, which `ctl_remove_cache` does
        where the binding offers it.
        """
        for addr, word in ((MFTBU_1, _lwz(5, self.hi_disp)),
                           (MFTB_LO, _lwz(4, self.lo_disp)),
                           (MFTBU_2, _lwz(3, self.hi_disp))):
            self.emu.write(addr, struct.pack(">I", word))
        drop = getattr(self.emu.uc, "ctl_remove_cache", None)
        if drop is not None:                                  # unicorn >= 2
            try:
                drop(READ_TIME_BASE, READ_TIME_BASE + 0x30)
            except Exception:                                 # pragma: no cover
                pass
        self._store(self.ticks)

    @property
    def installed(self) -> bool:
        return (struct.unpack(">I", self.emu.read(MFTB_LO, 4))[0]
                == _lwz(4, self.lo_disp))

    # -- the clock ---------------------------------------------------------
    def _store(self, ticks: int) -> None:
        self.ticks = ticks & 0xFFFF_FFFF_FFFF_FFFF
        self.emu.write(self.cell, struct.pack(">II", self.ticks >> 32,
                                              self.ticks & 0xFFFFFFFF))

    def advance(self, seconds: float) -> int:
        """Set the time base to `seconds` after power-on, never backwards.

        The seed loop also refuses a value that equals the previous seed, so
        the counter is forced forward by at least one tick per call: a
        simulator whose clock moves in 10 ms steps can otherwise serve two
        requests at the same simulated instant and hang the handler.
        """
        want = self.epoch + int(seconds * TICKS_PER_S)
        return self.set_ticks(max(want, self.ticks + 1))

    def set_ticks(self, ticks: int) -> int:
        self._store(ticks)
        return self.ticks

    def read(self) -> int:
        hi, lo = struct.unpack(">II", self.emu.read(self.cell, 8))
        return (hi << 32) | lo


# ---------------------------------------------------------------------------
def self_test(dump_path: str = "data/passat_azx_ori.bin") -> bool:
    """`read_time_base` returns the cell, and it moves."""
    from emu import Med9Emu

    ok = True
    stock = Med9Emu(dump_path, r2="app")
    res = stock.call(READ_TIME_BASE, reset=False)
    frozen = (res.regs["r3"] << 32) | res.regs["r4"] if res.ok else None
    print(f"  stock read_time_base: ok={res.ok} value={frozen}")
    ok = ok and res.ok and frozen == 0

    # a fresh emulator: Unicorn caches a translated block, so the rewrite has
    # to be in place before `read_time_base` is first executed
    emu = Med9Emu(dump_path, r2="app")
    tb = VirtualTimeBase(emu)
    seen = []
    for t in (0.0, 1.0, 1.0, 2.5):
        tb.advance(t)
        res = emu.call(READ_TIME_BASE, reset=False)
        if not res.ok:
            print(f"  t={t}: handler did not return ({res.stop_reason})")
            return False
        seen.append((res.regs["r3"] << 32) | res.regs["r4"])
    print(f"  virtual: {[hex(v) for v in seen]}")
    ok = ok and seen == sorted(seen) and len(set(seen)) == len(seen)
    ok = ok and all((v & 0xFF000000) for v in seen)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


def main(argv=None) -> int:                                   # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default="data/passat_azx_ori.bin")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return 0 if self_test(args.dump) else 1
    ap.print_help()
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    import sys
    sys.exit(main())
