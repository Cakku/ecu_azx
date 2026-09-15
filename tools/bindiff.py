#!/usr/bin/env python3
"""Diff two MED9.1 dump images and prove that only intended bytes changed.

Every changed byte is classified as one of

    patch        inside a change listed in the patch's `patch.json`
                 (format: docs/06_patch_pipeline.md section 1)
    descriptor   inside the sum/~sum words of a block-checksum descriptor
                 (the four tables in tools/checksum.py)
    unexpected   anything else

Consecutive bytes of the same class are reported as one range, with the file
offset and the CPU address from tools/med9lib.py.  The exit status is 1 if a
single unexpected byte changed, so this is usable straight from a Makefile or
a pre-flash check (docs/06_patch_pipeline.md section 5, step 2).

Usage:
    bindiff.py STOCK.bin PATCHED.bin [-p patches/ff_counter/patch.json]
    bindiff.py STOCK.bin PATCHED.bin -p patch.json --json report.json
    bindiff.py STOCK.bin PATCHED.bin --json -          # report on stdout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checksum as cs  # noqa: E402
import med9lib as m  # noqa: E402

CLS_PATCH = "patch"
CLS_DESCRIPTOR = "descriptor"
CLS_UNEXPECTED = "unexpected"

MAX_HEX = 32          # bytes of old/new shown per range before truncating


@dataclass
class Range:
    file_start: int
    file_end: int          # exclusive
    cpu_start: int
    cls: str
    detail: str
    old: str
    new: str

    @property
    def length(self) -> int:
        return self.file_end - self.file_start

    def line(self) -> str:
        alias = ""
        if self.file_start < m.EXT_FLASH_SIZE and self.file_start >= 0x80000:
            alias = f" [alias {self.file_start + m.HIGH_ALIAS_BASE:#08x}]"
        return (f"{self.cls:<10} cpu {self.cpu_start:#08x}"
                f"-{self.cpu_start + self.length - 1:#08x}{alias}  "
                f"file {self.file_start:#08x} ({self.length} B)  "
                f"{self.old} -> {self.new}"
                + (f"  {self.detail}" if self.detail else ""))


def _hex(buf: bytes) -> str:
    if len(buf) <= MAX_HEX:
        return buf.hex()
    return buf[:MAX_HEX].hex() + f"...(+{len(buf) - MAX_HEX}B)"


def descriptor_word_map() -> dict[int, str]:
    """file offset -> descriptor label, for every byte of every sum/~sum word."""
    out: dict[int, str] = {}
    for table, count in cs.TABLES:
        for k in range(count):
            off = table + k * cs.DESC_SIZE
            label = f"desc {off:#08x} (table {table:#08x} entry {k})"
            for b in range(off + 8, off + cs.DESC_SIZE):
                out[b] = label
    return out


def load_patch(path: str | Path) -> tuple[dict, dict[int, str]]:
    """Return (patch dict, file offset -> 'change #i <why>') for every patched byte."""
    patch = json.loads(Path(path).read_text())
    covered: dict[int, str] = {}
    for i, ch in enumerate(patch.get("changes", [])):
        addr = int(str(ch["addr"]), 0)
        new = bytes.fromhex(str(ch.get("new", "")))
        old = bytes.fromhex(str(ch.get("old", "")))
        size = len(new) or len(old) or int(ch.get("size", 0))
        if size == 0:
            raise ValueError(f"change #{i} at {addr:#x} has no new/old bytes and no size")
        start = m.cpu_to_file(addr)
        why = ch.get("why", "")
        for b in range(start, start + size):
            covered[b] = f"change #{i} {addr:#08x} {why}".rstrip()
    return patch, covered


def check_patch_bytes(patch: dict, a: bytes, b: bytes) -> list[str]:
    """Old bytes must match the stock file, new bytes the patched file."""
    issues: list[str] = []
    for i, ch in enumerate(patch.get("changes", [])):
        addr = int(str(ch["addr"]), 0)
        off = m.cpu_to_file(addr)
        for tag, want, buf in (("old", ch.get("old"), a), ("new", ch.get("new"), b)):
            if not want:
                continue
            exp = bytes.fromhex(str(want))
            got = bytes(buf[off:off + len(exp)])
            if got != exp:
                issues.append(f"change #{i} {addr:#08x}: {tag} bytes are {got.hex()}, "
                              f"patch.json says {exp.hex()}")
    return issues


def checksums_ok(data: bytes) -> bool | None:
    """True/False, or None if the descriptor tables themselves are malformed."""
    try:
        for _off, start, end, stored in cs.descriptors(data):
            fs, fe = m.cpu_to_file(start), m.cpu_to_file(end)
            if m.sum16(data[fs:fe + 1]) != stored:
                return False
        return True
    except ValueError:
        return None


def changed_offsets(a: bytes, b: bytes, chunk: int = 0x10000):
    """Yield every file offset where the two images differ (chunked for speed)."""
    for base in range(0, len(a), chunk):
        ca, cb = a[base:base + chunk], b[base:base + chunk]
        if ca == cb:
            continue
        for i, (x, y) in enumerate(zip(ca, cb)):
            if x != y:
                yield base + i


def diff(a: bytes, b: bytes, patch_path: str | None = None):
    if len(a) != len(b):
        raise ValueError(f"size mismatch: {len(a):#x} vs {len(b):#x}")
    desc = descriptor_word_map()
    patch, patched = (None, {})
    issues: list[str] = []
    if patch_path:
        patch, patched = load_patch(patch_path)
        issues += check_patch_bytes(patch, a, b)

    def classify(off: int) -> tuple[str, str]:
        if off in patched:
            return CLS_PATCH, patched[off]
        if off in desc:
            return CLS_DESCRIPTOR, desc[off]
        return CLS_UNEXPECTED, ""

    ranges: list[Range] = []
    cur_start = prev = None
    cur_cls = cur_detail = ""

    def flush():
        nonlocal cur_start
        if cur_start is None:
            return
        ranges.append(Range(cur_start, prev + 1, m.file_to_cpu(cur_start),
                            cur_cls, cur_detail,
                            _hex(bytes(a[cur_start:prev + 1])),
                            _hex(bytes(b[cur_start:prev + 1]))))
        cur_start = None

    for off in changed_offsets(a, b):
        cl, detail = classify(off)
        if cur_start is not None and off == prev + 1 and (cl, detail) == (cur_cls, cur_detail):
            prev = off
            continue
        flush()
        cur_start = prev = off
        cur_cls, cur_detail = cl, detail
    flush()

    counts = {CLS_PATCH: 0, CLS_DESCRIPTOR: 0, CLS_UNEXPECTED: 0}
    bytes_by_cls = dict(counts)
    for r in ranges:
        counts[r.cls] += 1
        bytes_by_cls[r.cls] += r.length

    # Every change the patch promised must actually be present.
    if patch is not None:
        seen = {off for r in ranges if r.cls == CLS_PATCH for off in range(r.file_start, r.file_end)}
        for i, ch in enumerate(patch.get("changes", [])):
            addr = int(str(ch["addr"]), 0)
            off = m.cpu_to_file(addr)
            size = len(bytes.fromhex(str(ch.get("new", "")))) or 1
            if not any(o in seen for o in range(off, off + size)):
                issues.append(f"change #{i} {addr:#08x}: patch.json lists it but the files are identical there")

    report = {
        "ranges": [asdict(r) | {"length": r.length} for r in ranges],
        "counts": counts,
        "bytes": bytes_by_cls,
        "issues": issues,
        "checksums_ok": checksums_ok(b),
        "ok": bytes_by_cls[CLS_UNEXPECTED] == 0 and not issues,
    }
    return ranges, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stock", help="the file the patch was built from")
    ap.add_argument("patched", help="the file to check")
    ap.add_argument("-p", "--patch", help="patch.json describing the intended changes")
    ap.add_argument("--json", metavar="OUT", help="write the JSON report here ('-' for stdout)")
    ap.add_argument("-q", "--quiet", action="store_true", help="print only unexpected ranges and the summary")
    a = ap.parse_args(argv)

    da = m.load_dump(a.stock)
    db = m.load_dump(a.patched)
    ranges, report = diff(da, db, a.patch)
    report["stock"] = {"path": a.stock, "sha256": hashlib.sha256(da).hexdigest()}
    report["patched"] = {"path": a.patched, "sha256": hashlib.sha256(db).hexdigest()}
    report["patch"] = a.patch

    if a.patch:
        base = json.loads(Path(a.patch).read_text()).get("base_sha256")
        if base and not report["stock"]["sha256"].startswith(base.rstrip("….")):
            report["issues"].append(
                f"patch.json base_sha256 {base} does not match {a.stock} "
                f"({report['stock']['sha256']})")
            report["ok"] = False

    if a.json:
        text = json.dumps(report, indent=2)
        if a.json == "-":
            print(text)
        else:
            Path(a.json).write_text(text + "\n")

    if a.json != "-":
        for r in ranges:
            if a.quiet and r.cls != CLS_UNEXPECTED:
                continue
            print(r.line())
        for i in report["issues"]:
            print(f"ISSUE: {i}")
        c, bb = report["counts"], report["bytes"]
        print(f"{len(ranges)} changed range(s): "
              f"{c[CLS_PATCH]} patch ({bb[CLS_PATCH]} B), "
              f"{c[CLS_DESCRIPTOR]} descriptor ({bb[CLS_DESCRIPTOR]} B), "
              f"{c[CLS_UNEXPECTED]} unexpected ({bb[CLS_UNEXPECTED]} B)")
        ck = report["checksums_ok"]
        print(f"block checksums of {a.patched}: "
              + ("ALL OK" if ck else "BAD" if ck is False else "descriptor table malformed"))
        print("RESULT: OK" if report["ok"] else "RESULT: FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
