#!/usr/bin/env python3
"""Decode the flex-fuel node's CAN frame (default id 0x0EC) from candump,
candump -L, SavvyCAN CSV/GVRET exports or raw hex bytes.

The frame is produced by pico_can_sender/ and specified in
docs/05_flexfuel_design.md section 2 (bytes 0, 1 and 7 are Zeitronix ECA-2 CAN
compatible, bytes 2-6 are ours):

    byte 0  ethanol content 0..100 %, averaged
    byte 1  fuel temperature in C + 40
    byte 2  raw frequency / 2, in Hz
    byte 3  rolling counter 0..255
    byte 4  reserved 0
    byte 5  reserved 0
    byte 6  firmware version
    byte 7  status: 0 OK, 1 sensor fault, 2 contaminated fuel, 3 not ready

python-can / cantools are NOT required; they are only used if installed and
--live is given. data/ethanol_node.dbc carries the same layout for tools that
want a DBC.

Usage:
    # a single frame, any of these spellings
    ethanol_frame_decode.py "0EC#32 2A 32 05 00 00 01 00"
    ethanol_frame_decode.py "  can0  0EC   [8]  32 2A 32 05 00 00 01 00"
    ethanol_frame_decode.py "(1622019432.123456) can0 0EC#322A320500000100"
    ethanol_frame_decode.py 32 2A 32 05 00 00 01 00

    # a whole log, only the flex-fuel id, with gap/counter checking
    candump -L can0 > drive.log
    ethanol_frame_decode.py --file drive.log
    ethanol_frame_decode.py --file savvycan_export.csv --id 0x0EC --summary

    # live, needs python-can and a configured socketcan interface
    ethanol_frame_decode.py --live --channel can0
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

DEFAULT_ID = 0x0EC

STATUS_NAMES = {
    0: "OK",
    1: "SENSOR FAULT",
    2: "CONTAMINATED FUEL",
    3: "NOT READY",
}


# --------------------------------------------------------------------- decode


@dataclass
class Frame:
    timestamp: Optional[float]
    can_id: int
    data: bytes


@dataclass
class Decoded:
    ethanol_pct: int
    fuel_temp_c: int
    freq_hz: int
    counter: int
    reserved: tuple
    fw_version: int
    status: int

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, f"UNKNOWN({self.status})")

    def line(self) -> str:
        return (
            f"E{self.ethanol_pct:3d}%  T{self.fuel_temp_c:4d} C  "
            f"f{self.freq_hz:4d} Hz  cnt{self.counter:4d}  "
            f"v{self.fw_version}  {self.status_name}"
        )


def decode(data: bytes) -> Decoded:
    if len(data) != 8:
        raise ValueError(f"expected 8 data bytes, got {len(data)}")
    return Decoded(
        ethanol_pct=data[0],
        fuel_temp_c=data[1] - 40,
        freq_hz=data[2] * 2,
        counter=data[3],
        reserved=(data[4], data[5]),
        fw_version=data[6],
        status=data[7],
    )


def plausibility_notes(d: Decoded) -> list:
    """Cheap sanity checks against docs/05_flexfuel_design.md section 2."""
    notes = []
    if d.ethanol_pct > 100:
        notes.append(f"ethanol {d.ethanol_pct} % out of range")
    if not -40 <= d.fuel_temp_c <= 125:
        notes.append(f"fuel temperature {d.fuel_temp_c} C out of sensor range")
    if d.status == 0 and not 44 <= d.freq_hz <= 156:
        notes.append(f"status OK but frequency {d.freq_hz} Hz outside 45..155 Hz")
    if d.status == 0 and abs((d.freq_hz - 50) - d.ethanol_pct) > 3:
        notes.append(
            f"ethanol {d.ethanol_pct} % does not match {d.freq_hz} Hz "
            f"(expected about {max(0, min(100, d.freq_hz - 50))} %)"
        )
    if d.reserved != (0, 0):
        notes.append(f"reserved bytes not zero: {d.reserved}")
    return notes


# ---------------------------------------------------------------- log parsing

_HEX = r"[0-9A-Fa-f]"

# "(1622019432.123456) can0 0EC#322A320500000100"   candump -L
_RE_LOG = re.compile(
    rf"^\s*(?:\((?P<ts>\d+\.\d+)\)\s+)?(?P<if>\w+)\s+"
    rf"(?P<id>{_HEX}{{3,8}})#(?P<data>{_HEX}*)\s*$"
)
# "  can0  0EC   [8]  32 2A 32 05 00 00 01 00"      candump (human readable)
_RE_HR = re.compile(
    rf"^\s*(?:\((?P<ts>\d+\.\d+)\)\s+)?(?P<if>\w+)\s+(?P<id>{_HEX}{{3,8}})\s+"
    rf"\[(?P<dlc>\d+)\]\s+(?P<data>(?:{_HEX}{{2}}\s*)+)$"
)
# "0EC#322A320500000100" or "0EC 32 2A 32 ..." (no interface)
_RE_BARE = re.compile(
    rf"^\s*(?P<id>{_HEX}{{3,8}})[#\s]+(?P<data>(?:{_HEX}{{2}}\s*)+)$"
)


def _hexbytes(s: str) -> bytes:
    s = re.sub(r"[\s,]", "", s)
    if len(s) % 2:
        raise ValueError(f"odd number of hex digits: {s!r}")
    return bytes.fromhex(s)


def parse_line(line: str) -> Optional[Frame]:
    """Parse one candump/SavvyCAN-ish line. Returns None if it is not a frame."""
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None

    for rx in (_RE_LOG, _RE_HR, _RE_BARE):
        m = rx.match(line)
        if m:
            try:
                return Frame(
                    timestamp=float(m.group("ts")) if "ts" in m.groupdict()
                    and m.group("ts") else None,
                    can_id=int(m.group("id"), 16),
                    data=_hexbytes(m.group("data")),
                )
            except ValueError:
                return None
    return None


def parse_csv(path: str) -> Iterator[Frame]:
    """SavvyCAN CSV export: Time,ID,Extended,Dir,Bus,LEN,D1..D8."""
    with open(path, newline="") as fh:
        sniff = fh.read(4096)
        fh.seek(0)
        if "," not in sniff:
            return
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        id_col = cols.get("id")
        if id_col is None:
            return
        time_col = cols.get("time") or cols.get("timestamp")
        dcols = [cols[k] for k in (f"d{i}" for i in range(1, 9)) if k in cols]
        for row in reader:
            raw_id = str(row[id_col]).strip()
            try:
                can_id = int(raw_id, 16) if not raw_id.isdigit() else int(raw_id)
            except ValueError:
                continue
            try:
                data = bytes(int(str(row[c]).strip(), 16) for c in dcols)
            except (ValueError, TypeError):
                continue
            ts = None
            if time_col:
                try:
                    ts = float(row[time_col])
                except (TypeError, ValueError):
                    ts = None
            yield Frame(timestamp=ts, can_id=can_id, data=data)


def frames_from_file(path: str) -> Iterator[Frame]:
    if path.lower().endswith(".csv"):
        got = False
        for f in parse_csv(path):
            got = True
            yield f
        if got:
            return
    with open(path) as fh:
        for line in fh:
            f = parse_line(line)
            if f is not None:
                yield f


# -------------------------------------------------------------------- reports


def report(frames: Sequence[Frame], want_id: int, summary: bool) -> int:
    seen = 0
    last_counter = None
    last_ts = None
    stalls = 0
    gaps = 0
    status_hist = {}
    e_min, e_max = 255, -1
    problems = 0

    for f in frames:
        if f.can_id != want_id:
            continue
        seen += 1
        try:
            d = decode(f.data)
        except ValueError as exc:
            print(f"  bad frame: {exc}")
            problems += 1
            continue

        status_hist[d.status_name] = status_hist.get(d.status_name, 0) + 1
        e_min = min(e_min, d.ethanol_pct)
        e_max = max(e_max, d.ethanol_pct)

        note = ""
        if last_counter is not None and d.counter == last_counter:
            stalls += 1
            note += "  [counter did not advance]"
        elif last_counter is not None and d.counter != (last_counter + 1) % 256:
            note += f"  [counter jumped {last_counter}->{d.counter}]"
        last_counter = d.counter

        if f.timestamp is not None:
            if last_ts is not None and (f.timestamp - last_ts) > 0.25:
                gaps += 1
                note += f"  [gap {1000 * (f.timestamp - last_ts):.0f} ms]"
            last_ts = f.timestamp

        notes = plausibility_notes(d)
        if notes:
            problems += 1
            note += "  [" + "; ".join(notes) + "]"

        if not summary:
            ts = f"{f.timestamp:.3f}  " if f.timestamp is not None else ""
            print(f"{ts}{f.data.hex(' ').upper()}  {d.line()}{note}")

    if seen == 0:
        print(f"no frames with id 0x{want_id:03X} found")
        return 1

    print(f"\n{seen} frames on 0x{want_id:03X}")
    print(f"  ethanol      {e_min} .. {e_max} %")
    print(f"  status       " + ", ".join(f"{k}={v}" for k, v in status_hist.items()))
    print(f"  counter stalls {stalls}   timing gaps > 250 ms {gaps}")
    print(f"  plausibility problems {problems}")
    return 0 if problems == 0 and stalls == 0 else 2


def live(channel: str, bustype: str, want_id: int) -> int:
    try:
        import can  # type: ignore
    except ImportError:
        print(
            "--live needs python-can:  python3 -m pip install python-can",
            file=sys.stderr,
        )
        return 1
    bus = can.interface.Bus(channel=channel, bustype=bustype)
    print(f"listening on {channel} for 0x{want_id:03X}, Ctrl-C to stop")
    try:
        for msg in bus:
            if msg.arbitration_id != want_id or len(msg.data) != 8:
                continue
            d = decode(bytes(msg.data))
            notes = plausibility_notes(d)
            suffix = "  [" + "; ".join(notes) + "]" if notes else ""
            print(f"{msg.timestamp:.3f}  {bytes(msg.data).hex(' ').upper()}  "
                  f"{d.line()}{suffix}")
    except KeyboardInterrupt:
        print()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("frame", nargs="*",
                    help="one candump/SavvyCAN line, or 8 hex bytes")
    ap.add_argument("--file", "-f", help="decode a candump / SavvyCAN log file")
    ap.add_argument("--id", default=hex(DEFAULT_ID),
                    help=f"CAN identifier (default {DEFAULT_ID:#05x})")
    ap.add_argument("--summary", "-s", action="store_true",
                    help="only print the summary, not every frame")
    ap.add_argument("--live", action="store_true",
                    help="read from a live bus (needs python-can)")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--bustype", default="socketcan")
    args = ap.parse_args(argv)

    want_id = int(str(args.id), 0)

    if args.live:
        return live(args.channel, args.bustype, want_id)

    if args.file:
        return report(list(frames_from_file(args.file)), want_id, args.summary)

    if not args.frame:
        ap.print_help()
        return 1

    joined = " ".join(args.frame)
    f = parse_line(joined)
    if f is None:
        # bare list of 8 hex bytes
        try:
            data = _hexbytes(joined)
        except ValueError as exc:
            print(f"cannot parse {joined!r}: {exc}", file=sys.stderr)
            return 1
        f = Frame(timestamp=None, can_id=want_id, data=data)

    if len(f.data) != 8:
        print(f"expected 8 data bytes, got {len(f.data)}", file=sys.stderr)
        return 1

    d = decode(f.data)
    print(f"id        0x{f.can_id:03X}")
    print(f"raw       {f.data.hex(' ').upper()}")
    print(f"ethanol   {d.ethanol_pct} %")
    print(f"fuel temp {d.fuel_temp_c} C")
    print(f"frequency {d.freq_hz} Hz (raw, 2 Hz resolution)")
    print(f"counter   {d.counter}")
    print(f"reserved  {d.reserved[0]}, {d.reserved[1]}")
    print(f"firmware  {d.fw_version}")
    print(f"status    {d.status} = {d.status_name}")
    notes = plausibility_notes(d)
    for n in notes:
        print(f"WARNING   {n}")
    if f.can_id != want_id:
        print(f"WARNING   id 0x{f.can_id:03X} is not the expected "
              f"0x{want_id:03X}")
    return 0 if not notes else 2


if __name__ == "__main__":
    sys.exit(main())
