#!/usr/bin/env python3
"""Run the ERCOSEK clock under Unicorn and measure every raster period.

Brief C4 (issue #44), task 2.  The static decode in
`re/findings/scheduler.md` section 11 says which task each dispatcher
activates and how often; this module *executes* the dispatchers out of
`data/passat_azx_ori.bin` against a virtual Time Base and reports the
measured cadence of every activation-counter byte in
0x7FE5FC-0x7FE644, so the arithmetic of that decode is checked by the CPU
model rather than by hand.

What is really executed
-----------------------
* `os_time_table_dispatch` (0x4768C0) — the reference-A handler.  It walks
  the cyclic {action, delta} time table, calls each action and programs
  TBREF0 (0x6FC204) with the next deadline.  The driver advances the virtual
  Time Base to whatever the code wrote into TBREF0, exactly as the hardware
  would raise the next REFA interrupt.
* `os_raster_select` (0x443F74) — the alarm-1 callback; picks the task set
  from the mode byte 0x7FAB55.
* `os_raster_divider` (0x40BEF0 for task set A, 0x40C064 for set B) — the
  five down-counters that make the 20 / 50 / 100 / 200 / 1000 ms rasters,
  including the adaptive recomputation of the alarm-1 cycle.

What is stubbed, and why
------------------------
* `os_ActivateTask` (0x475E8C) is replaced, **in emulator memory only**, by a
  single `blr`, and a code hook does what the real function's tail does:
  increment the byte at `*(handle + 0x0C)` (0x475E8C..0x476060,
  `lbz r12,0(r28); addi r12,r12,1; stb r12,0(r28)`).  Running the real one
  would need the whole kernel object K, which the boot builds and which this
  harness does not model.  The task *bodies* are therefore never run either —
  we are timing the dispatcher, not the application.
* `os_set_alarm_cycle` (0x476E94) is stubbed the same way; the driver takes
  the new cycle from r4, which is how the 50 ms..62.5 ms stretch would act.
* `mftb`/`mftbu` are rewritten, in emulator memory only, into
  `lwz rD,0x7F10(r13)` / `lwz rD,0x7F14(r13)`, i.e. a load from the scratch
  cell 0x807F00 that the driver keeps equal to the virtual Time Base.
  Unicorn runs a QEMU 603e whose time base is the host clock, so the real
  instruction cannot be used (emu/README.md, limitation 1).

The dump itself is never modified; `Med9Emu` writes flash into its own
Unicorn mapping and this module patches that mapping.

Usage:
    python3 -m emu.os_clock                 # 5 simulated seconds, task set A
    python3 -m emu.os_clock --set b --seconds 2
    python3 -m emu.os_clock --json
"""
from __future__ import annotations

import argparse
import json
import sys

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE

from .core import Med9Emu
from . import memmap as mm                                   # noqa: F401

# --- addresses (re/findings/scheduler.md section 11) ------------------------
OS_ACTIVATE_TASK = 0x475E8C
OS_SET_ALARM_CYCLE = 0x476E94
OS_TIME_TABLE_DISPATCH = 0x4768C0
OS_RASTER_SELECT = 0x443F74          # alarm-1 callback
RASTER_DIVIDER = {"a": 0x40BEF0, "b": 0x40C064}
TIME_TABLE_BASE = {"a": 0x478EE4, "b": 0x478F80}
MODE_BYTE = 0x7FAB55                 # 0 -> task set A, 2 -> task set B
MODE_VALUE = {"a": 0, "b": 2}

TT_CUR = 0x7FE5E0                    # r13-0x1A10, current time-table entry
TT_BASE = 0x7FE5E4                   # r13-0x1A0C, wrap target
TT_DEADLINE = 0x7FE5E8               # r13-0x1A08, accumulated deadline
TBREF0 = 0x6FC204                    # USIU Time Base reference A
FLAGS_LO, FLAGS_HI = 0x7FE5FC, 0x7FE644

# os_raster_counter_init 0x1344C0: the staggered start phases
DIVIDER_COUNTERS = {0x7FC2D0: 1, 0x7FC2D4: 6, 0x7FC2D8: 8,
                    0x7FC2DC: 0x18, 0x7FC2E0: 0x5E}
ALARM1_INCREMENT = 701               # os_SetRelAlarm(1, 701, 35087) @0x11B15C
ALARM1_CYCLE = 35087
# the three cells the raster-divider tail needs, as 0x11DAF8-0x11DB10 and
# 0x40C124-0x40C13C set them: current alarm cycle, load factor, /5 counter
INIT_CELLS = {0x7FC2E4: (0x890F, 4), 0x7FD415: (0x80, 1), 0x7FAB54: (5, 1)}

TB_CELL = 0x807F00                   # scratch: virtual TBL / TBU
R13 = 0x7FFFF0
BLR = 0x4E800020
PATCH_RANGES = ((0x40B000, 0x40D000), (0x443000, 0x444000),
                (0x475000, 0x479000))
NS_PER_TICK = 285                    # kernel config +0xAC


def _mftb_encodings():
    """(match, replacement) pairs for mftb/mfspr of TBL (268) and TBU (269)."""
    out = []
    for spr, disp in ((268, TB_CELL - R13), (269, TB_CELL + 4 - R13)):
        enc = ((spr & 0x1F) << 5) | (spr >> 5)
        for xo in (371, 339):                    # mftb, mfspr
            for rd in range(32):
                word = 0x7C000000 | (rd << 21) | (enc << 11) | (xo << 1)
                lwz = 0x80000000 | (rd << 21) | (13 << 16) | (disp & 0xFFFF)
                out.append((word, lwz))
    return out


class OsClock:
    """Virtual-Time-Base driver around the two ERCOSEK dispatchers."""

    def __init__(self, dump_path: str = "data/passat_azx_ori.bin",
                 task_set: str = "a"):
        if task_set not in ("a", "b"):
            raise ValueError("task_set must be 'a' or 'b'")
        self.set = task_set
        self.emu = Med9Emu(dump_path, log_periph=False)
        self.tb = 0
        self.tbref0: int | None = None
        self.alarm_cycle = ALARM1_CYCLE
        self.activations: list[tuple[int, int, int]] = []   # tb, handle, flag
        self.alarm_cycle_writes: list[tuple[int, int]] = []
        self._install()

    # -- setup ------------------------------------------------------------
    def _install(self) -> None:
        self.emu.reset()
        uc = self.emu.uc
        uc.mem_write(OS_ACTIVATE_TASK, BLR.to_bytes(4, "big"))
        uc.mem_write(OS_SET_ALARM_CYCLE, BLR.to_bytes(4, "big"))
        uc.hook_add(UC_HOOK_CODE, self._on_activate,
                    begin=OS_ACTIVATE_TASK, end=OS_ACTIVATE_TASK)
        uc.hook_add(UC_HOOK_CODE, self._on_set_cycle,
                    begin=OS_SET_ALARM_CYCLE, end=OS_SET_ALARM_CYCLE)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_tbref0,
                    begin=TBREF0, end=TBREF0 + 3)
        self._patch_mftb()
        # the scheduler state os_start_time_table (0x478034) would set up
        base = TIME_TABLE_BASE[self.set]
        self.emu.write(TT_CUR, base, 4)
        self.emu.write(TT_BASE, base, 4)
        self.emu.write(TT_DEADLINE, 0, 4)
        self.emu.write(MODE_BYTE, MODE_VALUE[self.set], 1)
        for addr, val in DIVIDER_COUNTERS.items():
            self.emu.write(addr, val, 4)
        for addr, (val, size) in INIT_CELLS.items():
            self.emu.write(addr, val, size)
        self._set_tb(0)

    def _patch_mftb(self) -> int:
        uc, n = self.emu.uc, 0
        table = dict(_mftb_encodings())
        for lo, hi in PATCH_RANGES:
            blob = bytearray(uc.mem_read(lo, hi - lo))
            for off in range(0, len(blob), 4):
                word = int.from_bytes(blob[off:off + 4], "big")
                if word in table:
                    blob[off:off + 4] = table[word].to_bytes(4, "big")
                    n += 1
            uc.mem_write(lo, bytes(blob))
        return n

    def _set_tb(self, tb: int) -> None:
        self.tb = tb
        self.emu.write(TB_CELL, tb & 0xFFFFFFFF, 4)
        self.emu.write(TB_CELL + 4, (tb >> 32) & 0xFFFFFFFF, 4)

    # -- hooks ------------------------------------------------------------
    def _on_activate(self, uc, address, size, ud):
        handle = self.emu.uc.reg_read(_R3)
        flag = self.emu.read_u32(handle + 0x0C)
        cur = self.emu.read(flag, 1)[0]
        self.emu.write(flag, (cur + 1) & 0xFF, 1)
        self.activations.append((self.tb, handle, flag))

    def _on_set_cycle(self, uc, address, size, ud):
        alarm = self.emu.uc.reg_read(_R3)
        cycle = self.emu.uc.reg_read(_R4)
        self.alarm_cycle_writes.append((alarm, cycle))
        if alarm == 1 and cycle:
            self.alarm_cycle = cycle

    def _on_tbref0(self, uc, access, address, size, value, ud):
        if size == 4:
            self.tbref0 = value & 0xFFFFFFFF

    # -- the run ----------------------------------------------------------
    def run(self, ticks: int) -> dict:
        """Advance the virtual Time Base by `ticks` and return the result."""
        end = self.tb + ticks
        next_alarm = self.tb + ALARM1_INCREMENT
        self.tbref0 = self.tb                      # reference A is due at once
        guard = 0
        while True:
            guard += 1
            if guard > 4_000_000:
                raise RuntimeError("os_clock made no progress")
            nxt = min(self.tbref0, next_alarm)
            if nxt > end:
                break
            self._set_tb(nxt)
            if nxt == self.tbref0:
                before = self.tbref0
                r = self.emu.call(OS_TIME_TABLE_DISPATCH, reset=False)
                if not r.ok:
                    raise RuntimeError("time-table dispatch faulted: %s"
                                       % r.summary())
                if self.tbref0 <= before:
                    raise RuntimeError("TBREF0 did not advance past %d" % before)
            if nxt == next_alarm:
                r = self.emu.call(OS_RASTER_SELECT, reset=False)
                if not r.ok:
                    raise RuntimeError("alarm callback faulted: %s" % r.summary())
                r = self.emu.call(RASTER_DIVIDER[self.set], reset=False)
                if not r.ok:
                    raise RuntimeError("raster divider faulted: %s" % r.summary())
                next_alarm += self.alarm_cycle
        return self.report()

    def report(self) -> dict:
        per_flag: dict[int, list[int]] = {}
        for tb, _handle, flag in self.activations:
            per_flag.setdefault(flag, []).append(tb)
        out = {}
        for flag, ts in sorted(per_flag.items()):
            gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
            if not gaps:
                continue
            out[flag] = {
                "n": len(ts), "min_ticks": min(gaps), "max_ticks": max(gaps),
                "mean_ticks": sum(gaps) / len(gaps),
                "period_ms": sum(gaps) / len(gaps) * NS_PER_TICK / 1e6,
            }
        return out


def _lazy_regs():
    from unicorn.ppc_const import UC_PPC_REG_3, UC_PPC_REG_4
    return UC_PPC_REG_3, UC_PPC_REG_4


_R3, _R4 = _lazy_regs()


def task_names(dump_path: str) -> dict:
    """{flag address: (task id, entry)} from tools/ercosek_tasks.py."""
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import ercosek_tasks as et
    img = et.Image(dump_path)
    return {r["flag"]: (r["id"], r["entry"])
            for r in et.decode_thunks(img).values()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default="data/passat_azx_ori.bin")
    ap.add_argument("--set", dest="task_set", choices=("a", "b"), default="a")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ticks = int(args.seconds * 1e9 / NS_PER_TICK)
    clock = OsClock(args.dump, args.task_set)
    rep = clock.run(ticks)
    names = task_names(args.dump)
    if args.json:
        print(json.dumps({"%#08x" % k: v for k, v in rep.items()},
                         indent=2, sort_keys=True))
        return 0
    print("task set %s, %.1f simulated seconds (%d Time Base ticks, "
          "285 ns each)" % (args.task_set.upper(), args.seconds, ticks))
    print("alarm-1 cycle ended at %d ticks\n" % clock.alarm_cycle)
    print("flag      id  entry     activations   period (ticks)   period (ms)")
    for flag, s in sorted(rep.items(), key=lambda kv: kv[1]["mean_ticks"]):
        tid, entry = names.get(flag, ("?", 0))
        jitter = "" if s["min_ticks"] == s["max_ticks"] else \
            "  [%d..%d]" % (s["min_ticks"], s["max_ticks"])
        print("%08X  %2s  %08X  %11d   %14d   %8.3f%s"
              % (flag, tid, entry or 0, s["n"], s["mean_ticks"],
                 s["period_ms"], jitter))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
