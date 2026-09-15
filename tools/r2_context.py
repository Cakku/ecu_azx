#!/usr/bin/env python3
"""Decide, and check, the r2 (SDA2) base of every function in the MED9.1.1 dump.

The boot module runs with r2 = 0x017FF0 (set at file 0x10E8), the application
with r2 = 0x5C9FF0 (set by app_sda_setup_a/b/int).  Which base a function uses
cannot be read off a single instruction, so this tool

  * walks the call graph (tools/callgraph.py) from the boot entry points and
    stops at the application SDA setup routines -> the boot function set;
  * for every function in the image, resolves each r2-relative D-form access
    under the assigned base and reports the ones that land on 0xFF filler,
    outside a mapped region, or outside the SDA2 window (base-0x8000 ..
    base+0x7FFF);
  * with --compare, scores each function under *both* bases so a wrong
    assignment shows up as a large 0xFF / unmapped count on one side.

Usage:
    python3 tools/r2_context.py data/passat_azx_ori.bin
    python3 tools/r2_context.py data/passat_azx_ori.bin --compare --min-refs 4
    python3 tools/r2_context.py data/passat_azx_ori.bin --list-boot
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib  # noqa: E402
from callgraph import Image, scan_bl_targets, walk, reachable  # noqa: E402

BOOT_R2 = 0x017FF0
APP_R2 = 0x5C9FF0
BOOT_SEEDS = (0x1004, 0x12328)
APP_SDA_SETUP = (0x986AC, 0x9E3E0, 0x405588)


def resolve(img: Image, addr: int):
    """Return ('ok'|'ff'|'unmapped', byte) for a data address."""
    try:
        off = med9lib.cpu_to_file(addr)
    except ValueError:
        return "unmapped", None
    b = img.data[off]
    return ("ff" if b == 0xFF else "ok"), b


def score(img: Image, fn, base: int):
    """Count how the function's r2 references resolve under `base`."""
    ok = ff = unmapped = outside = 0
    for pc, mnem, ra, disp in fn.sda:
        if ra != 2:
            continue
        target = (base + disp) & 0xFFFFFFFF
        if not (base - 0x8000 <= target <= base + 0x7FFF):
            outside += 1
            continue
        state, _ = resolve(img, target)
        if state == "ok":
            ok += 1
        elif state == "ff":
            ff += 1
        else:
            unmapped += 1
    return ok, ff, unmapped, outside


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--compare", action="store_true",
                    help="score every function under both bases")
    ap.add_argument("--min-refs", type=int, default=1)
    ap.add_argument("--list-boot", action="store_true")
    ap.add_argument("--violations", action="store_true",
                    help="print each r2 reference that does not resolve")
    args = ap.parse_args()

    img = Image(args.image)
    entries = scan_bl_targets(img)
    boot = reachable(img, entries, BOOT_SEEDS, APP_SDA_SETUP)
    boot_set = set(boot)

    if args.list_boot:
        for e in sorted(boot_set):
            print(f"{e:#08x}")
        print(f"# {len(boot_set)} boot functions, "
              f"range {min(boot_set):#x}-{max(boot_set):#x}")
        return 0

    all_entries = sorted(entries | set(BOOT_SEEDS))
    tot = {"boot": [0, 0, 0, 0], "app": [0, 0, 0, 0]}
    disagree = []
    for e in all_entries:
        fn = boot.get(e) or walk(img, e, entries)
        nrefs = sum(1 for s in fn.sda if s[2] == 2)
        if nrefs < args.min_refs:
            continue
        is_boot = e in boot_set
        base = BOOT_R2 if is_boot else APP_R2
        s = score(img, fn, base)
        key = "boot" if is_boot else "app"
        for i in range(4):
            tot[key][i] += s[i]
        if args.compare:
            other = score(img, fn, APP_R2 if is_boot else BOOT_R2)
            bad, bad_other = s[1] + s[2] + s[3], other[1] + other[2] + other[3]
            if bad > bad_other:
                disagree.append((e, key, nrefs, s, other))
        if args.violations and (s[1] or s[2] or s[3]):
            for pc, mnem, ra, disp in fn.sda:
                if ra != 2:
                    continue
                t = (base + disp) & 0xFFFFFFFF
                st, b = resolve(img, t)
                if st != "ok" or not (base - 0x8000 <= t <= base + 0x7FFF):
                    print(f"{pc:#08x} {mnem:6s} r2{disp:+#x} -> {t:#08x} "
                          f"[{key}] {st}")

    print(f"# boot functions: {len(boot_set)}  "
          f"range {min(boot_set):#x}-{max(boot_set):#x}")
    for key in ("boot", "app"):
        ok, ff, um, outside = tot[key]
        print(f"# {key:4s} base={BOOT_R2 if key == 'boot' else APP_R2:#08x}  "
              f"ok={ok} ff={ff} unmapped={um} outside_window={outside}")
    if args.compare:
        print(f"# functions that score better under the *other* base: "
              f"{len(disagree)}")
        for e, key, n, s, o in disagree:
            print(f"  {e:#08x} [{key}] refs={n} assigned(ok/ff/um/out)={s} "
                  f"other={o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
