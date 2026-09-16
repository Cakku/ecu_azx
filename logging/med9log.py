#!/usr/bin/env python3
"""Live RAM logger for the Passat 3.2 FSI MED9.1.1, over TP2.0/KWP2000.

Four commands:

``log``     define a session's variables as a KWP dynamic identifier and poll
            it as fast as the bus allows, writing the long-form CSV of
            `logging/README.md` section 1.
``dump``    RAM snapshots over RequestUpload, for the dynamic half of #23:
            a ``.bin`` plus a ``.json`` manifest `tools/ram_snapshot_diff.py`
            reads directly.
``groups``  read VCDS-style measuring blocks and decode the triples.
``probe``   channel setup, session, TesterPresent -- and the round-trip times.

Every command takes ``--bus interface:channel`` (see
`logging/med9kwp/can_transport.py`) or ``--sim``, which runs
`logging/ecu_sim.py` -- the emulated ECU that answers with the firmware's own
handlers -- on an in-process virtual bus.  **Try every command with ``--sim``
before plugging anything into the car.**

Examples::

    python3 logging/med9log.py probe --sim
    python3 logging/med9log.py log --sim --seconds 2 \\
            --session logging/sessions/wave_b_confirm.json -o /tmp/log.csv
    python3 logging/med9log.py groups --sim 1 2 3 106
    python3 logging/med9log.py dump --sim --ranges logging/sessions/ram_snapshot.json \\
            -o work/snap.bin --session-name key-on

and on the bench, with a candleLight/gs_usb adapter::

    python3 logging/med9log.py probe --bus gs_usb:0

Safety: nothing here ever writes to the ECU.  The only services used are
0x10, 0x21, 0x27, 0x2C, 0x35, 0x36, 0x37 and 0x3E, all read-only.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as _dt
import hashlib
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "logging"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from med9kwp import (  # noqa: E402
    KwpClient, NegativeResponse, Tp20Client, open_link, parse_bus_spec,
)
from med9kwp.kwp import (  # noqa: E402
    DDLI_FIRST, DDLI_LAST, KwpClient as _KwpClient, PROTECTED_WINDOW,
    SESSION_LOGGING, SESSION_UPLOAD, split_around_protected,
)
from med9kwp import vag_formulas  # noqa: E402

DEFAULT_ECU = "03H906032 / 1037382557"
DUMP_SHA256 = "b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3"
TRANSPORT_DDLI = "KWP2000 0x2C/0x21 over TP2.0"
TRANSPORT_UPLOAD = "KWP2000 0x35 RequestUpload + 0x36 TransferData, 0x3E bytes/block"
SYMBOLS_CSV = REPO / "re" / "symbols.csv"
MEASURING_CSV = REPO / "re" / "measuring_vars.csv"


# ---------------------------------------------------------------------------
# session files
# ---------------------------------------------------------------------------
@dataclass
class Variable:
    """One logged variable: where it lives and how its bytes become a number."""

    name: str
    address: int
    size: int = 1
    signed: bool = False
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    source: str = ""
    notes: str = ""
    split_bytes: bool = False
    patch_offset: int | None = None

    @property
    def end(self) -> int:
        return self.address + self.size

    def decode(self, raw: bytes) -> list[tuple[str, float, float, str]]:
        """-> [(name, scaled, raw_int, unit)], one entry unless `split_bytes`."""
        if self.split_bytes:
            return [(f"{self.name}_{i}", float(b), float(b), self.unit)
                    for i, b in enumerate(raw)]
        value = int.from_bytes(raw, "big", signed=self.signed)
        return [(self.name, value * self.scale + self.offset, float(value),
                 self.unit)]


@dataclass
class Session:
    path: str = ""
    ecu: str = DEFAULT_ECU
    dump_sha256: str = DUMP_SHA256
    rate_hint_hz: float = 40.0
    description: str = ""
    patch: str = ""
    variables: list[Variable] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.path).stem or "session"


def _resolve_symbol(symbol: str) -> tuple[int, int]:
    """(address, size) for a name in re/symbols.csv or re/measuring_vars.csv."""
    if SYMBOLS_CSV.is_file():
        with open(SYMBOLS_CSV, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("name") == symbol and row.get("cpu_addr"):
                    size = row.get("size") or "1"
                    try:
                        n = int(str(size), 0)
                    except ValueError:
                        n = 1
                    return int(row["cpu_addr"], 16), max(n, 1)
    if MEASURING_CSV.is_file():
        with open(MEASURING_CSV, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("name_or_blank") == symbol and row.get("ram_addr"):
                    return int(row["ram_addr"], 16), int(row.get("size") or 1)
    raise SystemExit(
        f"symbol {symbol!r} is in neither re/symbols.csv nor "
        "re/measuring_vars.csv; give an explicit \"addr\" instead")


def load_session(path: str | Path, patch_json: str | None = None) -> Session:
    """Read a session file; `patch_json` overrides the patch-relative addresses."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    session = Session(path=str(path),
                      ecu=doc.get("ecu", DEFAULT_ECU),
                      dump_sha256=doc.get("dump_sha256", DUMP_SHA256),
                      rate_hint_hz=float(doc.get("rate_hint_hz", 40)),
                      description=doc.get("description", ""),
                      patch=doc.get("patch", ""))
    patch_ram = None
    if patch_json:
        patch_doc = json.loads(Path(patch_json).read_text(encoding="utf-8"))
        patch_ram = int(str(patch_doc["build"]["ram"]), 16)
    for raw in doc.get("vars", []):
        name = raw.get("name") or raw.get("symbol")
        if not name:
            raise SystemExit(f"{path}: a var has neither \"name\" nor \"symbol\"")
        if raw.get("addr") is not None:
            address = int(str(raw["addr"]), 16)
            size = int(raw.get("size", 1))
        else:
            address, size = _resolve_symbol(raw["symbol"])
            size = int(raw.get("size", size))
        patch_offset = raw.get("patch_offset")
        if patch_ram is not None and patch_offset is not None:
            address = patch_ram + int(patch_offset)
        session.variables.append(Variable(
            name=name, address=address, size=size,
            signed=bool(raw.get("signed", False)),
            scale=float(raw.get("scale", 1.0)),
            offset=float(raw.get("offset", 0.0)),
            unit=raw.get("unit", ""), source=raw.get("source", ""),
            notes=raw.get("notes", ""),
            split_bytes=bool(raw.get("split_bytes", False)),
            patch_offset=patch_offset))
    if not session.variables:
        raise SystemExit(f"{path}: no variables")
    return session


# ---------------------------------------------------------------------------
# turning variables into DDLI chunks
# ---------------------------------------------------------------------------
#: the widest hole `plan_chunks` will read across to save a DDLI slot.  Every
#: bridged byte is transferred on every sample, so this stays small.
MAX_BRIDGE = 8


@dataclass
class Chunk:
    address: int
    size: int

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass
class Plan:
    """Which dynamic ids carry which chunks, and where each variable sits."""

    ids: list[int]
    chunks: dict[int, list[Chunk]]
    #: variable -> (dynamic id, offset into that id's record)
    placement: dict[str, tuple[int, int]]
    merged_gap_bytes: int = 0

    @property
    def total_chunks(self) -> int:
        return sum(len(c) for c in self.chunks.values())

    @property
    def record_bytes(self) -> int:
        return sum(c.size for cl in self.chunks.values() for c in cl)


def plan_chunks(variables: list[Variable], *, first_id: int = DDLI_FIRST,
                max_ids: int = 1 + (DDLI_LAST - DDLI_FIRST)) -> Plan:
    """Merge adjacent variables into chunks and spread them over dynamic ids.

    The firmware allows 20 chunks on 0xF0 and 3 on each of 0xF1..0xF9
    (`re/findings/kwp.md` section 4.1).  Adjacent or overlapping variables
    always merge; if that is still too many chunks, the smallest gaps are
    bridged next (costing a few wasted bytes per sample but saving a slot).
    """
    ordered = sorted(variables, key=lambda v: (v.address, v.size))
    chunks: list[Chunk] = []
    for v in ordered:
        if chunks and v.address <= chunks[-1].end:
            chunks[-1].size = max(chunks[-1].end, v.end) - chunks[-1].address
        else:
            chunks.append(Chunk(v.address, v.size))

    capacity = [_KwpClient.max_chunks(first_id + i) for i in range(max_ids)]
    merged_gap = 0
    while len(chunks) > sum(capacity):
        gaps = [(chunks[i + 1].address - chunks[i].end, i)
                for i in range(len(chunks) - 1)]
        gap, i = min(gaps) if gaps else (MAX_BRIDGE + 1, 0)
        if gap > MAX_BRIDGE:
            break            # bridging this would waste more than it saves
        merged_gap += gap
        chunks[i].size = chunks[i + 1].end - chunks[i].address
        del chunks[i + 1]
    if len(chunks) > sum(capacity):
        raise SystemExit(
            f"{len(chunks)} memory chunks needed but the ECU offers "
            f"{sum(capacity)} across dynamic ids 0xF0-0xF9 "
            "(20 on 0xF0, 3 on each of the rest, kwp.md 4.1). "
            "Split the session file.")

    per_id: dict[int, list[Chunk]] = {}
    ids: list[int] = []
    index = 0
    for slot in range(max_ids):
        if index >= len(chunks):
            break
        lid = first_id + slot
        take = chunks[index:index + capacity[slot]]
        if not take:
            break
        per_id[lid] = take
        ids.append(lid)
        index += len(take)

    placement: dict[str, tuple[int, int]] = {}
    for lid in ids:
        base = 0
        for chunk in per_id[lid]:
            for v in ordered:
                if chunk.address <= v.address and v.end <= chunk.end:
                    placement[v.name] = (lid, base + (v.address - chunk.address))
            base += chunk.size
    missing = [v.name for v in ordered if v.name not in placement]
    if missing:                                              # pragma: no cover
        raise SystemExit(f"internal error: unplaced variables {missing}")
    total = sum(c.size for cl in per_id.values() for c in cl)
    if total > 0xFF:
        raise SystemExit(
            f"the record would be {total} bytes; the ECU's response buffer is "
            "not that big. Split the session file.")
    return Plan(ids=ids, chunks=per_id, placement=placement,
                merged_gap_bytes=merged_gap)


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def connection(args):
    """Yield a connected `KwpClient`, over the simulator or over real hardware."""
    sim = None
    if args.sim:
        from ecu_sim import EcuSimulator
        channel = f"med9sim{os.getpid()}"
        sim = EcuSimulator.on_virtual_bus(channel, seed=0x12345678)
        stack = contextlib.ExitStack()
        stack.enter_context(sim.background())
        spec = f"virtual:{channel}"
    else:
        stack = contextlib.ExitStack()
        spec = args.bus
    link = open_link(parse_bus_spec(spec))
    tp = Tp20Client(link, dest=args.address, rx_id=args.rx_id,
                    timeout=args.timeout)
    try:
        tp.connect()
        yield KwpClient(tp, timeout=args.timeout)
    finally:
        with contextlib.suppress(Exception):
            tp.disconnect()
        link.close()
        if sim is not None:
            stack.close()
            sim.close()
        else:
            stack.close()


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------
def cmd_log(args) -> int:
    session = load_session(args.session, args.patch)
    plan = plan_chunks(session.variables)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"session {session.name}: {len(session.variables)} variables, "
          f"{plan.total_chunks} chunks on "
          + ", ".join(f"{i:#04x}" for i in plan.ids)
          + f", {plan.record_bytes} bytes per sample"
          + (f" ({plan.merged_gap_bytes} bridged)" if plan.merged_gap_bytes else ""))

    samples = 0
    rows = 0
    with connection(args) as kwp, open(out, "w", newline="", encoding="utf-8") as fh:
        kwp.start_session(SESSION_LOGGING)
        for lid in plan.ids:
            kwp.clear_dynamic_id(lid)
            kwp.define_dynamic_id(lid, [(c.address, c.size)
                                        for c in plan.chunks[lid]])
        fh.write(f"# session: {session.description or session.name}, "
                 f"{_dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n")
        fh.write(f"# ecu: {session.ecu}\n")
        fh.write(f"# dump_sha256: {session.dump_sha256}\n")
        fh.write(f"# transport: {TRANSPORT_DDLI}\n")
        fh.write(f"# source_file: {session.path}\n")
        if args.sim:
            fh.write("# simulated: logging/ecu_sim.py -- NOT a recording of an ECU\n")
        writer = csv.writer(fh)
        writer.writerow(["time_s", "var", "value", "unit"])

        started = time.monotonic()
        deadline = started + args.seconds
        period = 1.0 / args.rate if args.rate else 0.0
        next_due = started
        while time.monotonic() < deadline:
            if period:
                sleep = next_due - time.monotonic()
                if sleep > 0:
                    time.sleep(sleep)
                next_due += period
            record: dict[int, bytes] = {}
            try:
                for lid in plan.ids:
                    record[lid] = kwp.read_dynamic_id(lid)
            except NegativeResponse as exc:
                print(f"  ECU refused a sample: {exc}", file=sys.stderr)
                break
            stamp = time.monotonic() - started
            samples += 1
            for v in session.variables:
                lid, offset = plan.placement[v.name]
                raw = record[lid][offset:offset + v.size]
                if len(raw) != v.size:
                    continue
                for name, value, raw_value, unit in v.decode(raw):
                    writer.writerow([f"{stamp:.4f}", name,
                                     f"{value:.6g}", unit])
                    rows += 1
                    if args.raw:
                        writer.writerow([f"{stamp:.4f}", f"{name}_raw",
                                         f"{raw_value:.0f}", "count"])
                        rows += 1
        kwp.tester_present()

    elapsed = time.monotonic() - started
    print(f"{samples} samples in {elapsed:.2f} s "
          f"({samples / elapsed if elapsed else 0:.1f} Hz total, "
          f"{samples / elapsed / max(len(session.variables), 1) if elapsed else 0:.1f} Hz per variable)")
    print(f"{rows} rows -> {out}")
    return 0


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------
def load_ranges(path: str | Path) -> tuple[dict, list[tuple[int, int]]]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[tuple[int, int]] = []
    for r in doc.get("ranges", []):
        start = int(str(r["start"]), 16)
        end = int(str(r["end"]), 16)
        if end < start:
            raise SystemExit(f"{path}: range {start:#08x}-{end:#08x} is inverted")
        out.append((start, end))
    if not out:
        raise SystemExit(f"{path}: no ranges")
    return doc, out


def cmd_dump(args) -> int:
    doc, ranges = load_ranges(args.ranges)
    out_bin = Path(args.output)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    out_json = out_bin.with_suffix(".json")

    total = sum(end - start + 1 for start, end in ranges)
    print(f"{len(ranges)} ranges, {total} bytes, "
          f"{-(-total // 0x3E)} TransferData blocks")

    pieces: list[dict] = []
    blob = bytearray()
    started = time.monotonic()
    with connection(args) as kwp:
        kwp.start_session(SESSION_LOGGING)
        seed = kwp.unlock_level2()
        print(f"  security level 2 granted (seed {seed:#010x})")
        kwp.start_session(SESSION_UPLOAD)
        for start, end in ranges:
            size = end - start + 1
            for sub_start, sub_size in split_around_protected(start, size):
                if (sub_start, sub_size) != (start, size):
                    lo, hi = PROTECTED_WINDOW
                    print(f"  {start:#08x}-{end:#08x} crosses the protected "
                          f"window {lo:#08x}-{hi:#08x}; reading "
                          f"{sub_start:#08x}+{sub_size}")
                data = kwp.upload(sub_start, sub_size)
                pieces.append({
                    "start": f"0x{sub_start:06X}",
                    "end": f"0x{sub_start + sub_size - 1:06X}",
                    "bytes": sub_size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data": data.hex(),
                })
                blob += data
                print(f"  {sub_start:#08x}-{sub_start + sub_size - 1:#08x}  "
                      f"{sub_size:6d} B  ok")
        kwp.start_session(SESSION_LOGGING)

    out_bin.write_bytes(bytes(blob))
    manifest = {
        "session": args.session_name,
        "taken": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
        "ecu": doc.get("ecu", DEFAULT_ECU),
        "dump_sha256": doc.get("dump_sha256", DUMP_SHA256),
        "transport": TRANSPORT_UPLOAD,
        "tool": "logging/med9log.py dump",
        "ranges_source": str(args.ranges),
        "bin": out_bin.name,
        "bin_sha256": hashlib.sha256(bytes(blob)).hexdigest(),
        "bytes": len(blob),
        "seconds": round(time.monotonic() - started, 3),
        "ranges": pieces,
    }
    if args.sim:
        manifest["simulated"] = ("logging/ecu_sim.py -- NOT a recording of an "
                                 "ECU; do not put this in the #23 set")
    out_json.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{len(blob)} bytes in {manifest['seconds']} s -> {out_bin}")
    print(f"manifest -> {out_json}  "
          f"(python3 tools/ram_snapshot_diff.py {out_json} ... )")
    return 0


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
def _print_group(group: int, primary, secondary) -> None:
    print(f"group {group:03d}")
    for i, triple in enumerate(primary, start=1):
        print(f"  field {i}: {vag_formulas.decode(*triple).line()}")
    tail = group + 0x7F
    if any(not vag_formulas.decode(*t).empty for t in secondary):
        print(f"  ...and group {tail:03d}, which shares the answer "
              "(kwp.md 12.3):")
        for i, triple in enumerate(secondary, start=1):
            print(f"  field {i}: {vag_formulas.decode(*triple).line()}")


def cmd_groups(args) -> int:
    if args.formula_table:
        print("VAG measuring-block display formulas known to this tool:")
        for line in vag_formulas.table_lines():
            print(line)
        print("\nAnything else is printed as the raw (formula, A, B) triple.")
        if not args.groups:
            return 0
    wanted = []
    for raw in args.groups:
        g = int(raw, 0)
        if not 1 <= g <= 254:
            raise SystemExit(f"group {g} does not exist (1..254)")
        if g > 0x7F:
            print(f"note: group {g} cannot be requested directly; reading "
                  f"group {g - 0x7F}, whose answer carries it (kwp.md 12.3)")
            g -= 0x7F
        wanted.append(g)
    with connection(args) as kwp:
        kwp.start_session(SESSION_LOGGING)
        for g in wanted:
            try:
                primary, secondary = kwp.read_group(g)
            except NegativeResponse as exc:
                print(f"group {g:03d}: {exc}")
                continue
            _print_group(g, primary, secondary)
    return 0


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------
def cmd_probe(args) -> int:
    sim = None
    stack = contextlib.ExitStack()
    if args.sim:
        from ecu_sim import EcuSimulator
        channel = f"med9sim{os.getpid()}"
        sim = EcuSimulator.on_virtual_bus(channel, seed=0x12345678)
        stack.enter_context(sim.background())
        spec = f"virtual:{channel}"
    else:
        spec = args.bus
    link = open_link(parse_bus_spec(spec))
    print(f"bus: {link.description}")
    tp = Tp20Client(link, dest=args.address, rx_id=args.rx_id,
                    timeout=args.timeout)
    rc = 0
    try:
        tp.connect()
        ch = tp.channel
        print(f"channel setup   {tp.setup_rtt * 1000:7.2f} ms   "
              f"we transmit on {ch.tx_id:#05x}, module on {ch.rx_id:#05x}")
        print(f"parameters                    {ch.params.describe()}")
        kwp = KwpClient(tp, timeout=args.timeout)
        kwp.start_session(SESSION_LOGGING)
        print(f"10 89 session   {kwp.rtt_last * 1000:7.2f} ms")
        times = []
        for _ in range(args.count):
            kwp.tester_present()
            times.append(kwp.rtt_last * 1000)
        print(f"3E x{args.count:<3}        {sum(times) / len(times):7.2f} ms   "
              f"min {min(times):.2f}  max {max(times):.2f}")
        try:
            kwp.clear_dynamic_id(0xF0)
            kwp.define_dynamic_id(0xF0, [(0x7FEE74, 2)])
            reads = []
            for _ in range(args.count):
                value = kwp.read_dynamic_id(0xF0)
                reads.append(kwp.rtt_last * 1000)
            rpm = struct.unpack(">H", value)[0] * 0.25
            print(f"21 F0 x{args.count:<3}     {sum(reads) / len(reads):7.2f} ms   "
                  f"-> {1000 / (sum(reads) / len(reads)):.1f} samples/s, "
                  f"nmot_w = {rpm:.1f} rpm")
        except NegativeResponse as exc:
            print(f"21 F0: {exc}")
            rc = 1
    except Exception as exc:
        print(f"FAILED: {exc}")
        rc = 1
    finally:
        with contextlib.suppress(Exception):
            tp.disconnect()
        link.close()
        stack.close()
        if sim is not None:
            sim.close()
    return rc


# ---------------------------------------------------------------------------
def _add_bus_args(p) -> None:
    p.add_argument("--bus", default="gs_usb:0",
                   help="interface:channel, e.g. gs_usb:0, slcan:/dev/tty.usbmodem1411, "
                        "socketcand:pi.local:29536:can0 (default gs_usb:0)")
    p.add_argument("--sim", action="store_true",
                   help="talk to logging/ecu_sim.py on an in-process virtual bus")
    p.add_argument("--address", type=lambda s: int(s, 0), default=0x01,
                   help="module logical address (default 0x01, engine)")
    p.add_argument("--rx-id", type=lambda s: int(s, 0), default=0x300,
                   help="CAN id we ask the module to transmit on (default 0x300)")
    p.add_argument("--timeout", type=float, default=1.0,
                   help="per-request timeout in seconds")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Try every command with --sim first; it never touches hardware.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("log", help="poll a session's variables into a CSV")
    _add_bus_args(p)
    p.add_argument("--session", required=True, help="logging/sessions/<x>.json")
    p.add_argument("-o", "--output", required=True, help="logs/<date>.csv")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--rate", type=float, default=0.0,
                   help="cap the sample rate in Hz (default: as fast as the bus allows)")
    p.add_argument("--raw", action="store_true",
                   help="also write <var>_raw rows with the unscaled counts")
    p.add_argument("--patch", default=None,
                   help="patches/<name>/patch.json: take patch-relative "
                        "addresses from its build.ram")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("dump", help="RAM snapshot over RequestUpload")
    _add_bus_args(p)
    p.add_argument("--ranges", default=str(REPO / "logging" / "sessions" /
                                           "ram_snapshot.json"))
    p.add_argument("-o", "--output", required=True, help="snapshots/<date>.bin")
    p.add_argument("--session-name", default="unnamed",
                   help="key-on / idle / after-drive / key-cycle-1 ... "
                        "(re/findings/ram.md section 9)")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("groups", help="read VCDS-style measuring blocks")
    _add_bus_args(p)
    p.add_argument("groups", nargs="*", help="group numbers, e.g. 1 2 3 106")
    p.add_argument("--formula-table", action="store_true",
                   help="print what this tool knows about the display formulas")
    p.set_defaults(func=cmd_groups)

    p = sub.add_parser("probe", help="channel setup, session, round-trip times")
    _add_bus_args(p)
    p.add_argument("--count", type=int, default=20)
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
