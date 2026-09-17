#!/usr/bin/env python3
"""Build the FFCAL001 calibration block for patches/ff_fuel, and its descriptors.

FFCAL001 lives at CPU **0x5E2510**, inside the 0x5E0000-0x5EFFFF Bosch checksum
block and in the 0xFF area docs/06_patch_pipeline.md section 3 reserves for new
calibration.  `patch.json` references the binary through `build.data`, so
`make gen` picks up whatever this script last wrote and `tools/patch_apply.py`
re-checksums the block.

Two outputs:

  build/ffcal001.bin    the 332 bytes that go into the image
                        (v4; v3 was 290, v2 266 and v1 232)
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
shipped file is still inert -- with `ff_zw_enable = 1` as well.

Version 3 (brief E2, issue #35, 2026-09-17) APPENDED the start enrichment and
again moved nothing: `ff_st_enable` (+0x108, shipped **0**), `ff_zwst_enable`
(+0x109, shipped **0**), `ff_fst_max` (+0x10A), `ff_zwst_max` (+0x10C),
`ff_zwst_tmax` (+0x10D), the two 6-point axes `ff_fst_e_axis` (+0x10E) and
`ff_fst_tmst_axis` (+0x114), and `ff_fzwst_curve` (+0x11A); the checksum moved
from +0x108 to +0x120 with the length.  `ff_fst_map`, which v1 and v2 reserved
as all-1024, is now read by `src/ff_start.c` and stays **all 1024**, so the
shipped file is inert with `ff_st_enable = 1` as well.

Version 4 (brief E5, issue #36, 2026-09-17) APPENDED the rail-pressure adder
and once more moved nothing: `ff_prail_enable` (+0x122, shipped **0**), one
reserved byte that keeps the two words below it even (+0x123), `ff_prail_max`
(+0x124), `ff_diag_window_ms` (+0x126) and the 17-point `ff_prail_curve`
(+0x128); the checksum moved from +0x120 to +0x14A with the length.  The
8-byte `ff_prail_add` table v1 reserved at +0xDC is **superseded** by
`ff_prail_curve` and is left in place, neutral and unread, so nothing moves.
It was the wrong shape for the job: eight u8 in 0.1 MPa with no axis at all,
against seventeen u16 in the ECU's own 0.005 bar on the same ethanol grid as
`ff_F_curve` and `ff_fzw_curve`.

The ECU-side `ff_cal_ok()` accepts **the current version only**: a v1, v2 or
v3 block flashed under a v4 blob is rejected exactly like a corrupt one, i.e.
mode 0, F = 1024, dzw_e = 0, fst_q10 = 1024, zwst_add = 0 and prail_add = 0.

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
    CURVE_N, DZW_N, DZW_NMOT_AXIS, DZW_RL_AXIS, FST_E_AXIS, FST_N,
    FST_TMST_AXIS, PRAIL_HARD_MAX, PRAIL_N, f_curve_from_formula,
    fzw_curve_default,
)

CAL_BASE = 0x005E2510
MAGIC = b"FFCAL001"
VERSION = 4                          # E5 (#36) appended the rail adder
LENGTH = 0x014C                      # total, checksum included

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
    (0x108, "ff_st_enable", "B", "-", "E2 (#35): 1 = apply the start enrichment"
                                      " f_st(E, tmst); 0 (shipped) pins fst_q10 at 1024"),
    (0x109, "ff_zwst_enable", "B", "-", "E2 (#35): 1 = apply the start-ignition"
                                        " advance; 0 (shipped) pins zwst_add at 0"),
    (0x10A, "ff_fst_max", "H", "1/1024", "E2: fst_q10 ceiling; clamped to"
                                         " FF_FST_HARD_MAX (2560 = 2.50x) in code"),
    (0x10C, "ff_zwst_max", "B", "0.75 degCA", "E2: zwst_add ceiling in counts;"
                                              " clamped to FF_ZWST_HARD_MAX (8) in code"),
    (0x10D, "ff_zwst_tmax", "B", "tmst count", "E2: the start advance is 0 at and"
                                               " above this tmst count (117 = 39.75 degC)"),
    (0x122, "ff_prail_enable", "B", "-", "E5 (#36): 1 = apply the rail-pressure"
                                         " adder; 0 (shipped) pins prail_add at 0"),
    (0x123, "ff_prail_rsv", "B", "-", "E5: 0, reserved; it is here so that"
                                      " ff_prail_max and ff_diag_window_ms stay"
                                      " 2-byte aligned inside the block"),
    (0x124, "ff_prail_max", "H", "0.005 bar", "E5: prail_add ceiling; clamped to"
                                              " FF_PRAIL_HARD_MAX (6000 = 30.0 bar)"
                                              " in code, and KLPRMAX 22000 caps the"
                                              " result whatever this says"),
    (0x126, "ff_diag_window_ms", "H", "ms", "E5: length of the window over which"
                                            " win_margin_min, prist_min and"
                                            " msv_sat_ticks are accumulated"),
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
    (0x94, "ff_fst_map", 36, "H", 0, "map_2d", "1/1024",
     "E2 (#35): start/warm-up fuel factor f_st, 6 ethanol rows x 6 tmst columns,"
     " read as interp(val, ny=6, nx=6, key_E, key_tmst) by src/ff_start.c."
     " ALL 1024 as shipped, so the file is inert even with ff_st_enable = 1;"
     " ROW 0 (E0) must stay exactly 1024 - that is what makes E0 bit-identical"
     " at both S1 sites. Calibrate from about 1.2x at a warm start upwards, and"
     " never past the injection window dwi (0x803088, injection.md section 8)"),
    (0xDC, "ff_prail_add", 8, "B", 0, "curve_1d", "0.1 MPa",
     "SUPERSEDED BY ff_prail_curve (+0x128, E5 #36): unread, kept neutral 0 so"
     " that nothing in the block moves. It was reserved by v1 as an 8 x u8"
     " adder in 0.1 MPa with no axis at all; E5 needed 17 points on the same"
     " ethanol grid as ff_F_curve and the ECU's own 0.005 bar unit, so it"
     " appended a proper table instead of bending this one"),
    (0xE8, "ff_dzw_nmot_axis", DZW_N, "H", 0, "axis", "0.25 rpm",
     "E1 (#34): the 8 row breakpoints of ff_dzw_map, every other breakpoint of"
     " the stock KFZW nmot axis 0x5C7736 (520..6520 rpm), so a cell lines up"
     " with a stock KFZW row"),
    (0xF8, "ff_dzw_rl_axis", DZW_N, "H", 0, "axis", "100/4096 %",
     "E1 (#34): the 8 column breakpoints of ff_dzw_map, eight of the twelve"
     " breakpoints of the stock KFZW rl axis 0x5C7758 (10.2..103.9 %)"),
    (0x10E, "ff_fst_e_axis", FST_N, "B", 0, "axis", "%",
     "E2 (#35): the 6 ROW breakpoints of ff_fst_map, ethanol volume percent."
     " Shared with ff_fzwst_curve. 85 is a breakpoint because E85 is the"
     " calibration target; 0 must stay 0 so row 0 is the E0 row"),
    (0x114, "ff_fst_tmst_axis", FST_N, "B", 0, "axis", "tmst count",
     "E2 (#35): the 6 COLUMN breakpoints of ff_fst_map, in tmst counts of"
     " 0.75 degC with a -48 degC offset. Six of the twelve stock KFWKSTT"
     " breakpoints (0x5C6C62), so every cell lines up with a stock row:"
     " 24/44/64/91/117/184 = -30/-15/0/+20.25/+39.75/+90 degC"
     " (re/findings/start.md section 9.4)"),
    (0x11A, "ff_fzwst_curve", FST_N, "b", 1, "curve_1d", "0.75 degCA",
     "E2 (#35): start-ignition ADVANCE over ethanol %, on ff_fst_e_axis, in s8"
     " counts. ALL ZERO as shipped. The knock retard is bypassed during the"
     " start (start.md section 5), so nothing downstream takes this back:"
     " stay inside +2..+4 degCA = +3..+5 counts"),
    (0x128, "ff_prail_curve", PRAIL_N, "H", 0, "curve_1d", "0.005 bar",
     "E5 (#36): rail-pressure setpoint ADDER over ethanol %, 17 points"
     " 0..100 step 6.25, on the same grid as ff_F_curve. ALL ZERO as shipped,"
     " so the file is inert even with ff_prail_enable = 1; prail_curve[0] = 0"
     " is what makes E0 bit-identical at 0x45845C. The adder goes in BEFORE"
     " the stock KLPRMAX ceiling (22000 = 110.0 bar) and the pump-volume rate"
     " limiter, so no value here can raise the rail past what the stock ECU"
     " already allows - the useful range is 0..3000 (0..15 bar), the headroom"
     " between KFPRSOLHOM's 19000 and KLPRMAX (re/findings/rail.md 12.1)"),
)

CRC_OFF = LENGTH - 2

DEFAULT_TABLES = {
    "ff_F_curve": None,                      # from the formula
    "ff_fzw_curve": None,                    # from the docs/05 3.4 shape
    "ff_dzw_map": [0] * 64,
    "ff_fst_map": [1024] * (FST_N * FST_N),
    "ff_prail_add": [0] * 8,
    "ff_prail_curve": [0] * PRAIL_N,
    "ff_dzw_nmot_axis": list(DZW_NMOT_AXIS),
    "ff_dzw_rl_axis": list(DZW_RL_AXIS),
    "ff_fst_e_axis": list(FST_E_AXIS),
    "ff_fst_tmst_axis": list(FST_TMST_AXIS),
    "ff_fzwst_curve": [0] * FST_N,
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

    # --- E2 (#35): the same kind of properties for the start enrichment ----
    fst = struct.unpack_from(">" + "H" * (FST_N * FST_N), blk, 0x94)
    if any(v != 1024 for v in fst[:FST_N]):
        raise CalError("ff_fst_map row 0 is the E0 row and must be exactly 1024 in"
                       f" all {FST_N} cells, got {list(fst[:FST_N])} - E0 would not"
                       " be bit-identical at the two S1 sites")
    if any(v < 1024 for v in fst):
        raise CalError("ff_fst_map may not go below 1024: f_st only ever ENRICHES"
                       " the start, and the stub refuses a value below 1024 anyway")
    if max(fst) > 2560:
        raise CalError(f"ff_fst_map reaches {max(fst)} = {max(fst) / 1024:.2f}x; the"
                       " patch clamps every cell to FF_FST_HARD_MAX = 2560 (2.50x)"
                       " in code, so anything above that is a calibration that lies")
    fst_max = struct.unpack_from(">H", blk, 0x10A)[0]
    if not 1024 <= fst_max <= 2560:
        raise CalError(f"ff_fst_max is {fst_max}; it has to be between 1024 (1.00x,"
                       " the neutral) and FF_FST_HARD_MAX 2560 (2.50x)")
    zwst_max = blk[0x10C]
    if zwst_max > 8:
        raise CalError(f"ff_zwst_max is {zwst_max} counts = {zwst_max * 0.75:.2f}"
                       " degCA; the patch clamps to FF_ZWST_HARD_MAX = 8 (6.00 degCA)"
                       " in code, and the knock retard is bypassed during the start")
    e_axis = blk[0x10E:0x10E + FST_N]
    if e_axis[0] != 0:
        raise CalError(f"ff_fst_e_axis[0] must be 0 so row 0 is the E0 row, got"
                       f" {e_axis[0]}")
    if e_axis[FST_N - 1] > 100:
        raise CalError("ff_fst_e_axis is ethanol volume percent; it cannot exceed 100")
    for name, off in (("ff_fst_e_axis", 0x10E), ("ff_fst_tmst_axis", 0x114)):
        axis = blk[off:off + FST_N]
        if any(b <= a for a, b in zip(axis, axis[1:])):
            raise CalError(f"{name} must be strictly increasing, got {list(axis)}"
                           " - the breakpoint search assumes it")
    fzwst = struct.unpack_from(">" + "b" * FST_N, blk, 0x11A)
    if fzwst[0] != 0:
        raise CalError(f"ff_fzwst_curve[0] must be exactly 0 (no start advance at"
                       f" E0), got {fzwst[0]}")
    if max(fzwst) > 8:
        raise CalError(f"ff_fzwst_curve reaches {max(fzwst)} counts ="
                       f" {max(fzwst) * 0.75:.2f} degCA; the patch clamps to"
                       " FF_ZWST_HARD_MAX = 8 (6.00 degCA) in code")

    # --- E5 (#36): the same three properties for the rail adder -----------
    prail = struct.unpack_from(">" + "H" * PRAIL_N, blk, 0x128)
    if prail[0] != 0:
        raise CalError(f"ff_prail_curve[0] must be exactly 0 (no rail raise at"
                       f" E0), got {prail[0]} - E0 would not be bit-identical"
                       " at the 0x45845C store")
    if any(b < a for a, b in zip(prail, prail[1:])):
        raise CalError("ff_prail_curve must be monotonically non-decreasing:"
                       " more ethanol never means less rail pressure")
    if max(prail) > PRAIL_HARD_MAX:
        raise CalError(f"ff_prail_curve reaches {max(prail)} ="
                       f" {max(prail) * 0.005:.2f} bar; the patch clamps every"
                       f" point to FF_PRAIL_HARD_MAX = {PRAIL_HARD_MAX}"
                       f" ({PRAIL_HARD_MAX * 0.005:.1f} bar) in code")
    prail_max = struct.unpack_from(">H", blk, 0x124)[0]
    if prail_max > PRAIL_HARD_MAX:
        raise CalError(f"ff_prail_max is {prail_max} = {prail_max * 0.005:.2f}"
                       f" bar; the patch clamps to FF_PRAIL_HARD_MAX ="
                       f" {PRAIL_HARD_MAX} ({PRAIL_HARD_MAX * 0.005:.1f} bar)"
                       " in code, so anything above that is a calibration that"
                       " lies")
    win_ms = struct.unpack_from(">H", blk, 0x126)[0]
    if win_ms < 1:
        raise CalError("ff_diag_window_ms must be at least 1 ms; the patch"
                       " floors the window at one activation anyway, but a 0"
                       " here says something the author did not mean")
    if blk[0x123] != 0:
        raise CalError(f"ff_prail_rsv is reserved and must be 0, got {blk[0x123]}")

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
            # E2: like ff_dzw_map, the only other table in the block with axes
            # of its own.  x is the tmst column, y the ethanol row.
            x_n, y_n = FST_N, FST_N
            extra = {"x_axis_addr": f"0x{CAL_BASE + 0x114:06X}",
                     "y_axis_addr": f"0x{CAL_BASE + 0x10E:06X}",
                     "x_elem": "u8", "y_elem": "u8"}
        elif name == "ff_fzwst_curve":
            # E2: a 1-D curve that borrows ff_fst_map's ethanol axis.
            extra = {"x_axis_addr": f"0x{CAL_BASE + 0x10E:06X}", "x_elem": "u8"}
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
