#!/usr/bin/env python3
"""Print a calibration object of `re/calibration_draft.csv` with its axes.

The naming pass (issue #41) needs the same three things for every candidate:
the raw cells, the breakpoints of both axes, and the same numbers again under
a trial scaling.  Doing that by hand with `med9lib` is where brief E3 spent
most of its time, so this tool does it from the draft row:

* `ADDR` is looked up in the draft; `kind`, `elem_size`, `signed`, the axis
  addresses and the axis element types come from there.  Nothing is guessed.
* `--scale`/`--offset` (and `--x-scale`, `--y-scale`) are applied only to the
  printout; they are a question asked of the numbers, never a claim about
  them.  Fractions such as `100/4096` are accepted.
* `--raw ADDR N ELEM` reads N elements at an address that has no draft row
  (a limit table, a pointer array, an axis the detector missed).
* `--guess` prints the physical range the numbers would have under each unit
  this project has *proved* (`re/findings/calibration_names.md` §2) and marks
  the ones whose plausible band the whole range fits.  It is a filter, not an
  answer: an axis that fits one unit and is nonsense under the others is
  evidence (D3's method), an axis that fits five is not.

Usage::

    python3 tools/cal_show.py data/passat_azx_ori.bin 0x5D36A8
    python3 tools/cal_show.py data/passat_azx_ori.bin 0x5D36A8 --scale 1/4096
    python3 tools/cal_show.py data/passat_azx_ori.bin 0x5C430D \\
            --scale 1/128 --x-scale 100/128 --y-scale 40
    python3 tools/cal_show.py data/passat_azx_ori.bin --raw 0x5C607C 24 u8
    python3 tools/cal_show.py data/passat_azx_ori.bin 0x5C6E20 --guess
    python3 tools/cal_show.py data/passat_azx_ori.bin --self-test
"""
from __future__ import annotations

import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib as m  # noqa: E402

DEFAULT_DRAFT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "re", "calibration_draft.csv")


def parse_number(text, default=None):
    """'100/4096' -> 0.0244..., '0.75' -> 0.75, '' -> default."""
    text = (text or "").strip()
    if not text:
        return default
    if "/" in text:
        num, _, den = text.partition("/")
        return float(num) / float(den)
    return float(text)


def elem_spec(text, default_size=1, default_signed=False):
    """'u16' -> (2, False); 's8' -> (1, True); '' -> the defaults."""
    text = (text or "").strip().lower()
    if not text:
        return default_size, default_signed
    signed = text.startswith("s")
    bits = int(text[1:]) if text[1:].isdigit() else default_size * 8
    return max(bits // 8, 1), signed


def read_elems(data, cpu_addr, count, size, signed):
    """Read `count` big-endian elements of `size` bytes from a CPU address."""
    off = m.cpu_to_file(cpu_addr)
    fmt = {1: "b" if signed else "B", 2: "h" if signed else "H",
           4: "i" if signed else "I"}[size]
    return list(struct.unpack_from(">%d%s" % (count, fmt), data, off))


def physical(values, scale, offset):
    return [v * scale + offset for v in values]


def fmt_value(value):
    if value == int(value):
        return "%d" % int(value)
    return ("%.4f" % value).rstrip("0").rstrip(".")


# The units this project has proved, with the plausible physical band of each
# and the finding that proved it.  Nothing may be added here that is not
# established in re/findings/; the band is what makes the filter a filter.
KNOWN_UNITS = (
    ("nmot_w", "1/min", 0.25, 0.0, 0.0, 8000.0, "ignition.md S2"),
    ("nmot8", "1/min", 40.0, 0.0, 0.0, 8000.0, "measuring_vars.md S7.2"),
    ("rl_w", "%", 100.0 / 4096, 0.0, 0.0, 200.0, "ignition.md S2"),
    ("rl8", "%", 100.0 / 128, 0.0, 0.0, 200.0, "calibration_names.md S2.1"),
    ("tmot/tmst", "degC", 0.75, -48.0, -50.0, 150.0, "start.md S1"),
    ("zw", "degCA", 0.75, 0.0, -60.0, 60.0, "ignition.md S6"),
    ("prail", "bar", 0.005, 0.0, 0.0, 250.0, "rail.md S2"),
    ("ti", "us", 1.0, 0.0, 0.0, 40000.0, "injection.md S3"),
    ("q7", "-", 1.0 / 128, 0.0, 0.0, 4.0, "start.md S4.1 (128 = 1.0)"),
    ("q12", "-", 1.0 / 4096, 0.0, 0.0, 4.0, "start.md S4.2 (4096 = 1.0)"),
    ("q15", "-", 1.0 / 32768, 0.0, 0.0, 4.0, "rail.md S14 (32768 = 1.0)"),
    ("pct15", "%", 100.0 / 32768, 0.0, 0.0, 150.0, "calibration_names.md S9.1"),
    ("pct16", "%", 100.0 / 65536, 0.0, 0.0, 150.0, "calibration_names.md S9.1"),
)


def band_fit(values, scale, offset, lo_ok, hi_ok):
    """(physical min, physical max, does the whole range fit the band?)."""
    lo = min(values) * scale + offset
    hi = max(values) * scale + offset
    return lo, hi, (lo_ok <= lo and hi <= hi_ok)


def monotone(values):
    return all(b >= a for a, b in zip(values, values[1:]))


def guess(values, out=sys.stdout):
    """Print the physical range of these numbers under every proved unit.

    A unit whose band the whole range fits is marked; that is a filter, not a
    conclusion.  An axis is evidence only when exactly one unit survives and
    the consumer's arithmetic agrees (re/README.md, 'Contributing a name').
    """
    print("guess     monotone=%s  n=%d" % (monotone(values), len(values)),
          file=out)
    for name, unit, scale, offset, lo_ok, hi_ok, source in KNOWN_UNITS:
        lo, hi, fits = band_fit(values, scale, offset, lo_ok, hi_ok)
        print("  %-10s %-6s %10s .. %-10s  [%s]%s"
              % (name, unit, fmt_value(lo), fmt_value(hi), source,
                 "  <<<" if fits else ""), file=out)


def load_draft(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["addr"].upper(): row for row in csv.DictReader(handle)}


def axis_of(data, addr_text, count, elem_text, scale, offset):
    """Return (raw, physical) for an axis, or (None, None) when it has none."""
    if not addr_text or not count:
        return None, None
    size, signed = elem_spec(elem_text)
    raw = read_elems(data, int(addr_text, 16), int(count), size, signed)
    return raw, physical(raw, scale, offset)


def show(data, row, args, out=sys.stdout):
    addr = int(row["addr"], 16)
    size = int(row["elem_size"] or 1)
    signed = (row.get("signed") or "0") == "1"
    nx = int(row["x_n"] or 1)
    ny = int(row["y_n"] or 1)
    scale = parse_number(args.scale, 1.0)
    offset = parse_number(args.offset, 0.0)

    print("addr      0x%06X  (file 0x%06X)" % (addr, m.cpu_to_file(addr)), file=out)
    print("kind      %s  %s%d  %d x %d  = %d byte(s)"
          % (row["kind"], "s" if signed else "u", size * 8, nx, ny,
             nx * ny * size), file=out)
    if row.get("consumer_func"):
        print("consumer  %s" % row["consumer_func"], file=out)
    if scale != 1.0 or offset != 0.0:
        print("value     x %s %+g" % (args.scale or "1", offset), file=out)

    x_raw, x_phys = axis_of(data, row.get("x_axis_addr"), row.get("x_n"),
                            row.get("x_elem"), parse_number(args.x_scale, 1.0),
                            parse_number(args.x_offset, 0.0))
    y_raw, y_phys = axis_of(data, row.get("y_axis_addr"), row.get("y_n"),
                            row.get("y_elem"), parse_number(args.y_scale, 1.0),
                            parse_number(args.y_offset, 0.0))
    if x_raw is not None:
        print("x axis    0x%s raw %s" % (row["x_axis_addr"][2:].upper(), x_raw),
              file=out)
        print("          phys    %s" % [fmt_value(v) for v in x_phys], file=out)
    if y_raw is not None:
        print("y axis    0x%s raw %s" % (row["y_axis_addr"][2:].upper(), y_raw),
              file=out)
        print("          phys    %s" % [fmt_value(v) for v in y_phys], file=out)

    cells = read_elems(data, addr, nx * ny, size, signed)
    print("values    min %s  max %s" % (min(cells), max(cells)), file=out)
    if args.guess:
        if x_raw:
            print("x axis:", file=out)
            guess(x_raw, out)
        if y_raw:
            print("y axis:", file=out)
            guess(y_raw, out)
        print("values:", file=out)
        guess(cells, out)
    for iy in range(ny):
        rowvals = cells[iy * nx:(iy + 1) * nx]
        label = ("%9s" % fmt_value(y_phys[iy])) if y_phys else ("%9d" % iy)
        print("%s | %s" % (label,
                          "  ".join("%8s" % fmt_value(v * scale + offset)
                                    for v in rowvals)), file=out)
    return 0


def self_test():
    """Structural check that needs no dump: the parsers behave."""
    assert parse_number("100/4096") == 100.0 / 4096
    assert parse_number("") is None and parse_number("", 1.0) == 1.0
    assert elem_spec("u16") == (2, False)
    assert elem_spec("s8") == (1, True)
    assert elem_spec("") == (1, False)
    assert fmt_value(3.0) == "3" and fmt_value(0.75) == "0.75"
    assert physical([1, 2], 0.75, -48.0) == [-47.25, -46.5]
    # SRL12ZUUW, the proven 100/4096 %/LSB load axis of KFZW: 10.2 .. 103.9 %
    srl12 = [416, 640, 864, 1056, 1280, 1696, 2144, 2560, 2976, 3424, 3840, 4256]
    assert monotone(srl12)
    lo, hi, fits = band_fit(srl12, 100.0 / 4096, 0.0, 0.0, 200.0)
    assert fits and abs(lo - 10.15625) < 1e-6 and abs(hi - 103.90625) < 1e-6
    # ... and it cannot be an engine speed: 4256 * 40 = 170 240 rpm
    assert not band_fit(srl12, 40.0, 0.0, 0.0, 8000.0)[2]
    # the tmst axis of KFWKSTT: -30 .. +90 degC, and nothing else fits
    tmst = [24, 31, 37, 44, 55, 64, 84, 91, 101, 117, 144, 184]
    assert band_fit(tmst, 0.75, -48.0, -50.0, 150.0)[2]
    assert not band_fit(tmst, 0.005, 0.0, 1.0, 250.0)[2]
    print("cal_show self-test OK")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?")
    ap.add_argument("addr", nargs="?", help="CPU address of a draft row")
    ap.add_argument("--draft", default=DEFAULT_DRAFT)
    ap.add_argument("--raw", nargs=3, metavar=("ADDR", "N", "ELEM"),
                    help="read N elements of type ELEM at ADDR, ignoring the draft")
    ap.add_argument("--scale"), ap.add_argument("--offset")
    ap.add_argument("--x-scale"), ap.add_argument("--x-offset")
    ap.add_argument("--y-scale"), ap.add_argument("--y-offset")
    ap.add_argument("--guess", action="store_true",
                    help="report which proved unit makes the numbers round")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.image:
        ap.error("give an image, or --self-test")
    data = m.load_dump(args.image)

    if args.raw:
        addr, count, elem = int(args.raw[0], 16), int(args.raw[1]), args.raw[2]
        size, signed = elem_spec(elem)
        values = read_elems(data, addr, count, size, signed)
        scale = parse_number(args.scale, 1.0)
        offset = parse_number(args.offset, 0.0)
        print("0x%06X  %s x %d" % (addr, elem, count))
        print("raw   %s" % values)
        if scale != 1.0 or offset != 0.0:
            print("phys  %s" % [fmt_value(v * scale + offset) for v in values])
        if args.guess:
            guess(values)
        return 0

    if not args.addr:
        ap.error("give an address, or --raw")
    draft = load_draft(args.draft)
    key = args.addr.upper()
    if not key.startswith("0X"):
        key = "0X" + key
    key = "0x" + key[2:]
    row = draft.get(key.upper())
    if row is None:
        print("%s is not a row of %s" % (args.addr, args.draft), file=sys.stderr)
        return 1
    return show(data, row, args)


if __name__ == "__main__":
    sys.exit(main())
