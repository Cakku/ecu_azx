#!/usr/bin/env python3
"""Build every image the bench day writes, with a self-describing manifest.

Brief H6 (#26/#27, 2026-09-24).  `docs/08_bench_playbook.md` steps 5-7 name
four files.  This script builds them all by *invoking* the existing tools and
Makefiles -- it re-implements none of them -- and proves each one:

    00_stock_resaved.bin       Flash 0 (step 5): the dump through
                               `tools/checksum.py fix`, byte-identical to it
    10_ff_counter_both.bin     Flash 1 (step 6): `make -C patches/ff_counter
                               HOOKS=both apply`
    11_ff_counter_external.bin Flash 1 fallback (step 6, procedure section 4.5):
                               `make -C patches/ff_counter HOOKS=external apply`
    20_ff_fuel_shipped.bin     step 7 item 1: `make -C patches/ff_fuel apply`
                               (ff_persist_enable = 1 by ruling, Carlo
                               2026-09-24: the shipped image IS the bench image)

For each image: `tools/checksum.py verify -q`, `tools/bindiff.py` against the
dump (with the variant's patch.json), and `logging/ecu_sim.py --dump IMAGE
--print-flash-crc` (the value `flash_crc.json` must read back).  Every hash and
CRC is computed here, at build time; none is typed in.  The results go to
`MANIFEST.json` and a short `README.md` in the kit directory.

Guard rails: refuses to run unless data/passat_azx_ori.bin has the canonical
SHA-256; never writes under data/; inside the repository the kit must live
under work/ (gitignored); `make ... apply` regenerates patch.json, so each
descriptor is compared before/after and restored (and the run fails) if the
build moved it; exit status 1 if any verify / bindiff / identity result is not
the expected one, 2 on a refused start.  Nothing here talks to an ECU.

Usage:
    ./.venv/bin/python3 tools/bench_kit.py                 # -> work/bench_kit
    ./.venv/bin/python3 tools/bench_kit.py --out work/kit_2026-09-25
    make bench-kit                                         # the same, from the top
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
DUMP = DATA / "passat_azx_ori.bin"
DUMP_SHA256 = "b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3"
DEFAULT_OUT = REPO / "work" / "bench_kit"
PYTHON = Path(sys.executable)
LLVM_DIR = Path(os.environ.get("LLVM_DIR",
                               "/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64"))

# One row per kit file.  `step` and `s_rows` are docs/08's: the step that
# writes it and the stop-list rows (docs/08 section 0.2) that gate it.
IMAGES = (
    {
        "file": "00_stock_resaved.bin", "kind": "stock", "patch": None,
        "step": "docs/08 step 5 (Flash 0, #26): 5a file, 5b write + read back, 5c then",
        "s_rows": ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S13"],
        "sessions": ["logging/sessions/flash_crc.json",
                     "logging/sessions/adaptation_channels.json"],
    },
    {
        "file": "10_ff_counter_both.bin", "kind": "make", "patch": "ff_counter",
        "make_args": ["HOOKS=both"], "json": "patch.json", "out": "ff_counter.bin",
        "step": "docs/08 step 6 (Flash 1, #27): 6a build, 6b write + read back + 3a/3b/3c, 6c rows A-I",
        "s_rows": ["S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S13"],
        "sessions": ["logging/sessions/flash1_counter.json",
                     "logging/sessions/flash_crc.json"],
    },
    {
        "file": "11_ff_counter_external.bin", "kind": "make", "patch": "ff_counter",
        "make_args": ["HOOKS=external"], "json": "patch.external.json",
        "out": "ff_counter.bin",
        "step": "docs/08 step 6c row D note / ff_counter procedure section 4.5: "
                "ONLY if set B is live (row B) AND the on-chip array cannot be written",
        "s_rows": ["S3", "S4", "S5", "S6", "S7", "S9", "S10", "S13"],
        "sessions": ["logging/sessions/flash1_counter.json",
                     "logging/sessions/flash_crc.json"],
    },
    {
        "file": "20_ff_fuel_shipped.bin", "kind": "make", "patch": "ff_fuel",
        "make_args": [], "json": "patch.json", "out": "ff_fuel.bin",
        "step": "docs/08 step 7 item 1 (ff_fuel first flash, #32), after step 3f (S12)",
        "s_rows": ["S3", "S4", "S5", "S6", "S7", "S8", "S10", "S11", "S12", "S13"],
        "sessions": ["logging/sessions/ff_fuel.json",
                     "logging/sessions/flash_crc.json",
                     "logging/sessions/adaptation_channels.json"],
    },
)


class KitError(Exception):
    """An unexpected result: the kit is not trustworthy."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def check_out_dir(out: Path) -> Path:
    """The kit may not land under data/, nor anywhere tracked in the repo."""
    out = out.resolve()
    if _is_under(out, DATA):
        raise SystemExit(f"refusing: {out} is under data/ (never written)")
    if _is_under(out, REPO) and not _is_under(out, REPO / "work"):
        raise SystemExit(f"refusing: {out} is inside the repository but not "
                         f"under work/ (gitignored); use --out work/<name>")
    return out


def run(cmd, log, cwd=REPO, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True,
                       text=True)
    log.append({"cmd": " ".join(str(c) for c in cmd), "rc": r.returncode})
    if check and r.returncode != 0:
        raise KitError(f"`{' '.join(str(c) for c in cmd)}` exited "
                       f"{r.returncode}\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r


def rel(p: Path) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(REPO)) if _is_under(p, REPO) else str(p)


def git_rev(path: Path) -> dict:
    """The last commit that touched `path`, and whether it is dirty now."""
    try:
        h = subprocess.run(["git", "log", "-1", "--format=%h %cs", "--", str(path)],
                           cwd=REPO, capture_output=True, text=True).stdout.split()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", str(path)],
                               cwd=REPO, capture_output=True, text=True).stdout
        return {"commit": h[0] if h else None, "date": h[1] if len(h) > 1 else None,
                "dirty": bool(dirty.strip())}
    except OSError:                                          # pragma: no cover
        return {"commit": None, "date": None, "dirty": None}


def ffcal001_version(image: Path):
    """FFCAL001's header version, read from the image itself (magic, then >HH
    version/length at +8, patches/ff_fuel/ffcal001.py).  None if absent."""
    data = Path(image).read_bytes()
    i = data.find(b"FFCAL001")
    if i < 0:
        return None
    version, length = struct.unpack_from(">HH", data, i + 8)
    return {"version": version, "length": length, "file_offset": f"{i:#08x}"}


def flash_crc(image: Path, log) -> int:
    r = run([PYTHON, REPO / "logging" / "ecu_sim.py", "--dump", image,
             "--print-flash-crc"], log)
    m = re.match(r"\s*(0x[0-9a-fA-F]{8})\b", r.stdout)
    if not m:
        raise KitError(f"ecu_sim --print-flash-crc gave no CRC: {r.stdout!r}")
    return int(m.group(1), 16)


def verify(image: Path, log) -> str:
    r = run([PYTHON, REPO / "tools" / "checksum.py", "verify", "-q", image],
            log, check=False)
    out = r.stdout.strip()
    if r.returncode != 0 or out != "ALL OK (65 blocks)":
        raise KitError(f"checksum.py verify {image.name}: rc {r.returncode}, {out!r}")
    return out


def bindiff(image: Path, patch_json: Path | None, work: Path, log) -> dict:
    rep = work / (image.name + ".bindiff.json")
    cmd = [PYTHON, REPO / "tools" / "bindiff.py", DUMP, image, "--json", rep, "-q"]
    if patch_json is not None:
        cmd += ["-p", patch_json]
    r = run(cmd, log, check=False)
    report = json.loads(rep.read_text())
    if r.returncode != 0 or not report.get("ok") or report["counts"]["unexpected"]:
        raise KitError(f"bindiff {image.name}: rc {r.returncode}, "
                       f"counts {report.get('counts')}")
    return report


def build_patch(spec: dict, stage: Path, log) -> Path:
    pdir = REPO / "patches" / spec["patch"]
    pjson = pdir / spec["json"]
    before = pjson.read_bytes()
    variant_dir = stage / spec["file"].rsplit(".", 1)[0]
    variant_dir.mkdir(parents=True)
    try:
        run(["make", "-C", pdir, *spec["make_args"], "apply",
             f"WORK={variant_dir}", f"PYTHON={PYTHON}", f"LLVM_DIR={LLVM_DIR}"], log)
    finally:
        after = pjson.read_bytes()
        if after != before:
            pjson.write_bytes(before)
            raise KitError(f"`make apply` regenerated {rel(pjson)} to different "
                           f"content: the committed descriptor is stale for this "
                           f"source/toolchain.  Restored it; run `make "
                           f"{' '.join(spec['make_args'])} gen` there and review")
    return variant_dir / spec["out"]


def ranges_of(report: dict) -> list:
    """bindiff's ranges in hex: file offset, CPU address, length (file_end is
    exclusive in bindiff's JSON), class and what it belongs to."""
    return [{"file": f"{r['file_start']:#08x}-{r['file_end'] - 1:#08x}",
             "cpu": f"{r['cpu_start']:#08x}", "len": r["length"],
             "class": r["cls"], "detail": r.get("detail")}
            for r in report.get("ranges", [])]


def build(out: Path) -> dict:
    log: list = []
    out.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stage_", dir=out))
    entries = []
    try:
        for spec in IMAGES:
            dest = out / spec["file"]
            if spec["kind"] == "stock":
                src = stage / "flash0_src.bin"
                shutil.copyfile(DUMP, src)
                built = stage / "flash0.bin"
                r = run([PYTHON, REPO / "tools" / "checksum.py", "fix", src,
                         "-o", built], log)
                if "0 descriptor(s) updated" not in r.stdout:
                    raise KitError(f"checksum.py fix changed descriptors on the "
                                   f"untouched dump: {r.stdout!r}")
                patch_json = None
            else:
                built = build_patch(spec, stage, log)
                patch_json = REPO / "patches" / spec["patch"] / spec["json"]
            shutil.copyfile(built, dest)
            ok = verify(dest, log)
            report = bindiff(dest, patch_json, stage, log)
            if spec["kind"] == "stock":
                if dest.read_bytes() != DUMP.read_bytes() or report["ranges"]:
                    raise KitError("the stock re-save is not byte-identical to "
                                   "the dump")
            elif not report["ranges"]:
                raise KitError(f"{dest.name} does not differ from the dump")
            desc = json.loads(patch_json.read_text()) if patch_json else {}
            build_sec = desc.get("build", {})
            entry = {
                "file": dest.name,
                "size": dest.stat().st_size,
                "sha256": sha256_file(dest),
                "patch": None if not patch_json else {
                    "id": desc.get("name"),
                    "variant": desc.get("variant", "default"),
                    "descriptor": rel(patch_json),
                    "make": " ".join(["make", "-C", f"patches/{spec['patch']}",
                                      *spec["make_args"], "apply"]),
                    "blob_sha256": build_sec.get("blob_sha256"),
                    "blob_size": build_sec.get("blob_size"),
                    "source_rev": git_rev(REPO / "patches" / spec["patch"]),
                    "ram_status": desc.get("ram_status"),
                    "onchip_edits": sum(1 for c in desc.get("changes", [])
                                        if c.get("onchip_edit")),
                },
                "ffcal001": ffcal001_version(dest),
                "checksum_verify": ok,
                "bindiff": {"counts": report["counts"], "bytes": report["bytes"],
                            "ranges": ranges_of(report)},
                "expected_flash_crc": f"{flash_crc(dest, log):#010x}",
                "docs08_step": spec["step"],
                "s_rows": spec["s_rows"],
                "sessions": spec["sessions"],
            }
            entries.append(entry)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    manifest = {
        "generated_by": "tools/bench_kit.py (brief H6)",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "repo_head": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                    cwd=REPO, capture_output=True,
                                    text=True).stdout.strip() or None,
        "dump": {"path": rel(DUMP), "sha256": DUMP_SHA256,
                 "size": DUMP.stat().st_size},
        "images": entries,
        "commands": log,
        "note": "Every hash and CRC here was computed at build time. Nothing in "
                "this kit has been flashed; S3 (ram_status 'static') still "
                "forbids writing any image until docs/08 step 4 has passed.",
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "README.md").write_text(kit_readme(manifest))
    return manifest


READBACK = {
    "00_stock_resaved.bin": [
        "./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin   # must print 0 changed ranges",
        "./.venv/bin/python3 tools/checksum.py verify -q work/readback.bin",
    ],
    "10_ff_counter_both.bin": [
        "./.venv/bin/python3 tools/bindiff.py KIT/10_ff_counter_both.bin work/readback.bin   # 0 ranges = what was written",
        "./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin -p patches/ff_counter/patch.json",
        "./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x22E940 --addr 0x432940 --len 4   # on-chip word",
        "./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x12067C --addr 0x12067C --len 4",
    ],
    "11_ff_counter_external.bin": [
        "./.venv/bin/python3 tools/bindiff.py KIT/11_ff_counter_external.bin work/readback.bin",
        "./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin -p patches/ff_counter/patch.external.json",
        "./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x12067C --addr 0x12067C --len 4",
    ],
    "20_ff_fuel_shipped.bin": [
        "./.venv/bin/python3 tools/bindiff.py KIT/20_ff_fuel_shipped.bin work/readback.bin",
        "./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin -p patches/ff_fuel/patch.json",
        "# then all seven on-chip hook words: patches/ff_fuel/test/procedure.md section 1",
    ],
}


def kit_readme(manifest: dict) -> str:
    lines = [
        "# Bench kit",
        "",
        f"Built by `tools/bench_kit.py` at {manifest['generated_at']} from "
        f"`{manifest['repo_head']}`; dump `{manifest['dump']['sha256']}`. "
        "Every value below was computed at build time; `MANIFEST.json` has "
        "the full record (changed ranges, commands, source revisions).",
        "",
        "**Nothing here has been flashed. S3 still applies: while any "
        "`ram_status` is `static`, write nothing (docs/08 step 4 first).** "
        "`docs/08_bench_playbook.md` is the procedure; this file is only its "
        "index of files.",
        "",
        "| File | Step | Gates | SHA-256 | Expected flash CRC | Changed ranges |",
        "|---|---|---|---|---|---|",
    ]
    for e in manifest["images"]:
        c = e["bindiff"]["counts"]
        lines.append(
            f"| `{e['file']}` | {e['docs08_step']} | {', '.join(e['s_rows'])} | "
            f"`{e['sha256']}` | `{e['expected_flash_crc']}` | "
            f"{c['patch']} patch, {c['descriptor']} descriptor, "
            f"{c['unexpected']} unexpected |")
    lines += ["", "## Read-back, per file", "",
              "Read the whole ECU back into `work/readback.bin` (docs/07 "
              "section 3.3), then (KIT = this directory):", ""]
    for e in manifest["images"]:
        lines += [f"### `{e['file']}`", "", "```bash", *READBACK[e["file"]],
                  "```", "",
                  f"Power-cycle, then the flash CRC (docs/08 step 2d's command, "
                  f"session `logging/sessions/flash_crc.json`): it must publish "
                  f"**{e['expected_flash_crc']}**.", "",
                  "Sessions to load: " + ", ".join(f"`{s}`" for s in e["sessions"])
                  + ".", ""]
        if e["ffcal001"]:
            lines += [f"FFCAL001 version {e['ffcal001']['version']}, "
                      f"length {e['ffcal001']['length']:#06x}.", ""]
    return "\n".join(lines) + "\n"


def summary(manifest: dict) -> str:
    rows = [f"bench kit: {len(manifest['images'])} image(s), dump "
            f"{manifest['dump']['sha256'][:16]}..., head {manifest['repo_head']}"]
    for e in manifest["images"]:
        c = e["bindiff"]["counts"]
        cal = f" FFCAL001 v{e['ffcal001']['version']}" if e["ffcal001"] else ""
        rows.append(f"  {e['file']:<28} {e['sha256'][:16]}...  crc "
                    f"{e['expected_flash_crc']}  {c['patch']}p/{c['descriptor']}d/"
                    f"{c['unexpected']}u  {e['checksum_verify']}{cal}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="kit directory (default work/bench_kit; inside the "
                         "repo it must be under work/, never under data/)")
    args = ap.parse_args(argv)

    if not DUMP.is_file():
        print(f"refusing: {DUMP} not present", file=sys.stderr)
        return 2
    got = sha256_file(DUMP)
    if got != DUMP_SHA256:
        print(f"refusing: {rel(DUMP)} SHA-256 is {got}, not the canonical "
              f"{DUMP_SHA256} (docs/08 S1)", file=sys.stderr)
        return 2
    out = check_out_dir(args.out)
    if not (LLVM_DIR / "bin" / "clang").is_file():
        print(f"refusing: no PowerPC cross compiler at {LLVM_DIR} "
              f"(docs/03_tooling.md section 3.1)", file=sys.stderr)
        return 2
    try:
        manifest = build(out)
    except KitError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    if sha256_file(DUMP) != DUMP_SHA256:                     # pragma: no cover
        print("FAIL: the dump changed during the build!", file=sys.stderr)
        return 1
    print(summary(manifest))
    print(f"wrote {rel(out)}/MANIFEST.json and README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
