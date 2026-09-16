#!/usr/bin/env python3
"""Build the FFCAL001 calibration block for patches/ff_fuel, and its descriptors.

FFCAL001 lives at CPU **0x5E2510**, inside the 0x5E0000-0x5EFFFF Bosch checksum
block and in the 0xFF area docs/06_patch_pipeline.md section 3 reserves for new
calibration.  `patch.json` references the binary through `build.data`, so
`make gen` picks up whatever this script last wrote and `tools/patch_apply.py`
re-checksums the block.

Two outputs:

  build/ffcal001.bin    the 232 bytes that go into the image
  ffcal001_rows.csv     descriptor rows in the exact column format of
                        `re/calibration_draft.csv`, for the integrator to
                        append at merge time (brief D3 owns that file, so D1
                        must not write it)

Units.  Every time constant is stored in **physical** units - ms, s, %/s - and
the patch converts to raster activations with `ff_tick_ms`, itself a
calibration value.  That is the whole answer to the trap C4 found (issue #44):
the rasters are 10x faster than their names, `K ~ 1/32 per 100 ms` becomes
`K ~ 1/320 per 10 ms` and `2 %/s` becomes `0.02 %/activation`, and none of that
has to be re-derived by hand when the hook moves.  The filter gain is expressed
as the first-order **time constant** `ff_filter_tau_ms` (3000 ms), from which
the patch computes `K = ff_tick_ms / ff_filter_tau_ms = 1/300` per activation;
the slew limit is `ff_slew_pct_s` (2 %/s), from which the patch computes
`slew = ff_slew_pct_s * 16 * 1024 * ff_tick_ms / 1000` = 327 units of
1/16384 % per activation.  **The conversion is in the patch, not here**, so the
same block works at any raster period.

The F curve is the docs/05 section 3.3 mass factor, evaluated by
`emu.models.flexfuel.fuel_mass_factor` so the patch, the model and the
calibration cannot drift apart.  F(0) = 1024 exactly, which is what makes E0
bit-identical to stock.

Usage:
    ./.venv/bin/python3 patches/ff_fuel/ffcal001.py                # build both
    ./.venv/bin/python3 patches/ff_fuel/ffcal001.py --print        # human dump
    ./.venv/bin/python3 patches/ff_fuel/ffcal001.py --set ff_mode=0 \\
        -o build/ffcal001_mode0.bin --no-rows                      # a mode-0 file
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
from emu.models.flexfuel import CURVE_N, f_curve_from_formula  # noqa: E402

CAL_BASE = 0x005E2510
MAGIC = b"FFCAL001"
VERSION = 1
LENGTH = 0x00E8                      # total, checksum included

# offset, name, struct code, count - must match patches/ff_fuel/src/ff_state.h
SCALARS = (
    (0x0C, "ff_can_id", "H", "-", "CAN id of the Pico frame; documentation, and the"
                                  " id echo at 0x803F98 is compared against it"),
    (0x0E, "ff_timeout_ms", "H", "ms", "no good frame for this long -> FAULT"),
    (0x10, "ff_hold_s", "H", "s", "FAULT: hold the last F for this long before decaying"),
    (0x12, "ff_filter_tau_ms", "H", "ms", "first-order time constant of the E filter"),
    (0x14, "ff_slew_pct_s", "H", "%/s", "maximum |dE/dt|; clamped to 1..100 in code"),
    (0x16, "ff_tick_ms", "H", "ms", "real period of the periodic hook (10 ms, C4 #44)"),
    (0x18, "ff_mode", "B", "-", "0 off (F = 1.000, no CAN), 1 normal, 2 bench override"),
    (0x19, "ff_e_override", "B", "%", "ethanol % used in mode 2"),
    (0x1A, "ff_stall_max", "B", "frames", "frames with an unchanged counter -> FAULT"),
    (0x1B, "ff_persist_enable", "B", "-", "D2 (#33): 1 = store E% in EEPROM block 8"),
    (0x1C, "ff_persist_hyst_pct", "B", "%", "D2: minimum E% change before a commit"),
    (0x1D, "ff_persist_block", "B", "-", "D2: EEP_CONF block number (eeprom.md section 5)"),
    (0x1E, "ff_persist_offset", "B", "-", "D2: payload offset inside that block"),
    (0x1F, "ff_persist_rate_s", "B", "s", "D2: minimum seconds between two commits"),
)

TABLES = (
    # offset, name, count, struct code, signed, kind, unit, note
    (0x20, "ff_F_curve", CURVE_N, "H", 0, "curve_1d", "1/1024",
     "fuel MASS factor over ethanol %, 17 points 0..100 step 6.25; F(0) = 1024"
     " is bit-identical to stock; read by ff_f_of() every activation"),
    (0x42, "ff_fzw_curve", CURVE_N, "B", 0, "curve_1d", "1/256",
     "RESERVED for the ignition blend (docs/05 section 3.4); neutral 0 in the MVP"),
    (0x54, "ff_dzw_map", 64, "b", 1, "map_2d_data", "0.75 degCA",
     "RESERVED 8x8 ignition offset (docs/05 section 3.4); neutral 0 in the MVP"),
    (0x94, "ff_fst_map", 36, "H", 0, "map_2d_data", "1/1024",
     "RESERVED 6x6 start/warm-up factor (docs/05 section 3.5); neutral 1024"),
    (0xDC, "ff_prail_add", 8, "B", 0, "curve_1d", "0.1 MPa",
     "RESERVED rail-pressure adder over ethanol % (docs/05 section 3.6); neutral 0"),
)

CRC_OFF = LENGTH - 2

DEFAULT_TABLES = {
    "ff_F_curve": None,                      # from the formula
    "ff_fzw_curve": [0] * CURVE_N,
    "ff_dzw_map": [0] * 64,
    "ff_fst_map": [1024] * 36,
    "ff_prail_add": [0] * 8,
}


class CalError(RuntimeError):
    pass


def load_params(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def build(params: dict) -> bytes:
    """The FFCAL001 bytes, big endian, from a parameter dict."""
    blk = bytearray(b"\x00" * LENGTH)
    blk[0:8] = MAGIC
    struct.pack_into(">HH", blk, 0x08, int(params.get("version", VERSION)), LENGTH)

    for off, name, code, _unit, _note in SCALARS:
        if name not in params:
            raise CalError(f"missing parameter {name!r}")
        struct.pack_into(">" + code, blk, off, int(params[name]))

    for off, name, n, code, _signed, _kind, _unit, _note in TABLES:
        values = params.get(name)
        if values is None:
            values = (f_curve_from_formula() if name == "ff_F_curve"
                      else DEFAULT_TABLES[name])
        if len(values) != n:
            raise CalError(f"{name} needs {n} values, got {len(values)}")
        struct.pack_into(">" + code * n, blk, off, *(int(v) for v in values))

    curve = struct.unpack_from(">" + "H" * CURVE_N, blk, 0x20)
    if curve[0] != 1024:
        raise CalError(f"ff_F_curve[0] must be exactly 1024 (F(0) = 1.000), "
                       f"got {curve[0]} - E0 would not be bit-identical")
    if any(b < a for a, b in zip(curve, curve[1:])):
        raise CalError("ff_F_curve must be monotonically non-decreasing")
    if max(curve) > 2048:
        raise CalError("ff_F_curve exceeds the 2048 (2.000) ceiling the patch clamps to")

    crc = (~sum(blk[:CRC_OFF])) & 0xFFFF
    struct.pack_into(">H", blk, CRC_OFF, crc)
    return bytes(blk)


def check(blk: bytes) -> None:
    """The same validation `ff_cal_ok()` does on the ECU."""
    if len(blk) != LENGTH:
        raise CalError(f"block is {len(blk)} B, expected {LENGTH}")
    if blk[0:8] != MAGIC:
        raise CalError("magic is not FFCAL001")
    version, length = struct.unpack_from(">HH", blk, 0x08)
    if version != VERSION or length != LENGTH:
        raise CalError(f"header says version {version} length {length:#x}")
    crc = struct.unpack_from(">H", blk, CRC_OFF)[0]
    if crc != (~sum(blk[:CRC_OFF])) & 0xFFFF:
        raise CalError("checksum mismatch")


# ------------------------------------------------------------- descriptors --
CSV_HEADER = ("addr,kind,x_axis_addr,y_axis_addr,x_n,y_n,elem_size,signed,"
              "consumer_func,name_or_blank,confidence,evidence,x_elem,y_elem,"
              "struct_addr,sites").split(",")

EVIDENCE = ("D1 issue #32, patches/ff_fuel (FFCAL001 at 0x5E2510, built by "
            "patches/ff_fuel/ffcal001.py); layout asserted by "
            "tests/test_ff_fuel_patch.py")


def rows(params: dict) -> list[dict]:
    """Descriptor rows in the column format of re/calibration_draft.csv."""
    out: list[dict] = []

    def row(addr, kind, x_n, y_n, elem_size, signed, name, note, x_elem=""):
        out.append({
            "addr": f"0x{addr:06X}", "kind": kind,
            "x_axis_addr": "", "y_axis_addr": "",
            "x_n": str(x_n), "y_n": str(y_n) if y_n else "",
            "elem_size": str(elem_size), "signed": str(signed),
            "consumer_func": "ff_fuel@patches/ff_fuel",
            "name_or_blank": name, "confidence": "static",
            "evidence": f"{EVIDENCE}; {note}",
            "x_elem": x_elem, "y_elem": "", "struct_addr": "", "sites": "1",
        })

    row(CAL_BASE + 0x08, "scalar", 1, "", 2, 0, "ff_cal_version",
        "FFCAL001 header: format version, must be 1")
    row(CAL_BASE + 0x0A, "scalar", 1, "", 2, 0, "ff_cal_length",
        f"FFCAL001 header: block length, {LENGTH} B including the checksum")
    for off, name, code, unit, note in SCALARS:
        row(CAL_BASE + off, "scalar", 1, "", 2 if code == "H" else 1, 0,
            name, f"{note} [{unit}]")
    for off, name, n, code, signed, kind, unit, note in TABLES:
        y_n = ""
        x_n = n
        if name == "ff_dzw_map":
            x_n, y_n = 8, 8
        elif name == "ff_fst_map":
            x_n, y_n = 6, 6
        row(CAL_BASE + off, kind, x_n, y_n,
            1 if code in ("B", "b") else 2, signed, name,
            f"{note} [{unit}]",
            x_elem="" if y_n == "" else "")
    row(CAL_BASE + CRC_OFF, "scalar", 1, "", 2, 0, "ff_cal_crc",
        "FFCAL001 header: bit-complement of the 16-bit sum of bytes [0, length-2)")
    return out


def write_rows(path: Path, params: dict) -> int:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        w.writeheader()
        for r in rows(params):
            w.writerow(r)
    return len(rows(params))


# -------------------------------------------------------------------- main --
def dump(blk: bytes) -> None:
    version, length = struct.unpack_from(">HH", blk, 0x08)
    print(f"FFCAL001 at {CAL_BASE:#08x}, version {version}, {length} B, "
          f"crc {struct.unpack_from('>H', blk, CRC_OFF)[0]:#06x}")
    for off, name, code, unit, _note in SCALARS:
        v = struct.unpack_from(">" + code, blk, off)[0]
        print(f"  +{off:02X}  {name:<20} {v:>6}  {unit}")
    for off, name, n, code, _s, _k, unit, _note in TABLES:
        v = struct.unpack_from(">" + code * n, blk, off)
        head = " ".join(str(x) for x in v[:8])
        print(f"  +{off:02X}  {name:<20} [{n}] {unit}  {head}"
              + (" ..." if n > 8 else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", default=str(HERE / "ffcal001.json"),
                    help="parameter file (default patches/ff_fuel/ffcal001.json)")
    ap.add_argument("-o", "--output", default=str(HERE / "build" / "ffcal001.bin"))
    ap.add_argument("--rows", default=str(HERE / "ffcal001_rows.csv"),
                    help="descriptor rows for re/calibration_draft.csv "
                         "(brief D3 owns that file; the integrator appends these)")
    ap.add_argument("--no-rows", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override one scalar, e.g. --set ff_mode=0")
    ap.add_argument("--print", dest="do_print", action="store_true")
    a = ap.parse_args(argv)

    params = load_params(Path(a.json))
    for item in a.set:
        name, _, value = item.partition("=")
        if name not in params:
            print(f"ffcal001: unknown parameter {name!r}", file=sys.stderr)
            return 1
        params[name] = int(value, 0)

    try:
        blk = build(params)
        check(blk)
    except CalError as exc:
        print(f"ffcal001: {exc}", file=sys.stderr)
        return 1

    if a.do_print:
        dump(blk)
        return 0

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blk)
    print(f"wrote {out} ({len(blk)} B, crc "
          f"{struct.unpack_from('>H', blk, CRC_OFF)[0]:#06x})")
    if not a.no_rows:
        n = write_rows(Path(a.rows), params)
        print(f"wrote {a.rows} ({n} descriptor rows for re/calibration_draft.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
