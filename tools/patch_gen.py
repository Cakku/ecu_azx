#!/usr/bin/env python3
"""Generate the `changes` list of a patch.json from the built blob and symbols.

`changes` is *generated, never hand-edited*: it is the one part of a patch that
has to agree with the compiler's output byte for byte.  This tool reads the
`build` section of `patches/<name>/patch.json` (docs/06_patch_pipeline.md
section 1), takes the blob and the linker's `.sym` file, resolves every hook's
target symbol, encodes the branch word and rewrites `changes`:

    {"build": {"flash": "0x150000", "ram": "0x7FFB00", "ram_size": 256,
               "blob": "build/ff_counter.bin", "sym": "build/ff_counter.sym",
               "hooks": [{"site": "0x12067C", "kind": "bl",
                          "target": "ff_counter_hook", "old": "4bffe9b1",
                          "why": "..."}],
               "data": [{"addr": "0x5E2510", "file": "build/ffcal001.bin",
                         "expect_blank": true, "why": "..."},
                        {"addr": "0x2BD8C", "bytes": "000000ec",
                         "old": "000007ff", "why": "..."},
                        {"addr": "0xA78A8", "u32_syms": ["ff_diag_e_pct"],
                         "old": "00038ec4", "why": "..."}]}}

A `data` entry is a flat byte range that is not code: a new calibration block
(`file`, built by the patch's own generator), a small table edit (`bytes`), or
a list of pointers into our own blob (`u32_syms`, resolved from the linker's
`.sym` exactly as a hook target is, so a table of handler pointers cannot go
stale when the code moves).  Its `old` is read from the stock image;
`expect_blank` additionally asserts that the stock bytes are all 0xFF, and an
explicit `old` is compared against what is really there.  Unlock flags
(`calibration_edit`, `onchip_edit`) are copied through to the change so
`tools/patch_apply.py` sees them.

Branch encoding, I-form (docs/06, PowerPC UISA):

    word = 0x48000000 | (LI & 0x03FFFFFC) | (AA << 1) | LK
    LI   = target - site   for a relative branch (`b`/`bl`, AA = 0)
    LI   = target          for an absolute branch (`ba`/`bla`, AA = 1)

reach +-32 MB, LI always a multiple of 4.  Both are checked here.

The blob is added as one change whose `old` is all 0xFF, and the stock image is
read to prove that those bytes really are 0xFF before anything is written.

Usage:
    python3 tools/patch_gen.py patches/ff_counter
    python3 tools/patch_gen.py patches/ff_counter --stock data/passat_azx_ori.bin
    python3 tools/patch_gen.py patches/ff_counter --dry-run --json -
    python3 tools/patch_gen.py --check-size NAME --blob B.bin --sym B.sym --ram-size 64
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import med9lib as m  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_STOCK = REPO / "data" / "passat_azx_ori.bin"

# Free flash for patch code (docs/06_patch_pipeline.md section 3).
FREE_FLASH_START = 0x150000
FREE_FLASH_END = 0x1B0000          # exclusive
# On-chip + external SRAM (docs/02_memory_map.md section 3).
RAM_START = 0x7F8000
RAM_END = 0x808000                 # exclusive

# Per-change flags that unlock a guarded region in tools/patch_apply.py; they
# are declared on a hook or a data entry and copied into the change verbatim.
UNLOCK_FLAGS = ("calibration_edit", "onchip_edit")

BRANCH_KINDS = {                   # name -> (absolute, link)
    "b": (False, False),
    "bl": (False, True),
    "ba": (True, False),
    "bla": (True, True),
}

_SYM_RE = re.compile(r"^([0-9a-fA-F]+)\s+(\S)\s+(\S+)\s*$")


class PatchError(RuntimeError):
    pass


# ----------------------------------------------------------------- helpers --
def parse_sym(path: Path) -> dict[str, int]:
    """`llvm-nm -n` / `powerpc-eabi-nm -n` output -> {name: value}."""
    out: dict[str, int] = {}
    for line in Path(path).read_text().splitlines():
        mo = _SYM_RE.match(line)
        if mo:
            out[mo.group(3)] = int(mo.group(1), 16)
    return out


def encode_branch(site: int, target: int, kind: str) -> int:
    """I-form branch word from a hook site to a target."""
    try:
        absolute, link = BRANCH_KINDS[kind]
    except KeyError:
        raise PatchError(f"unknown branch kind {kind!r}; use one of {sorted(BRANCH_KINDS)}")
    li = target if absolute else target - site
    if li % 4:
        raise PatchError(f"branch displacement {li:#x} is not a multiple of 4")
    if not -0x2000000 <= li < 0x2000000:
        raise PatchError(f"branch from {site:#x} to {target:#x} is out of the "
                         f"+-32 MB reach (LI = {li:#x})")
    return 0x48000000 | (li & 0x03FFFFFC) | (0x2 if absolute else 0) | (1 if link else 0)


def decode_branch(word: int, site: int) -> tuple[str, int]:
    """(kind, target) of an I-form branch word, for reporting."""
    if word >> 26 != 18:
        raise PatchError(f"{word:#010x} at {site:#x} is not an I-form branch")
    li = word & 0x03FFFFFC
    if li & 0x02000000:
        li -= 0x04000000
    absolute, link = bool(word & 2), bool(word & 1)
    kind = {(False, False): "b", (False, True): "bl",
            (True, False): "ba", (True, True): "bla"}[(absolute, link)]
    return kind, (li if absolute else site + li) & 0xFFFFFFFF


def _int(value) -> int:
    return int(str(value), 0)


# -------------------------------------------------------------- generation --
def generate(patch_dir: Path, stock_path: Path = DEFAULT_STOCK) -> tuple[dict, list[str]]:
    """Return (patch dict with a fresh `changes`, human-readable notes)."""
    patch_file = patch_dir / "patch.json" if patch_dir.is_dir() else patch_dir
    patch_dir = patch_file.parent
    patch = json.loads(patch_file.read_text())
    build = patch.get("build")
    if not build:
        raise PatchError(f"{patch_file} has no 'build' section (docs/06 section 1)")

    notes: list[str] = []
    stock = m.load_dump(str(stock_path))
    stock_sha = hashlib.sha256(bytes(stock)).hexdigest()
    want_sha = str(patch.get("base_sha256", ""))
    if want_sha and not stock_sha.startswith(want_sha.rstrip("….")):
        raise PatchError(f"base_sha256 {want_sha} does not match {stock_path} ({stock_sha})")

    flash = _int(build["flash"])
    blob = (patch_dir / build["blob"]).read_bytes()
    sym_path = patch_dir / build.get("sym", build["blob"].replace(".bin", ".sym"))
    syms = parse_sym(sym_path)

    # --- placement -----------------------------------------------------
    if not FREE_FLASH_START <= flash < FREE_FLASH_END:
        raise PatchError(f"flash {flash:#x} is outside the free area "
                         f"{FREE_FLASH_START:#x}-{FREE_FLASH_END - 1:#x}")
    if flash + len(blob) > FREE_FLASH_END:
        raise PatchError(f"the blob ({len(blob)} B at {flash:#x}) runs past "
                         f"{FREE_FLASH_END - 1:#x}")
    ram, ram_size = _int(build["ram"]), _int(build.get("ram_size", 0))
    if not RAM_START <= ram < RAM_END or ram + ram_size > RAM_END:
        raise PatchError(f"ram {ram:#x}+{ram_size:#x} is outside the SRAM "
                         f"{RAM_START:#x}-{RAM_END - 1:#x}")
    used_ram = syms.get("__bss_end", ram) - syms.get("__bss_start", ram)
    if used_ram > ram_size:
        raise PatchError(f".bss needs {used_ram} B but ram_size is {ram_size} B")
    linked_size = syms.get("__patch_flash_size")
    if linked_size is not None and linked_size != len(blob):
        raise PatchError(f"the blob is {len(blob)} B but the link says "
                         f"__patch_flash_size = {linked_size}; an unexpected "
                         f"section landed in the binary")
    notes.append(f"blob {len(blob)} B at {flash:#08x}, .bss {used_ram} B of "
                 f"{ram_size} B at {ram:#08x}")

    # --- the blob itself ------------------------------------------------
    blob_off = m.cpu_to_file(flash)
    stock_there = bytes(stock[blob_off:blob_off + len(blob)])
    if set(stock_there) != {0xFF}:
        first = next(i for i, b in enumerate(stock_there) if b != 0xFF)
        raise PatchError(f"the stock image is not blank at {flash + first:#08x} "
                         f"(found {stock_there[first]:#04x}); refusing to "
                         f"overwrite code or data")
    changes = [{
        "addr": f"{flash:#08x}",
        "kind": "blob",
        "old": "ff" * len(blob),
        "new": blob.hex(),
        "why": f"{patch['name']} code and constants ({len(blob)} B of free flash)",
    }]

    # --- one change per hook --------------------------------------------
    resolved: dict[str, str] = {}
    for i, hook in enumerate(build.get("hooks", [])):
        site = _int(hook["site"])
        kind = hook.get("kind", "bl")
        target_name = hook["target"]
        if target_name not in syms:
            raise PatchError(f"hook #{i}: {sym_path} has no symbol {target_name!r}")
        target = syms[target_name]
        if not flash <= target < flash + len(blob):
            raise PatchError(f"hook #{i}: {target_name} resolves to {target:#x}, "
                             f"which is outside the blob")
        word = encode_branch(site, target, kind)

        site_off = m.cpu_to_file(site)
        old = bytes(stock[site_off:site_off + 4])
        want_old = bytes.fromhex(str(hook["old"]))
        if old != want_old:
            raise PatchError(f"hook #{i} at {site:#08x}: the stock word is "
                             f"{old.hex()}, patch.json says {want_old.hex()}")
        if word == int.from_bytes(old, "big"):
            raise PatchError(f"hook #{i} at {site:#08x}: the new word equals the old one")
        old_kind, old_target = decode_branch(int.from_bytes(old, "big"), site)
        resolved[target_name] = f"{target:#08x}"
        notes.append(f"hook {site:#08x}: {old_kind} {old_target:#08x} -> "
                     f"{kind} {target:#08x} ({target_name}), word "
                     f"{old.hex()} -> {word:08x}")
        change = {
            "addr": f"{site:#08x}",
            "kind": "hook",
            "old": old.hex(),
            "new": f"{word:08x}",
            "why": hook.get("why") or
                   f"{old_kind} {old_target:#08x} -> {target_name} trampoline",
        }
        for flag in UNLOCK_FLAGS:
            if hook.get(flag):
                change[flag] = True
        changes.append(change)

    # --- one change per data block ---------------------------------------
    for i, item in enumerate(build.get("data", [])):
        addr = _int(item["addr"])
        if "file" in item:
            new = (patch_dir / item["file"]).read_bytes()
        elif "bytes" in item:
            new = bytes.fromhex(str(item["bytes"]))
        elif "u32_syms" in item:
            # A table of pointers into our own blob: resolved from the linker's
            # symbols, never written by hand, so it follows the code.
            words = bytearray()
            for name in item["u32_syms"]:
                if name not in syms:
                    raise PatchError(f"data #{i}: {sym_path} has no symbol {name!r}")
                value = syms[name]
                if not flash <= value < flash + len(blob):
                    raise PatchError(f"data #{i}: {name} resolves to {value:#x}, "
                                     f"which is outside the blob")
                if value % 4:
                    raise PatchError(f"data #{i}: {name} ({value:#x}) is not "
                                     f"4-byte aligned; the CPU branches to it")
                resolved[name] = f"{value:#08x}"
                words += value.to_bytes(4, "big")
            new = bytes(words)
        else:
            raise PatchError(f"data #{i} at {addr:#08x} has no 'file', 'bytes' "
                             f"or 'u32_syms'")
        if not new:
            raise PatchError(f"data #{i} at {addr:#08x} is empty")
        off = m.cpu_to_file(addr)
        old = bytes(stock[off:off + len(new)])
        if len(old) != len(new):
            raise PatchError(f"data #{i} at {addr:#08x}+{len(new):#x} runs past "
                             f"the end of the dump")
        if item.get("expect_blank") and set(old) != {0xFF}:
            first = next(j for j, b in enumerate(old) if b != 0xFF)
            raise PatchError(f"data #{i}: the stock image is not blank at "
                             f"{addr + first:#08x} (found {old[first]:#04x})")
        want_old = item.get("old")
        if want_old is not None and old != bytes.fromhex(str(want_old)):
            raise PatchError(f"data #{i} at {addr:#08x}: the stock bytes are "
                             f"{old.hex()}, patch.json says {want_old}")
        if old == new:
            raise PatchError(f"data #{i} at {addr:#08x} writes what is already there")
        change = {
            "addr": f"{addr:#08x}",
            "kind": "data",
            "old": old.hex(),
            "new": new.hex(),
            "why": item.get("why") or f"{len(new)} B of patch data",
        }
        for flag in UNLOCK_FLAGS:
            if item.get(flag):
                change[flag] = True
        changes.append(change)
        origin = ""
        if "file" in item:
            origin = f" from {item['file']}"
        elif "u32_syms" in item:
            origin = " = " + ", ".join(f"{n} {syms[n]:#08x}" for n in item["u32_syms"])
        notes.append(f"data {addr:#08x}: {len(new)} B{origin}")

    # --- RAM objects worth recording ------------------------------------
    # A patch's .bss is more than its documented state block once it owns
    # anything a stock API keeps a pointer to.  Naming those objects here puts
    # their addresses in `build.symbols`, where a test or a logger session can
    # read them instead of hard-coding an offset the next link might move.
    for name in build.get("ram_symbols", []):
        if name not in syms:
            raise PatchError(f"ram_symbols: {sym_path} has no symbol {name!r}")
        value = syms[name]
        if not ram <= value < ram + ram_size:
            raise PatchError(f"ram_symbols: {name} is at {value:#x}, outside "
                             f"the patch RAM block {ram:#x}+{ram_size:#x}")
        resolved[name] = f"{value:#08x}"
        notes.append(f"ram symbol {name} at {value:#08x}")

    build["blob_size"] = len(blob)
    build["blob_sha256"] = hashlib.sha256(blob).hexdigest()
    build["bss_size"] = used_ram
    build["symbols"] = resolved
    patch["changes"] = changes
    patch["generated_by"] = "tools/patch_gen.py"
    return patch, notes


# ------------------------------------------------------------- check-size ---
def check_size(name: str, blob: Path, sym: Path, ram_size: int) -> int:
    """`make check` helper: the blob is exactly the linked flash size."""
    data = Path(blob).read_bytes()
    syms = parse_sym(Path(sym))
    linked = syms.get("__patch_flash_size")
    used_ram = syms.get("__bss_end", 0) - syms.get("__bss_start", 0)
    if linked is None:
        print(f"FAIL: {sym} has no __patch_flash_size (wrong linker script?)",
              file=sys.stderr)
        return 1
    if linked != len(data):
        print(f"FAIL: {name}: blob is {len(data)} B but the link says {linked} B "
              f"- an unexpected section landed in the binary", file=sys.stderr)
        return 1
    if used_ram > ram_size:
        print(f"FAIL: {name}: .bss is {used_ram} B, PATCH_RAM_SIZE is {ram_size} B",
              file=sys.stderr)
        return 1
    print(f"OK: {name}: blob {len(data)} B == linked flash size, "
          f".bss {used_ram} B <= {ram_size} B")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patch", nargs="?", help="patches/<name>/ or its patch.json")
    ap.add_argument("--stock", default=str(DEFAULT_STOCK))
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="do not rewrite patch.json")
    ap.add_argument("--json", metavar="OUT",
                    help="write the generated patch.json here ('-' for stdout)")
    ap.add_argument("--check-size", metavar="NAME",
                    help="make-check helper, needs --blob/--sym/--ram-size")
    ap.add_argument("--blob")
    ap.add_argument("--sym")
    ap.add_argument("--ram-size", type=lambda s: int(s, 0), default=0)
    a = ap.parse_args(argv)

    if a.check_size:
        return check_size(a.check_size, a.blob, a.sym, a.ram_size)
    if not a.patch:
        ap.error("give a patch directory (or --check-size)")

    try:
        patch, notes = generate(Path(a.patch), Path(a.stock))
    except (PatchError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"patch_gen: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(patch, indent=2) + "\n"
    if a.json == "-":
        sys.stdout.write(text)
    else:
        out = Path(a.json) if a.json else (
            Path(a.patch) / "patch.json" if Path(a.patch).is_dir() else Path(a.patch))
        if a.dry_run:
            print(f"(dry run: {out} not written)")
        else:
            out.write_text(text)
            print(f"wrote {out}: {len(patch['changes'])} change(s)")
    for n in notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
