#!/usr/bin/env python3
"""Apply a patch to a copy of the dump, fix the checksums and prove the result.

The one place a MED9.1.1 image is ever modified (docs/06_patch_pipeline.md
section 5).  It never touches its input, never writes to `data/`, and writes
nothing at all unless every check below passes:

 1. the stock file's SHA-256 matches `base_sha256` in patch.json;
 2. no change lands in a forbidden region (see FORBIDDEN) unless it carries
    that region's explicit unlock flag - `calibration_edit` for the stock
    calibration, `onchip_edit` for the on-chip flash 0x404000-0x47FFFF;
 3. every change's `old` bytes are really there;
 4. the `new` bytes are written to a copy;
 5. `checksum.fix` rewrites the affected block descriptors and
    `checksum.verify` then prints ALL OK (65 blocks);
 6. the identification block 0x1CEE20-0x1CEE6F is byte-identical;
 7. `bindiff.diff` classifies every changed byte as patch or descriptor - one
    unexpected byte and nothing is written.

Outputs (next to `-o`): `<name>.bin`, `<name>.diff.json`, `<name>.sha256`.

Usage:
    python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter \\
            -o work/ff_counter.bin
    python3 tools/patch_apply.py STOCK patches/ff_counter -o OUT --dry-run
    python3 tools/patch_apply.py STOCK patches/ff_counter -o OUT --json
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bindiff  # noqa: E402
import checksum as cs  # noqa: E402
import med9lib as m  # noqa: E402

# CPU ranges no patch may touch (docs/06_patch_pipeline.md section 3).
# The last field is the per-change flag that unlocks the range, or None when
# nothing unlocks it.  A flag unlocks exactly one range: `calibration_edit`
# never opens the on-chip flash and `onchip_edit` never opens the calibration.
FORBIDDEN = (
    (0x000000, 0x010000, "boot block and immobiliser pairing", None),
    # The firmware's own OBD programming service refuses these two ranges
    # outright (re/findings/flash_programming.md section 3, brief E6): a
    # change there could only ever be written by BDM, which is a different
    # risk class, so the apply tool refuses it too (integration, 2026-09-17).
    (0x010000, 0x020000, "exception tables and boot module, which the OBD "
                         "programming route refuses", None),
    (0x080000, 0x0A0000, "resident flash-programming module, which the OBD "
                         "route aliases to the calibration and never programs", None),
    (0x1C0000, 0x1E0000, "stock calibration", "calibration_edit"),
    # The first 16 KB of the on-chip flash is genuinely absent from the dump
    # (docs/02_memory_map.md section 2), so nothing can be written there.
    (0x400000, 0x404000, "16 KB of on-chip flash that is not in our read", None),
    # 0x404000-0x47FFFF *is* in our read (file 0x200000+) and is covered by the
    # code descriptor table at file 0x0A0000, so a hook there re-checksums
    # correctly.  It still needs an explicit flag, because it is the region
    # that carries the KWP / flash-programming services and because whether
    # KESSv2 writes it has not been demonstrated (docs/06 section 1).
    (0x404000, 0x480000, "on-chip flash", "onchip_edit"),
)
# Ranges whose unlock flag leaves a warning on every apply, even when used
# correctly: `flag -> text`.
UNLOCK_WARNINGS = {
    "onchip_edit": "writes the MPC561 on-chip flash (0x404000-0x47FFFF). The "
                   "block checksums are handled and the firmware's own OBD "
                   "programming route whitelists the range (re/findings/"
                   "flash_programming.md), but a KESSv2 write of it has not "
                   "been demonstrated: read the image back and compare "
                   "before trusting it.",
}
# Never changes, whatever the flags say.
IDENT_START, IDENT_END = 0x1CEE20, 0x1CEE70      # CPU, end exclusive

# Every ram_status but "verified" blocks flashing; the text says why
# (docs/06_patch_pipeline.md section 1, re/findings/ram.md, issue #23).
RAM_STATUS_WARNINGS = {
    "static": "is VERIFIED-STATIC only (re/findings/ram.md): no instruction "
              "references it, but the runtime snapshots of issue #23 are still pending.",
    "placeholder": "has NOT been proven unused (issue #23).",
    "example": "is an example address; this patch is a template, never a flash candidate.",
}


class ApplyError(RuntimeError):
    pass


def _int(value) -> int:
    return int(str(value), 0)


def canonical_cpu(addr: int) -> int:
    """CPU address folded to its canonical (low / on-chip) alias.

    The calibration is reachable as 0x1Cxxxx and as 0x5Cxxxx; a guard that only
    knew one of them would be trivial to walk around.

    An address that no file offset backs (the missing 16 KB of on-chip flash,
    RAM, the peripherals) has no canonical alias; it is returned unchanged so
    that `check_region` can refuse it with a readable message instead of a
    ValueError from med9lib.
    """
    try:
        return m.file_to_cpu(m.cpu_to_file(addr))
    except ValueError:
        return addr


def check_region(addr: int, size: int, change: dict | bool | None = None) -> list[str]:
    """Refuse a change in a forbidden range; return the warnings it earns.

    `change` is the change dict (its unlock flags are read from it).  A bare
    bool is accepted for backwards compatibility and means `calibration_edit`.
    """
    if isinstance(change, bool) or change is None:
        change = {"calibration_edit": bool(change)}
    lo = canonical_cpu(addr)
    hi = lo + size                                   # exclusive
    warnings: list[str] = []
    for start, end, what, flag in FORBIDDEN:
        if lo < end and hi > start:
            if flag and change.get(flag):
                if flag in UNLOCK_WARNINGS:
                    warnings.append(f"change at {addr:#08x}+{size:#x} "
                                    + UNLOCK_WARNINGS[flag])
                continue
            extra = "" if flag else " (not unlockable)"
            raise ApplyError(
                f"change at {addr:#08x}+{size:#x} lands in {start:#08x}-{end - 1:#08x}, "
                f"the {what}{extra}"
                + (f"; add \"{flag}\": true to the change if that is "
                   "really intended" if flag else ""))
    if lo < IDENT_END and hi > IDENT_START:
        raise ApplyError(f"change at {addr:#08x}+{size:#x} touches the "
                         f"identification block {IDENT_START:#08x}-{IDENT_END - 1:#08x}")
    return warnings


def apply_patch(stock_path: Path, patch_dir: Path) -> tuple[bytearray, dict, list[str]]:
    """Return (patched image, report, warnings).  Raises ApplyError on refusal."""
    patch_file = patch_dir / "patch.json" if patch_dir.is_dir() else patch_dir
    patch = json.loads(patch_file.read_text())
    name = patch.get("name") or patch_file.parent.name
    warnings: list[str] = []

    stock = m.load_dump(str(stock_path))
    stock_sha = hashlib.sha256(bytes(stock)).hexdigest()
    want = str(patch.get("base_sha256", ""))
    if not want:
        raise ApplyError(f"{patch_file} has no base_sha256")
    if not stock_sha.startswith(want.rstrip("….")):
        raise ApplyError(f"base_sha256 mismatch: patch.json says {want}, "
                         f"{stock_path} is {stock_sha}")

    ram_status = str(patch.get("ram_status", "missing"))
    if ram_status != "verified":
        why = RAM_STATUS_WARNINGS.get(ram_status, "has an unknown ram_status value.")
        warnings.append(
            f"{name}: \"ram_status\": \"{ram_status}\" - the RAM block at "
            f"{patch.get('build', {}).get('ram', '?')} {why} Do not flash this image.")
    for missing in patch.get("requires", []):
        warnings.append(f"{name}: requires {missing!r}; this tool does not check that")

    changes = patch.get("changes")
    if not changes:
        raise ApplyError(f"{patch_file} has no changes; run tools/patch_gen.py first")

    data = bytearray(stock)
    for i, ch in enumerate(changes):
        addr = _int(ch["addr"])
        new = bytes.fromhex(str(ch["new"]))
        old = bytes.fromhex(str(ch.get("old", "")))
        if not new:
            raise ApplyError(f"change #{i} at {addr:#08x} has no new bytes")
        if old and len(old) != len(new):
            raise ApplyError(f"change #{i} at {addr:#08x}: old is {len(old)} B "
                             f"but new is {len(new)} B")
        warnings.extend(check_region(addr, len(new), ch))
        off = m.cpu_to_file(addr)
        if old:
            there = bytes(data[off:off + len(old)])
            if there != old:
                raise ApplyError(
                    f"change #{i} at {addr:#08x} (file {off:#08x}): expected "
                    f"{old.hex()}, found {there.hex()} - wrong base image, or "
                    f"the patch is already applied")
        data[off:off + len(new)] = new

    # Checksums: fix, then verify.  Both are chatty, so their output is captured
    # and only reported when something goes wrong.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cs.fix(data)
        ok = cs.verify(data, quiet=True)
    if not ok:
        raise ApplyError("checksum verify failed after fix:\n" + buf.getvalue())

    ident_off = m.cpu_to_file(IDENT_START)
    ident_len = IDENT_END - IDENT_START
    if bytes(data[ident_off:ident_off + ident_len]) != bytes(stock[ident_off:ident_off + ident_len]):
        raise ApplyError(f"the identification block {IDENT_START:#08x}-"
                         f"{IDENT_END - 1:#08x} changed")

    _ranges, report = bindiff.diff(stock, data, str(patch_file))
    report["stock"] = {"path": str(stock_path), "sha256": stock_sha}
    report["patched"] = {"sha256": hashlib.sha256(bytes(data)).hexdigest()}
    report["patch"] = str(patch_file)
    report["name"] = name
    report["warnings"] = warnings
    if not report["ok"]:
        lines = [r.line() for r in _ranges if r.cls == bindiff.CLS_UNEXPECTED]
        raise ApplyError("bindiff refused the result:\n  "
                         + "\n  ".join(report["issues"] + lines))
    if report["checksums_ok"] is not True:
        raise ApplyError(f"bindiff reports checksums_ok = {report['checksums_ok']}")
    return data, report, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stock", help="the unmodified dump the patch was built from")
    ap.add_argument("patch", help="patches/<name>/ or its patch.json")
    ap.add_argument("-o", "--output", required=True, help="e.g. work/ff_counter.bin")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="run every check, write nothing")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON instead of text")
    a = ap.parse_args(argv)

    try:
        data, report, warnings = apply_patch(Path(a.stock), Path(a.patch))
    except (ApplyError, ValueError, KeyError, FileNotFoundError) as exc:
        if a.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"patch_apply: REFUSED\n{exc}", file=sys.stderr)
        return 1

    out = Path(a.output)
    written: list[str] = []
    if not a.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(data))
        diff_path = out.with_suffix(".diff.json")
        diff_path.write_text(json.dumps(report, indent=2) + "\n")
        sha_path = out.with_suffix(".sha256")
        sha_path.write_text(f"{report['patched']['sha256']}  {out.name}\n")
        written = [str(out), str(diff_path), str(sha_path)]
    report["written"] = written
    report["dry_run"] = a.dry_run

    if a.json:
        print(json.dumps(report, indent=2))
        return 0

    c, bb = report["counts"], report["bytes"]
    print(f"{report['name']}: {c['patch']} patch range(s) ({bb['patch']} B), "
          f"{c['descriptor']} descriptor range(s) ({bb['descriptor']} B), "
          f"0 unexpected")
    print(f"checksums: ALL OK (65 blocks); identification block unchanged")
    print(f"sha256: {report['patched']['sha256']}")
    for w in warnings:
        print(f"WARNING: {w}")
    if a.dry_run:
        print("(dry run: nothing written)")
    else:
        for f in written:
            print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
