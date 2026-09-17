#!/usr/bin/env python3
"""Dump the tables that decide what the ECU's own programming route can touch.

Brief E6 (issues #26 #27 #28 #32), `re/findings/flash_programming.md`.
Four tables, all read straight out of `data/passat_azx_ori.bin`:

* ``--devices``  the flash *device* table at 0x082980 (3 entries x 0x1C B:
  start, end, five driver entry points) and its twin in the RAM-resident
  bootstrap loader at 0x01E71C.  Device 1 is the on-chip UC3F flash.
* ``--geometry`` the erase-block geometry table at 0x0825E4 (5 command-set
  types x 0x20 B: JEDEC id, five region counts, five region sizes), expanded
  into the block list each device is erased in, plus the UC3FCTL block-select
  mask table at 0x082684 for the on-chip type.
* ``--kwp``     the programming-mode KWP dispatch table at 0x088174
  (13 entries x 20 B, same layout as the application table at 0x02B820) and
  the download/erase address whitelist that `kwp_download_range_allowed`
  (0x0889C8) hard-codes.
* ``--segments`` the 32-entry segment table behind `kwp_transfer_mode4`
  (0x0A33B4), i.e. the map of the 1 KB upload window 0x480000-0x480400.  It is
  EEP_CONF, the SPI-EEPROM block table at 0x0B2FF0, so the window is the
  EEPROM image; `tools/eeprom_map.py` decodes the same bytes from the EEPROM
  side.

Usage::

    python3 tools/flash_segments.py data/passat_azx_ori.bin --all
    python3 tools/flash_segments.py data/passat_azx_ori.bin --geometry
    python3 tools/flash_segments.py data/passat_azx_ori.bin --json

Nothing here is a guess: every address is cited in
`re/findings/flash_programming.md` with the instruction that reads it.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys

# --- table addresses (CPU == file offset in the external flash) -------------
DEV_TABLE = 0x082980          # u32 count, then 3 x 0x1C
DEV_TABLE_LOADER = 0x01E71C   # the RAM loader's copy (same shape)
DEV_ENTRY_SIZE = 0x1C
GEOM_TABLE = 0x0825E4         # 5 x 0x20
GEOM_ENTRY_SIZE = 0x20
GEOM_TYPES = 5
UC3F_MASK_TABLE = 0x082684    # 10 x u16, UC3FCTL BLOCK/SBBLOCK select bits
UC3F_TYPE = 3
KWP_PROG_TABLE = 0x088174     # 13 x 20
KWP_PROG_COUNT_AT = 0x08829C  # config struct 0x088280 +0x1C
KWP_PROG_ENTRY_SIZE = 20
MODE4_TABLE = 0x0B2FF0        # EEP_CONF, 32 x 0xC
MODE4_ENTRIES = 32
MODE4_WINDOW = 0x480000
LFSR_MASK_AT = 0x088170       # 0x5FBD5DBD, the level-1 SecurityAccess mask

# The whitelist `kwp_download_range_allowed` (0x0889C8) hard-codes.  The last
# three rows depend on the variant byte at RAM 0x7FD328; see flash_programming.md
# section 2.4.
DOWNLOAD_WHITELIST = (
    (0x020000, 0x07FFFF, "always", "application code, part 1 (CS0)"),
    (0x0A0000, 0x1BFFFF, "always", "application code, part 2 (CS0)"),
    (0x404000, 0x47FFFF, "always", "on-chip UC3F flash"),
    (0x080000, 0x09FFFF, "always", "alias, rewritten to 0x1C0000-0x1DFFFF"),
    (0x1E0000, 0x1FFFFF, "variant != 0x33", "calibration, upper half (CS0)"),
    (0x1C0000, 0x1DFFFF, "variant != 0x33", "calibration, lower half (CS0)"),
    (0x1C0000, 0x1FFFFF, "variant not 0x11", "whole calibration (CS0)"),
)

KWP_SERVICE_NAMES = {
    0x10: "StartDiagnosticSession", 0x1A: "ReadEcuIdentification",
    0x20: "StopDiagnosticSession", 0x27: "SecurityAccess",
    0x31: "StartRoutineByLocalId", 0x33: "RequestRoutineResultsByLocalId",
    0x34: "RequestDownload", 0x36: "TransferData", 0x37: "RequestTransferExit",
    0x3E: "TesterPresent", 0x81: "StartCommunication",
    0x82: "StopCommunication", 0x83: "AccessTimingParameters",
}


def _u32(d: bytes, off: int) -> int:
    return struct.unpack_from(">I", d, off)[0]


def _u16(d: bytes, off: int) -> int:
    return struct.unpack_from(">H", d, off)[0]


# --- decoders ---------------------------------------------------------------
def devices(d: bytes, base: int = DEV_TABLE) -> list[dict]:
    """The flash device table: {start, end, five driver entry points}."""
    count = d[base]            # lbz at 0x082D20; the rest of the word is pad
    out = []
    for i in range(count):
        e = base + 4 + i * DEV_ENTRY_SIZE
        out.append({
            "index": i,
            "addr": e,
            "start": _u32(d, e),
            "end": _u32(d, e + 4),
            "ops": [_u32(d, e + 8 + 4 * k) for k in range(5)],
        })
    return out


def geometry(d: bytes) -> list[dict]:
    """The erase-block geometry of each of the five command-set types."""
    out = []
    for t in range(GEOM_TYPES):
        e = GEOM_TABLE + t * GEOM_ENTRY_SIZE
        counts = list(d[e + 4:e + 9])
        sizes = [_u32(d, e + 0x0C + 4 * k) for k in range(5)]
        regions = [(c, s) for c, s in zip(counts, sizes) if c and s]
        out.append({
            "type": t,
            "addr": e,
            "id": _u32(d, e),
            "regions": regions,
            "total": sum(c * s for c, s in regions),
        })
    return out


def blocks(regions, array_base: int) -> list[tuple[int, int, int]]:
    """Expand a region list into (index, start, size) erase blocks."""
    out = []
    addr = array_base
    idx = 0
    for count, size in regions:
        for _ in range(count):
            out.append((idx, addr, size))
            addr += size
            idx += 1
    return out


def uc3f_masks(d: bytes, n: int) -> list[int]:
    return [_u16(d, UC3F_MASK_TABLE + 2 * i) for i in range(n)]


def kwp_prog_table(d: bytes) -> list[dict]:
    count = d[KWP_PROG_COUNT_AT]
    out = []
    for i in range(count):
        e = KWP_PROG_TABLE + i * KWP_PROG_ENTRY_SIZE
        out.append({
            "addr": e,
            "sid": d[e],
            "sub": d[e + 1],
            "session_mask": _u32(d, e + 4),
            "h1": _u32(d, e + 8),
            "h2": _u32(d, e + 0x0C),
            "arg": _u32(d, e + 0x10),
        })
    return out


def mode4_segments(d: bytes) -> list[dict]:
    """The 0x480000 upload window map = EEP_CONF (see eeprom.md section 3.3)."""
    out = []
    for i in range(MODE4_ENTRIES):
        e = MODE4_TABLE + i * 0x0C
        flags = _u16(d, e + 8)
        length = d[e + 0x0A]
        replicated = bool(flags & 1)
        off = _u16(d, e + 2)
        span = length * 2 if replicated else length
        out.append({
            "index": i,
            "addr": e,
            "eeprom_off": off,
            "length": length,
            "replicated": replicated,
            "span": span,
            "flags": flags,
            "window_start": MODE4_WINDOW + off,
            "window_end": MODE4_WINDOW + off + span - 1,
        })
    return out


# --- printers ---------------------------------------------------------------
def print_devices(d: bytes) -> None:
    for label, base in (("application module", DEV_TABLE),
                        ("RAM bootstrap loader", DEV_TABLE_LOADER)):
        print(f"flash device table, {label} (0x{base:06X}), "
              f"{d[base]} entries")
        print("  idx  start     end       driver entry points")
        for e in devices(d, base):
            ops = " ".join(f"0x{o:06X}" for o in e["ops"])
            print(f"  {e['index']:3d}  0x{e['start']:06X}  0x{e['end']:06X}  {ops}")
        print()
    print("  device 1 (0x404000-0x47FFFF) is the on-chip UC3F flash; it is")
    print("  enabled only while RAM 0x7F8014 == 0x20, which the boot code sets")
    print("  on every MPC563 start (flash_programming.md section 6.1).")


def print_geometry(d: bytes) -> None:
    names = {0: "Intel, 32-bit port", 1: "AMD, 32-bit port",
             2: "Intel/ST, 32-bit port", 3: "on-chip UC3F",
             4: "AMD, 16-bit port"}
    print(f"erase-block geometry table (0x{GEOM_TABLE:06X}), "
          f"{GEOM_TYPES} command-set types")
    for g in geometry(d):
        regions = ", ".join(f"{c} x 0x{s:X}" for c, s in g["regions"])
        print(f"  type {g['type']} @0x{g['addr']:06X}  id 0x{g['id']:08X}  "
              f"total 0x{g['total']:X}  [{regions}]   {names[g['type']]}")
    print()
    uc3f = geometry(d)[UC3F_TYPE]
    blks = blocks(uc3f["regions"], 0x400000)
    masks = uc3f_masks(d, len(blks))
    print(f"on-chip UC3F erase blocks, array base 0x400000 "
          f"({len(blks)} blocks, select masks at 0x{UC3F_MASK_TABLE:06X}):")
    print("  idx  range                 size     UC3FCTL select")
    for (idx, start, size), mask in zip(blks, masks):
        field = ("SBBLOCK[0]" if mask == 0x0200 else
                 "SBBLOCK[1]" if mask == 0x0100 else
                 f"BLOCK[{7 - (mask.bit_length() - 1)}]")
        note = "  <- not reachable over OBD" if start < 0x404000 else ""
        print(f"  {idx:3d}  0x{start:06X}-0x{start + size - 1:06X}  "
              f"0x{size:06X}  0x{mask:04X} {field}{note}")


def print_kwp(d: bytes) -> None:
    print(f"programming-mode KWP dispatch table (0x{KWP_PROG_TABLE:06X}), "
          f"{d[KWP_PROG_COUNT_AT]} entries; "
          f"level-1 LFSR mask 0x{_u32(d, LFSR_MASK_AT):08X} at "
          f"0x{LFSR_MASK_AT:06X}")
    print("  entry     SID  service                         h1        h2")
    for e in kwp_prog_table(d):
        name = KWP_SERVICE_NAMES.get(e["sid"], "?")
        print(f"  0x{e['addr']:06X}  0x{e['sid']:02X}  {name:<30}  "
              f"0x{e['h1']:06X}  0x{e['h2']:06X}")
    print()
    print("download / erase address whitelist (kwp_download_range_allowed, "
          "0x0889C8):")
    print("  start     end       when              what")
    for start, end, when, what in DOWNLOAD_WHITELIST:
        print(f"  0x{start:06X}  0x{end:06X}  {when:<16}  {what}")
    print("  anything else -> NRC 0x42 can'tDownloadToSpecifiedAddress")


def print_segments(d: bytes) -> None:
    print(f"kwp_transfer_mode4 segment table (0x{MODE4_TABLE:06X} = EEP_CONF), "
          f"{MODE4_ENTRIES} entries; window 0x{MODE4_WINDOW:06X}-"
          f"0x{MODE4_WINDOW + 0x400:06X}")
    print("  idx  window range          eeprom  len   copies  reachable")
    covered = 0
    for s in mode4_segments(d):
        covered += s["span"]
        inside = s["window_start"] < MODE4_WINDOW + 0x400
        print(f"  {s['index']:3d}  0x{s['window_start']:06X}-0x{s['window_end']:06X}  "
              f"0x{s['eeprom_off']:03X}   0x{s['length']:02X}  "
              f"{2 if s['replicated'] else 1:6d}  {'yes' if inside else 'no'}")
    print(f"  {covered} of 0x800 EEPROM bytes are mapped; unmapped ranges read")
    print("  back 0xFF.  kwp_sid_35_h1 rejects an upload whose end reaches")
    print("  0x480400 with NRC 0x53, so only the first 1 KB (blocks 0-21) can")
    print("  actually be read through this window.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("--devices", action="store_true")
    p.add_argument("--geometry", action="store_true")
    p.add_argument("--kwp", action="store_true")
    p.add_argument("--segments", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable dump")
    a = p.parse_args(argv)

    with open(a.image, "rb") as fh:
        d = fh.read()

    if a.json:
        json.dump({
            "devices": devices(d),
            "devices_loader": devices(d, DEV_TABLE_LOADER),
            "geometry": geometry(d),
            "uc3f_blocks": blocks(geometry(d)[UC3F_TYPE]["regions"], 0x400000),
            "uc3f_masks": uc3f_masks(d, len(blocks(
                geometry(d)[UC3F_TYPE]["regions"], 0x400000))),
            "kwp_prog_table": kwp_prog_table(d),
            "download_whitelist": [
                {"start": s, "end": e, "when": w, "what": t}
                for s, e, w, t in DOWNLOAD_WHITELIST],
            "mode4_segments": mode4_segments(d),
        }, sys.stdout, indent=1)
        print()
        return 0

    want = (a.devices, a.geometry, a.kwp, a.segments)
    if a.all or not any(want):
        want = (True, True, True, True)
    printers = (print_devices, print_geometry, print_kwp, print_segments)
    first = True
    for flag, fn in zip(want, printers):
        if flag:
            if not first:
                print()
            fn(d)
            first = False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
