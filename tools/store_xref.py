#!/usr/bin/env python3
"""store_xref.py -- every store that lands in a RAM window, with its base.

`tools/find_abs_refs.py` resolves only the `lis` + D-form pair and
`tools/sda_xref.py` only r2/r13 displacements.  Neither can see a store made
through a base register that was *computed* -- `addi` off another register, a
pointer word loaded out of flash, an `mr` copy -- or an indexed `stwx`.  That
gap is what kept `re/findings/eeprom.md` §7 Q1 (who binds the NVM device
function pointers at 0x7FAB70 / 0x7FAB74?) at "no instruction stores to them,
as far as we looked".

This walks both code regions linearly, carrying a small constant-propagation
model, and reports every store whose effective address falls in the window:

* `lis` / `addis` / `addi` / `li` / `ori` / `oris` / `mr` build register
  values; `lwz rD,d(rA)` with a resolved `rA` in flash **loads the word out of
  the image**, so a base taken from a pointer table is followed;
* r13 = 0x7FFFF0 and r2 = 0x5C9FF0 are seeded, so small-data stores are in;
* stores covered: `stb stbu sth sthu stw stwu stmw stfs stfsu stfd stfdu` and
  the indexed `stbx stbux sthx sthux stwx stwux`;
* the model is reset at every branch, so a value is only trusted inside the
  straight-line run that built it.  That makes false **negatives** possible
  where a base is built before a loop, which is why `--loops` lists the
  `stwu`-style copy loops separately and `--control` exists.

It is deliberately generous about indexed stores: when the base is known and
the index is not, the site is reported with a `?` and the base as the
address, so a window can be cleared of array walks by inspection.

Usage::

    python3 tools/store_xref.py data/passat_azx_ori.bin --window 0x7FAB58 0x7FAB80
    python3 tools/store_xref.py data/passat_azx_ori.bin --window 0x7FAB58 0x7FAB80 --near 0x1000
    python3 tools/store_xref.py data/passat_azx_ori.bin --control
    python3 tools/store_xref.py data/passat_azx_ori.bin --loops

`--control` runs the scan over three windows whose writers are known from the
findings files and prints whether each was found; a scan that fails its
control is not evidence of anything.

Dependencies: nothing beyond the standard library and `tools/med9lib.py`.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402

#: the two regions that hold instructions (boot + application external flash,
#: and the on-chip flash).  The calibration is data only.
CODE = ((0x000000, 0x1C0000), (0x404000, 0x480000))
#: where a `lwz` may be resolved from
FLASH = ((0x000000, 0x200000), (0x404000, 0x480000), (0x5C0000, 0x600000))

R13_SDA = 0x7FFFF0
R2_APP = 0x5C9FF0

DFORM_STORE = {36: ("stw", 4), 37: ("stwu", 4), 38: ("stb", 1),
               39: ("stbu", 1), 44: ("sth", 2), 45: ("sthu", 2),
               47: ("stmw", 4), 52: ("stfs", 4), 53: ("stfsu", 4),
               54: ("stfd", 8), 55: ("stfdu", 8)}
DFORM_LOAD = {32, 33, 34, 35, 40, 41, 42, 43}
X_STORE = {151: ("stwx", 4), 183: ("stwux", 4), 215: ("stbx", 1),
           247: ("stbux", 1), 407: ("sthx", 2), 439: ("sthux", 2)}
#: X-form opcodes whose destination is rA rather than rD
X_WRITES_RA = {444, 28, 60, 124, 284, 412, 476, 24, 536, 792, 824, 26, 58,
               922, 954, 986, 316}

#: (window, what should be found) -- the control set, from the findings files
CONTROLS = (
    ((0x7FB6F4, 0x7FB704), "flash_crc_task 0x11CB10 state cells "
                           "(flash_programming.md 5.3a)"),
    ((0x7FCD68, 0x7FCD6A), "nvm_mode, written only by 0x0BA0F4 / 0x0BA104 "
                           "(eeprom.md 9)"),
    ((0x803D3C, 0x803D40), "kwp_security_state / kwp_session_current "
                           "(kwp.md 2)"),
)


def _exts16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _flash_word(data, addr: int):
    for lo, hi in FLASH:
        if lo <= addr < hi - 3:
            return struct.unpack_from(">I", data, m.cpu_to_file(addr))[0]
    return None


def scan(data, lo: int, hi: int, *, near: int = 0) -> list[tuple]:
    """Stores landing in [lo, hi).  `near` also keeps bases that far below."""
    out: list[tuple] = []
    base_lo = lo - near
    for start, end in CODE:
        fo = m.cpu_to_file(start)
        regs = {13: R13_SDA, 2: R2_APP}
        for off in range(0, end - start, 4):
            site = start + off
            w = struct.unpack_from(">I", data, fo + off)[0]
            op = w >> 26
            rd = (w >> 21) & 0x1F
            ra = (w >> 16) & 0x1F
            imm = w & 0xFFFF
            if op in (16, 18, 19):                 # any branch: forget it all
                regs = {13: R13_SDA, 2: R2_APP}
                continue
            if op == 15:                           # addis / lis
                if ra and ra not in regs:
                    regs.pop(rd, None)
                else:
                    regs[rd] = ((regs.get(ra, 0) if ra else 0)
                                + (_exts16(imm) << 16)) & 0xFFFFFFFF
                continue
            if op == 14:                           # addi / li
                if ra == 0:
                    regs[rd] = _exts16(imm) & 0xFFFFFFFF
                elif ra in regs:
                    regs[rd] = (regs[ra] + _exts16(imm)) & 0xFFFFFFFF
                else:
                    regs.pop(rd, None)
                continue
            if op in (24, 25):                     # ori / oris
                if op == 24 and rd == 0 and ra == 0 and imm == 0:
                    continue                       # nop
                if ra in regs:
                    regs[rd] = regs[ra] | (imm << (16 if op == 25 else 0))
                else:
                    regs.pop(rd, None)
                continue
            if op in DFORM_LOAD:
                val = None
                if ra in regs and op == 32:        # follow a pointer word
                    val = _flash_word(data, (regs[ra] + _exts16(imm))
                                      & 0xFFFFFFFF)
                if val is None:
                    regs.pop(rd, None)
                else:
                    regs[rd] = val
                continue
            if op in DFORM_STORE:
                name, _width = DFORM_STORE[op]
                if ra in regs:
                    ea = (regs[ra] + _exts16(imm)) & 0xFFFFFFFF
                    if lo <= ea < hi:
                        out.append((site, name, ea, f"r{ra}={regs[ra]:#x}"))
                continue
            if op == 31:
                xo = (w >> 1) & 0x3FF
                rb = (w >> 11) & 0x1F
                if xo == 444 and rd == rb:         # mr rA,rS
                    if rd in regs:
                        regs[ra] = regs[rd]
                    else:
                        regs.pop(ra, None)
                    continue
                if xo in X_STORE:
                    name, _width = X_STORE[xo]
                    if ra in regs and rb in regs:
                        ea = (regs[ra] + regs[rb]) & 0xFFFFFFFF
                        if lo <= ea < hi:
                            out.append((site, name, ea,
                                        f"r{ra}={regs[ra]:#x}+"
                                        f"r{rb}={regs[rb]:#x}"))
                    elif ra in regs and base_lo <= regs[ra] < hi:
                        out.append((site, name + "?", regs[ra],
                                    f"r{ra}={regs[ra]:#x}+r{rb}=?"))
                    continue
                regs.pop(ra if xo in X_WRITES_RA else rd, None)
                continue
            if op in (20, 21, 23):                 # rlwimi / rlwinm / rlwnm
                regs.pop(ra, None)
                continue
            regs.pop(rd, None)
    return out


def loops(data) -> list[tuple]:
    """`stwu rV,4(rP)` copy/fill loops, with the destination they start at."""
    return [h for h in scan(data, 0x7F8000, 0x808000) if h[1].endswith("u")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image")
    ap.add_argument("--window", nargs=2, type=lambda s: int(s, 0),
                    metavar=("LO", "HI"), help="the RAM window, HI exclusive")
    ap.add_argument("--near", type=lambda s: int(s, 0), default=0,
                    help="also report indexed stores whose base lies this far "
                         "below LO (they could reach it with a big index)")
    ap.add_argument("--control", action="store_true",
                    help="scan three windows with known writers and say "
                         "whether they were found")
    ap.add_argument("--loops", action="store_true",
                    help="list every stwu/sthu/stbu copy or fill loop")
    args = ap.parse_args(argv)
    data = m.load_dump(args.image)

    if args.control:
        ok = True
        for (lo, hi), what in CONTROLS:
            hits = scan(data, lo, hi)
            ok = ok and bool(hits)
            print(f"  {lo:#08x}-{hi - 1:#08x}  {len(hits):3d} store site(s)  "
                  f"{'ok' if hits else 'NOT FOUND'}   {what}")
        print("\nRESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if args.loops:
        for site, name, ea, how in sorted(loops(data), key=lambda h: h[2]):
            print(f"  {site:#08x}  {name:<6} from {ea:#08x}   ({how})")
        return 0

    if not args.window:
        ap.error("give --window LO HI, or --control, or --loops")
    lo, hi = args.window
    hits = scan(data, lo, hi, near=args.near)
    print(f"{len(hits)} store site(s) into {lo:#08x}-{hi - 1:#08x}")
    for site, name, ea, how in sorted(hits, key=lambda h: (h[2], h[0])):
        print(f"  {site:#08x}  {name:<6} -> {ea:#08x}   ({how})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
