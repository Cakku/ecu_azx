#!/usr/bin/env python3
"""Build the FFCAL001 calibration block for patches/ff_fuel, and its descriptors.

FFCAL001 lives at CPU **0x5E2510**, inside the 0x5E0000-0x5EFFFF Bosch checksum
block and in the 0xFF area docs/06_patch_pipeline.md section 3 reserves for new
calibration.  `patch.json` references the binary through `build.data`, so
`make gen` picks up whatever this script last wrote and `tools/patch_apply.py`
re-checksums the block.

Two outputs:

  build/ffcal001.bin    the 266 bytes that go into the image (v2; v1 was 232)
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

Version 2 (brief E1, issue #34, 2026-09-17) APPENDED the ignition blend and
moved nothing: `ff_zw_enable` (+0xE6, shipped **0**), `ff_dzw_max` (+0xE7),
and the two 8-point breakpoint axes of `ff_dzw_map` (+0xE8, +0xF8); the
checksum moved from +0xE6 to +0x108 with the length.  `ff_fzw_curve`, which
v1 reserved as all-zero, now carries the docs/05 section 3.4 shape (0 at E0,
the u8 maximum 255 from E50 on) and `ff_dzw_map` stays all zero, so the
shipped file is still inert -- with `ff_zw_enable = 1` as well.  The ECU-side
`ff_cal_ok()` accepts **version 2 only**: a v1 block flashed under a v2 blob
is rejected exactly like a corrupt one, i.e. mode 0, F = 1024 and dzw_e = 0.

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
from emu.models.flexfuel import (  # noqa: E402
    CURVE_N, DZW_N, DZW_NMOT_AXIS, DZW_RL_AXIS, f_curve_from_formula,
    fzw_curve_default,
)

CAL_BASE = 0x005E2510
MAGIC = b"FFCAL001"
VERSION = 2                          # E1 (#34) appended the ignition blend
LENGTH = 0x010A                      # total, checksum included

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
    (0xE6, "ff_zw_enable", "B", "-", "E1 (#34): 1 = apply the ignition blend;"
                                     " 0 (shipped) makes dzw_e permanently 0"),
    (0xE7, "ff_dzw_max", "B", "0.75 degCA", "E1: |dzw_e| ceiling in counts;"
                                            " clamped to FF_DZW_HARD_MAX (16) in code"),
)

TABLES = (
    # offset, name, count, struct code, signed, kind, unit, note
    (0x20, "ff_F_curve", CURVE_N, "H", 0, "curve_1d", "1/1024",
     "fuel MASS factor over ethanol %, 17 points 0..100 step 6.25; F(0) = 1024"
     " is bit-identical to stock; read by ff_f_of() every activation"),
    (0x42, "ff_fzw_curve", CURVE_N, "B", 0, "curve_1d", "1/256",
     "E1 (#34): ignition blend factor over ethanol %, 17 points 0..100 step 6.25."
     " f_zw(0) = 0 is what makes E0 bit-identical for the ignition too;"
     " 255 = 0.996 is the u8 maximum and the plateau from E50 on"),
    (0x54, "ff_dzw_map", 64, "b", 1, "map_2d", "0.75 degCA",
     "E1 (#34): 8 nmot rows x 8 rl columns of ignition ADVANCE at f_zw = 1,"
     " read as interp_2d_s8(val, nx=8, key_nmot, key_rl). ALL ZERO as shipped,"
     " so the file is inert even with ff_zw_enable = 1; calibrate it from +0"
     " towards +2 degCA only in cells where dwkrz stays 0"),
    (0x94, "ff_fst_map", 36, "H", 0, "map_2d_data", "1/1024",
     "RESERVED 6x6 start/warm-up factor (docs/05 section 3.5); neutral 1024"),
    (0xDC, "ff_prail_add", 8, "B", 0, "curve_1d", "0.1 MPa",
     "RESERVED rail-pressure adder over ethanol % (docs/05 section 3.6); neutral 0"),
    (0xE8, "ff_dzw_nmot_axis", DZW_N, "H", 0, "axis", "0.25 rpm",
     "E1 (#34): the 8 row breakpoints of ff_dzw_map, every other breakpoint of"
     " the stock KFZW nmot axis 0x5C7736 (520..6520 rpm), so a cell lines up"
     " with a stock KFZW row"),
    (0xF8, "ff_dzw_rl_axis", DZW_N, "H", 0, "axis", "100/4096 %",
     "E1 (#34): the 8 column breakpoints of ff_dzw_map, eight of the twelve"
     " breakpoints of the stock KFZW rl axis 0x5C7758 (10.2..103.9 %)"),
)

CRC_OFF = LENGTH - 2

DEFAULT_TABLES = {
    "ff_F_curve": None,                      # from the formula
    "ff_fzw_curve": None,                    # from the docs/05 3.4 shape
    "ff_dzw_map": [0] * 64,
    "ff_fst_map": [1024] * 36,
    "ff_prail_add": [0] * 8,
    "ff_dzw_nmot_axis": list(DZW_NMOT_AXIS),
    "ff_dzw_rl_axis": list(DZW_RL_AXIS),
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
            if name == "ff_F_curve":
                values = f_curve_from_formula()
            elif name == "ff_fzw_curve":
                values = fzw_curve_default()
            else:
                values = DEFAULT_TABLES[name]
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

    # --- E1 (#34): the same three properties for the ignition blend --------
    fzw = blk[0x42:0x42 + CURVE_N]
    if fzw[0] != 0:
        raise CalError(f"ff_fzw_curve[0] must be exactly 0 (no advance at E0), "
                       f"got {fzw[0]} - E0 would not be bit-identical")
    if any(b < a for a, b in zip(fzw, fzw[1:])):
        raise CalError("ff_fzw_curve must be monotonically non-decreasing")
    for name, off in (("ff_dzw_nmot_axis", 0xE8), ("ff_dzw_rl_axis", 0xF8)):
        axis = struct.unpack_from(">" + "H" * DZW_N, blk, off)
        if any(b <= a for a, b in zip(axis, axis[1:])):
            raise CalError(f"{name} must be strictly increasing, got {list(axis)}"
                           " - the breakpoint search assumes it")
    dzw_max = blk[0xE7]
    if dzw_max > 16:
        raise CalError(f"ff_dzw_max is {dzw_max} counts = {dzw_max * 0.75:.2f} degCA;"
                       " the patch clamps to FF_DZW_HARD_MAX = 16 (12.00 degCA) in"
                       " code, so anything above that is a calibration that lies")

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

    def row(addr, kind, x_n, y_n, elem_size, signed, name, note, x_elem="",
            x_axis_addr="", y_axis_addr="", y_elem=""):
        out.append({
            "addr": f"0x{addr:06X}", "kind": kind,
            "x_axis_addr": x_axis_addr, "y_axis_addr": y_axis_addr,
            "x_n": str(x_n), "y_n": str(y_n) if y_n else "",
            "elem_size": str(elem_size), "signed": str(signed),
            "consumer_func": "ff_fuel@patches/ff_fuel",
            "name_or_blank": name, "confidence": "static",
            "evidence": f"{EVIDENCE}; {note}",
            "x_elem": x_elem, "y_elem": y_elem, "struct_addr": "", "sites": "1",
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
        extra = {}
        if name == "ff_dzw_map":
            # E1: the only table in the block with axes of its own.  x is the
            # rl column (interp_2d_s8 indexes val[iy * nx + ix]), y the nmot row.
            x_n, y_n = DZW_N, DZW_N
            extra = {"x_axis_addr": f"0x{CAL_BASE + 0xF8:06X}",
                     "y_axis_addr": f"0x{CAL_BASE + 0xE8:06X}",
                     "x_elem": "u16", "y_elem": "u16"}
        elif name == "ff_fst_map":
            x_n, y_n = 6, 6
        row(CAL_BASE + off, kind, x_n, y_n,
            1 if code in ("B", "b") else 2, signed, name,
            f"{note} [{unit}]", **extra)
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
