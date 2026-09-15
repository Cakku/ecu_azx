#!/usr/bin/env python3
"""Run the MED9.1.1 firmware from the reset vector and report where it stops.

This is the evidence script behind re/findings/emulation_boot_path.md: it runs
0x100 with nothing but the memory map of emu/memmap.py, reports the first
peripheral value the map does not provide, then adds the named stub and runs
again, so the progression is visible instead of asserted.

Nothing in the flash image is ever patched; every value comes from a read hook.

Usage:
    python3 -m emu.boot_trace                          # default dump, both stages
    python3 -m emu.boot_trace --max-insns 2000000
    python3 -m emu.boot_trace --no-stub                # stage 1 only
"""
from __future__ import annotations

import argparse

from .core import Med9Emu
from .memmap import describe, periph_name

PLPRCR = 0x6FC284          # USIU PLL / low-power / reset control
PLPRCR_LOCK_BIT = 0x8000   # written at 0x116FC, polled at 0x11704 until it reads 0


def report(title: str, res, emu: Med9Emu, top: int = 8) -> None:
    print(f"\n=== {title} ===")
    print(res.summary())
    print(f"  distinct peripheral addresses: "
          f"{len({a.addr for a in res.accesses})} (log holds {len(res.accesses)} accesses)")
    print("  most-read addresses:")
    for (pc, addr), n in res.hot_reads(top):
        print(f"    x{n:<8} pc={pc:#010x}  {describe(addr)}")
    usiu = sorted({a.addr for a in res.accesses if 0x6FC000 <= a.addr < 0x6FD000})
    print("  USIU registers touched: "
          + ", ".join(f"{a:#08x}" + (f" {periph_name(a)}" if periph_name(a) else "")
                      for a in usiu))
    imb = sorted({periph_name(a.addr).split("+")[0]
                  for a in res.accesses if 0x700000 <= a.addr < 0x710000})
    if imb:
        print("  IMB modules touched:    " + ", ".join(imb))
    if res.sprs:
        bad = sorted({s.spr for s in res.sprs if not s.modelled})
        print(f"  SPRs used: {sorted({s.spr for s in res.sprs})}; not modelled by Unicorn: {bad}")
    if res.unmapped:
        print("  unmapped accesses:")
        for a in res.unmapped[:5]:
            print(f"    {a}")
    ran_decram = [a for a in res.accesses if 0x6F8000 <= a.pc < 0x6F8800]
    print(f"  DECRAM routine executed: {'yes' if ran_decram else 'no'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?", default="data/passat_azx_ori.bin")
    ap.add_argument("--max-insns", type=int, default=400_000)
    ap.add_argument("--no-stub", action="store_true", help="skip the PLL-lock stage")
    a = ap.parse_args(argv)

    emu = Med9Emu(a.dump, r2="boot", watch_spr=True, max_log=20000)
    res = emu.run(0x100, max_insns=a.max_insns)
    report("stage 1: bare memory map, no peripheral stubs", res, emu)
    print("  -> the boot spins on the PLL lock-status bit: it sets PLPRCR bit "
          f"{PLPRCR_LOCK_BIT:#x} at 0x116FC and polls it at 0x11704 until it reads 0.")

    if a.no_stub:
        return 0

    emu2 = Med9Emu(a.dump, r2="boot", watch_spr=True, max_log=20000)
    emu2.stub_read(PLPRCR, lambda pc, addr, size: emu2.read_u32(PLPRCR) & ~PLPRCR_LOCK_BIT)
    res2 = emu2.run(0x100, max_insns=a.max_insns)
    report("stage 2: + PLL reported locked (one read hook on PLPRCR)", res2, emu2)
    print("  -> next stop: the TPU3 parameter-RAM scan at 0x14604-0x146C4, which "
          "repeats until every channel reports the expected value. Modelling the "
          "TPU3 is the next step if the boot has to run further.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
