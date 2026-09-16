#!/usr/bin/env python3
"""Compare RAM snapshots taken over KWP RequestUpload and classify every byte.

The static survey (`tools/ram_survey.py`, `re/findings/ram.md`) can only say
that no instruction in the image *names* a byte.  It cannot see a byte written
through a pointer the code computes at run time, and it cannot see a stack.
This tool closes that gap: C3's logger reads the same ranges at several points
in an ECU's life and this compares them.

A byte is put in exactly one class:

| class | meaning |
|---|---|
| `changed`  | its value differs between at least two snapshots -> **live RAM, never use it** |
| `constant` | identical everywhere and neither 0x00 nor 0xFF -> live RAM holding a constant, or a variable that happened not to move. **Suspicious: do not use it without more snapshots.** |
| `blank`    | identical everywhere and 0x00 or 0xFF in every snapshot -> nothing wrote it in any of the recorded sessions |

Only a byte that is `blank` in a snapshot set that includes a key-on, an idle,
a post-drive and three key cycles is a confirmed-free byte, and only then when
the static survey agrees.  The snapshot protocol is in `re/findings/ram.md`
section 8.

Snapshot format (`logging/sessions/*.json`, one file per session)::

    {"session": "key-on", "taken": "2026-09-20T10:00:00Z",
     "ecu": "03H906032 / 1037382557",
     "transport": "KWP2000 0x35/0x36",
     "ranges": [{"start": "0x7F8000", "end": "0x7F9E3B", "data": "00ff12..."}]}

`data` is lowercase hex, exactly ``end - start + 1`` bytes.  The ranges of a
snapshot may not overlap; snapshots are compared only over the ranges they all
share.

Usage::

    python3 tools/ram_snapshot_diff.py logging/sessions/*.json
    python3 tools/ram_snapshot_diff.py a.json b.json --csv work/ramdiff.csv
    python3 tools/ram_snapshot_diff.py a.json b.json --free 64
    python3 tools/ram_snapshot_diff.py --self-test      # synthetic snapshots

Dependency-free (standard library only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BLANK = (0x00, 0xFF)


class Snapshot:
    def __init__(self, name: str, doc: dict):
        self.name = doc.get("session") or name
        self.path = name
        self.bytes: dict[int, int] = {}
        self.ranges: list[tuple[int, int]] = []
        for r in doc.get("ranges", []):
            start = int(str(r["start"]), 16)
            end = int(str(r["end"]), 16)
            raw = bytes.fromhex(r["data"])
            if len(raw) != end - start + 1:
                raise ValueError(
                    f"{name}: range 0x{start:06X}-0x{end:06X} declares "
                    f"{end - start + 1} bytes but carries {len(raw)}")
            for i, b in enumerate(raw):
                if start + i in self.bytes:
                    raise ValueError(f"{name}: address 0x{start + i:06X} appears twice")
                self.bytes[start + i] = b
            self.ranges.append((start, end))

    @classmethod
    def load(cls, path) -> "Snapshot":
        with open(path, encoding="utf-8") as fh:
            return cls(str(path), json.load(fh))


def classify(snaps: list[Snapshot]) -> dict[int, str]:
    """Address -> 'changed' | 'constant' | 'blank', over the common addresses."""
    common = set(snaps[0].bytes)
    for s in snaps[1:]:
        common &= set(s.bytes)
    out = {}
    for a in common:
        values = {s.bytes[a] for s in snaps}
        if len(values) > 1:
            out[a] = "changed"
        elif values.pop() in BLANK:
            out[a] = "blank"
        else:
            out[a] = "constant"
    return out


def runs(verdict: dict[int, str], want: str, minimum: int = 1) -> list:
    """Maximal contiguous runs of addresses whose class is `want`."""
    out, start, prev = [], None, None
    for a in sorted(verdict):
        ok = verdict[a] == want
        if ok and (start is None or prev != a - 1):
            if start is not None and prev - start + 1 >= minimum:
                out.append((start, prev))
            start = a
        elif not ok and start is not None:
            if prev - start + 1 >= minimum:
                out.append((start, prev))
            start = None
        prev = a
    if start is not None and prev - start + 1 >= minimum:
        out.append((start, prev))
    return out


def synthetic() -> list[Snapshot]:
    """Three snapshots of 0x800000-0x8000FF for the self-test.

    * 0x800000-0x80003F counts up differently in each snapshot -> changed
    * 0x800040-0x80007F holds 0x5A everywhere                  -> constant
    * 0x800080-0x8000BF is 0x00 everywhere                     -> blank
    * 0x8000C0-0x8000FF is 0xFF in two snapshots and 0x00 in one -> changed
    """
    out = []
    for n, name in enumerate(("key-on", "idle", "after-drive")):
        data = bytearray(0x100)
        for i in range(0x40):
            data[i] = (i + n * 7) & 0xFF
        for i in range(0x40, 0x80):
            data[i] = 0x5A
        for i in range(0x80, 0xC0):
            data[i] = 0x00
        for i in range(0xC0, 0x100):
            data[i] = 0x00 if n == 1 else 0xFF
        out.append(Snapshot(name, {
            "session": name,
            "ranges": [{"start": "0x800000", "end": "0x8000FF",
                        "data": bytes(data).hex()}],
        }))
    return out


def report(snaps: list[Snapshot], min_free: int, csv_path: str | None,
           quiet: bool = False) -> dict:
    verdict = classify(snaps)
    counts = {k: 0 for k in ("changed", "constant", "blank")}
    for v in verdict.values():
        counts[v] += 1
    if not quiet:
        print("# snapshots compared:")
        for s in snaps:
            spans = ", ".join(f"0x{a:06X}-0x{b:06X}" for a, b in s.ranges)
            print(f"#   {s.name:<16s} {len(s.bytes):6d} B   {spans}")
        print(f"# common addresses: {len(verdict)}")
        for k in ("changed", "constant", "blank"):
            pct = 100.0 * counts[k] / len(verdict) if verdict else 0.0
            print(f"#   {k:<9s} {counts[k]:6d}  ({pct:.1f} %)")
        print()
        print(f"blank runs of at least {min_free} B "
              f"(candidate free RAM, confirm against re/ram_map.csv):")
        found = runs(verdict, "blank", min_free)
        for lo, hi in sorted(found, key=lambda r: r[1] - r[0], reverse=True):
            print(f"  0x{lo:06X}-0x{hi:06X}  {hi - lo + 1:6d} B")
        if not found:
            print("  (none)")
        print()
        print("constant (never changed but not blank) runs of at least "
              f"{min_free} B -- these look\nfree but are not:")
        for lo, hi in sorted(runs(verdict, "constant", min_free),
                             key=lambda r: r[1] - r[0], reverse=True)[:20]:
            print(f"  0x{lo:06X}-0x{hi:06X}  {hi - lo + 1:6d} B")
    if csv_path:
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("addr,class," + ",".join(s.name for s in snaps) + "\n")
            for a in sorted(verdict):
                vals = ",".join("0x%02X" % s.bytes[a] for s in snaps)
                fh.write("0x%06X,%s,%s\n" % (a, verdict[a], vals))
        if not quiet:
            print(f"\nwrote {csv_path}")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshots", nargs="*", help="two or more snapshot JSON files")
    ap.add_argument("--csv", help="write the per-byte verdict here")
    ap.add_argument("--free", type=lambda v: int(v, 0), default=16,
                    help="shortest run to report (default 16)")
    ap.add_argument("--self-test", action="store_true",
                    help="run against the built-in synthetic snapshots")
    args = ap.parse_args(argv)

    if args.self_test:
        snaps = synthetic()
        counts = report(snaps, args.free, args.csv)
        expected = {"changed": 0x80, "constant": 0x40, "blank": 0x40}
        if counts != expected:
            print(f"SELF-TEST FAILED: {counts} != {expected}", file=sys.stderr)
            return 1
        print("\nself-test OK")
        return 0

    if len(args.snapshots) < 2:
        ap.error("give at least two snapshot files, or --self-test")
    snaps = [Snapshot.load(Path(p)) for p in args.snapshots]
    report(snaps, args.free, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
