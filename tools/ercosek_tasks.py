#!/usr/bin/env python3
"""ERCOSEK task/raster decoder: task descriptors, time tables, divider chains.

Everything this prints is read out of `data/passat_azx_ori.bin`; nothing is
hard-coded except the handful of anchor addresses listed in ANCHORS, each of
which is justified in `re/findings/scheduler.md` section 11 (brief C4, #44).

It answers the question "what is the period of every ERCOSEK task": the
activation chain is

    USIU Time Base reference A (TBREF0) -> os_time_table_dispatch (0x4768C0)
        -> a cyclic {action, delta} time table  -> the 1 / 2 / 5 ms rasters
    USIU Time Base reference B (TBREF1) -> os_alarm_dispatch (0x476630)
        -> alarm 1 (cycle 35087 ticks)  -> os_raster_select (0x443F74)
        -> the 10 ms task, whose body runs os_raster_divider (0x40BEF0),
           a chain of five down-counters that activate the
           20 / 50 / 100 / 200 / 1000 ms rasters.

Usage:
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin              # all
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --tasks
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --timetable
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --dividers
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --periods
    python3 tools/ercosek_tasks.py data/passat_azx_ori.bin --json

Tick unit: the kernel configuration record carries the ERCOSEK generator's own
clock description at config+0xA0..0xAC = {56000 kHz, divider 16, 3508
ticks/ms, 285 ns/tick}; SCCR[TBS]=1 makes TMBCLK = system clock / 16 = 3.5 MHz
(MPC561RM Table 8-2), so one configured "millisecond" is 3508 Time Base ticks
and every timing literal in the image is floor(t_ns / 285).
"""
from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import med9lib                                              # noqa: E402

# --- anchors (re/findings/scheduler.md section 11) ---------------------------
ANCHORS = {
    # first and last of the 37 generated ActivateTask thunks; each is
    #   lis r3,HI ; lwz r3,LO(r3) ; b os_ActivateTask
    "thunk_first": 0x0B091C,
    "thunk_last": 0x0B0ACC,
    "os_ActivateTask": 0x475E8C,
    # the two time tables and their {cur, base} descriptors
    "time_table_a": 0x478EE4,          # on-chip task set, installed by os_init
    "time_table_b": 0x478F80,          # external-flash task set
    "os_time_table_wrap": 0x47820C,
    # the two divider chains, one per task set
    "os_raster_divider_a": 0x40BEF0,   # called from task 0x4328E4 @0x432BBC
    "os_raster_divider_b": 0x40C064,   # called from task 0x1205A0 @0x1206AC
    # alarm callback vector (kernel config -> K+0x60)
    "alarm_callbacks": 0x478DF8,
    "n_alarms": 3,
    # os_SetRelAlarm(1, 701, 35087) in os_init at 0x11B15C
    "alarm1_cycle_site": 0x11B15C,
}
NS_PER_TICK = 285              # kernel config +0xAC
TICKS_PER_MS = 3508.0          # kernel config +0xA8, = floor(1e6 / 285)
TB_HZ_REAL = 56_000_000 / 16   # SCCR[TBS]=1 -> TMBCLK = sysclk/16 = 3.5 MHz
WRAPPERS = (0x0B5878, 0x0B5978)   # os process-list prologue/epilogue stubs


class Image:
    def __init__(self, path: str):
        self.buf = med9lib.load_dump(path)

    def w(self, addr: int) -> int:
        return med9lib.u32(self.buf, med9lib.cpu_to_file(addr))


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def decode_thunks(img: Image) -> dict:
    """Return {thunk_addr: task record} for the generated ActivateTask stubs."""
    out = {}
    a = ANCHORS["thunk_first"]
    while a <= ANCHORS["thunk_last"]:
        i0, i1, i2 = img.w(a), img.w(a + 4), img.w(a + 8)
        if (i0 >> 26) != 15 or (i1 >> 26) != 32:            # lis / lwz
            raise ValueError("thunk at 0x%06X is not lis+lwz" % a)
        if (i2 & 0xFC000003) != 0x48000000:                 # b
            raise ValueError("thunk at 0x%06X does not end in b" % a)
        rel = i2 & 0x03FFFFFC
        if rel & 0x02000000:
            rel -= 0x04000000
        if a + 8 + rel != ANCHORS["os_ActivateTask"]:
            raise ValueError("thunk at 0x%06X does not branch to ActivateTask" % a)
        const = ((i0 & 0xFFFF) << 16) + _s16(i1 & 0xFFFF)
        out[a] = task_record(img, img.w(const), const)
        a += 0xC
    return out


def task_record(img: Image, handle: int, const: int | None = None) -> dict:
    """Decode one ERCOSEK task descriptor.

    handle (the OSEK TaskType) points at
        +0x00 process-list pointer   +0x04 priority   +0x08 max activations
        +0x0C address of the activation counter byte (0x7FE5FC..0x7FE644)
        +0x10 task id                +0x14 0          +0x18 pointer to itself
    The process list is an array of function pointers terminated by
    os_TerminateTask = 0x004764FC.  A task linked into the on-chip flash has a
    one-entry list sitting inline in the table; the external-flash tasks point
    into the list block at 0x0B1ED4..0x0B2A40.
    """
    body = img.w(handle)
    procs = []
    a = body
    while len(procs) < 256:
        v = img.w(a)
        if v == 0x004764FC:
            break
        procs.append(v)
        a += 4
    return {
        "handle": handle, "const": const, "proc_list": body,
        "prio": img.w(handle + 4), "max_act": img.w(handle + 8),
        "flag": img.w(handle + 0xC), "id": img.w(handle + 0x10),
        "entry": next((p for p in procs if p not in WRAPPERS), None),
        "n_proc": len(procs),
        "procs": procs,
    }


def decode_time_table(img: Image, base: int, thunks: dict) -> dict:
    """Walk a cyclic {action, delta} time table until os_time_table_wrap."""
    entries, a, t = [], base, 0
    while len(entries) < 256:
        act, d = img.w(a), img.w(a + 4)
        rec = {"addr": a, "action": act, "delta": d, "t": t}
        if act in thunks:
            rec["task_id"] = thunks[act]["id"]
            rec["task_entry"] = thunks[act]["entry"]
            rec["prio"] = thunks[act]["prio"]
        entries.append(rec)
        if act == ANCHORS["os_time_table_wrap"]:
            break
        t += d
        a += 8
    cycle = entries[-1]["t"]
    per_task: dict[int, list] = {}
    for e in entries:
        if "task_id" in e:
            per_task.setdefault(e["task_id"], []).append(e["t"])
    periods = {}
    for tid, ts in per_task.items():
        gaps = {ts[i + 1] - ts[i] for i in range(len(ts) - 1)}
        gaps.add(cycle - ts[-1] + ts[0])
        periods[tid] = {"n": len(ts), "period_ticks": min(gaps),
                        "uniform": len(gaps) == 1}
    return {"base": base, "entries": entries, "cycle_ticks": cycle,
            "periods": periods}


def decode_divider(img: Image, addr: int, thunks: dict) -> list:
    """Decode a chain of `counter -= 1; if 0 { counter = N; ActivateTask(T) }`.

    The generated shape is fixed:
        lwz  r12,D(r13) ; addic. rX,r12,-1 ; stw rX,D(r13) ; bne +0x18
        li   r12,N      ; stw r12,D(r13)
        lis  r3,HI      ; lwz r3,LO(r3)    ; bl os_ActivateTask
    """
    out, a = [], addr
    end = addr + 0x200
    while a < end:
        i = img.w(a)
        if i == 0x4E800020:                                  # blr: end of chain
            break
        if (i >> 26) == 32 and ((i >> 16) & 0x1F) == 13:     # lwz rX,D(r13)
            if (img.w(a + 4) >> 26) == 13 and (img.w(a + 8) >> 26) == 36:
                ctr = 0x7FFFF0 + _s16(i & 0xFFFF)
                reload_i = img.w(a + 0x10)
                if (reload_i >> 26) != 14:                   # li
                    a += 4
                    continue
                n = _s16(reload_i & 0xFFFF)
                lis_i, lwz_i = img.w(a + 0x18), img.w(a + 0x1C)
                if (lis_i >> 26) != 15 or (lwz_i >> 26) != 32:
                    a += 4
                    continue
                const = ((lis_i & 0xFFFF) << 16) + _s16(lwz_i & 0xFFFF)
                rec = task_record(img, img.w(const), const)
                out.append({"site": a, "counter": ctr, "divider": n,
                            "task": rec})
                a += 0x20
                continue
        a += 4
    return out


def alarm_callbacks(img: Image) -> list:
    return [img.w(ANCHORS["alarm_callbacks"] + 4 * i)
            for i in range(ANCHORS["n_alarms"])]


def alarm1_cycle(img: Image) -> int:
    """The cycle of alarm 1, read out of os_SetRelAlarm(1, 701, 35087)."""
    site = ANCHORS["alarm1_cycle_site"]
    lis_i, ori_i = img.w(site - 8), img.w(site - 4)
    if (lis_i >> 26) != 15 or (ori_i >> 26) != 24:
        raise ValueError("alarm-1 cycle site does not hold lis+ori")
    return ((lis_i & 0xFFFF) << 16) | (ori_i & 0xFFFF)


def analyse(img: Image) -> dict:
    thunks = decode_thunks(img)
    tabs = {k: decode_time_table(img, ANCHORS[k], thunks)
            for k in ("time_table_a", "time_table_b")}
    divs = {k: decode_divider(img, ANCHORS[k], thunks)
            for k in ("os_raster_divider_a", "os_raster_divider_b")}
    cyc = alarm1_cycle(img)
    periods = {}
    for tab in tabs.values():
        for tid, p in tab["periods"].items():
            periods[tid] = p["period_ticks"]
    for site, chain in (("a", divs["os_raster_divider_a"]),
                        ("b", divs["os_raster_divider_b"])):
        for step in chain:
            periods[step["task"]["id"]] = cyc * step["divider"]
    # the two tasks the alarm itself activates (os_raster_select 0x443F74)
    for tid in _alarm1_tasks(img, thunks):
        periods[tid] = cyc
    return {"thunks": thunks, "tables": tabs, "dividers": divs,
            "alarm_cycle": cyc, "alarm_callbacks": alarm_callbacks(img),
            "periods": periods}


def _alarm1_tasks(img: Image, thunks: dict) -> list:
    """The task ids os_raster_select (alarm-1 callback) can activate."""
    cb = alarm_callbacks(img)[1]
    out, a = [], cb
    while a < cb + 0x60:
        i = img.w(a)
        if (i >> 26) == 15 and ((i >> 21) & 0x1F) == 3:      # lis r3
            nxt = img.w(a + 4)
            if (nxt >> 26) == 32 and ((nxt >> 21) & 0x1F) == 3:
                const = ((i & 0xFFFF) << 16) + _s16(nxt & 0xFFFF)
                out.append(task_record(img, img.w(const), const)["id"])
                a += 8
                continue
        a += 4
    return out


# --- printing ---------------------------------------------------------------
def ms(ticks: float) -> float:
    """Design period in ms: the ERCOSEK generator's 285 ns per tick."""
    return ticks * NS_PER_TICK / 1e6


def ms_real(ticks: float) -> float:
    """Period on the real part: Time Base = 56 MHz / 16 = 3.5 MHz."""
    return ticks * 1000.0 / TB_HZ_REAL


def print_tasks(res: dict) -> None:
    print("ERCOSEK task descriptors (%d, reached through the ActivateTask "
          "thunk table 0x0B091C..0x0B0AD4)\n" % len(res["thunks"]))
    print("thunk     handle    id  prio  flag      procs  entry     period")
    rows = sorted(res["thunks"].values(), key=lambda r: r["id"])
    for r in rows:
        p = res["periods"].get(r["id"])
        per = "%8.2f ms" % ms(p) if p else "   (event)"
        thunk = next(k for k, v in res["thunks"].items() if v is r)
        print("%08X  %08X  %2d   %2d   %08X  %5d  %08X %s"
              % (thunk, r["handle"], r["id"], r["prio"], r["flag"],
                 r["n_proc"], r["entry"] or 0, per))


def print_timetable(res: dict) -> None:
    for key, tab in res["tables"].items():
        print("\n%s  base 0x%06X  cycle %d ticks = %.2f ms"
              % (key, tab["base"], tab["cycle_ticks"], ms(tab["cycle_ticks"])))
        for e in tab["entries"]:
            tag = ("ActivateTask id=%2d prio=%2d entry=0x%06X"
                   % (e["task_id"], e["prio"], e["task_entry"])
                   if "task_id" in e else "os_time_table_wrap")
            print("  0x%06X  t=%8.3f ms  d=%6d  %s"
                  % (e["addr"], ms(e["t"]), e["delta"], tag))
        for tid, p in sorted(tab["periods"].items()):
            print("  -> task id %2d : %d activations/cycle, period %d ticks "
                  "= %.2f ms%s" % (tid, p["n"], p["period_ticks"],
                                   ms(p["period_ticks"]),
                                   "" if p["uniform"] else "  (NOT uniform)"))


def print_dividers(res: dict) -> None:
    print("\nalarm 1 cycle = %d ticks = %.2f ms  (os_SetRelAlarm(1,701,%d) "
          "at 0x%06X)" % (res["alarm_cycle"], ms(res["alarm_cycle"]),
                          res["alarm_cycle"], ANCHORS["alarm1_cycle_site"]))
    print("alarm callbacks: " + ", ".join("%d -> 0x%06X" % (i, c)
                                          for i, c in
                                          enumerate(res["alarm_callbacks"])))
    for key, chain in res["dividers"].items():
        print("\n%s  0x%06X" % (key, ANCHORS[key]))
        for s in chain:
            t = s["task"]
            print("  0x%06X  counter 0x%06X  /%-3d -> id %2d prio %2d "
                  "entry 0x%06X  = %.2f ms"
                  % (s["site"], s["counter"], s["divider"], t["id"], t["prio"],
                     t["entry"], ms(res["alarm_cycle"] * s["divider"])))


def print_periods(res: dict) -> None:
    print("\nraster periods (design ms = ticks x 285 ns; real ms = ticks / "
          "3.5 MHz, +0.25 %)")
    print("id  prio  entry     ticks    design ms  real ms  task")
    byid = {r["id"]: r for r in res["thunks"].values()}
    for tid, ticks in sorted(res["periods"].items(), key=lambda kv: kv[1]):
        r = byid[tid]
        print("%2d   %2d   %08X  %8d  %9.2f %8.2f  %d process(es)"
              % (tid, r["prio"], r["entry"], ticks, ms(ticks), ms_real(ticks),
                 r["n_proc"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default="data/passat_azx_ori.bin")
    ap.add_argument("--tasks", action="store_true")
    ap.add_argument("--timetable", action="store_true")
    ap.add_argument("--dividers", action="store_true")
    ap.add_argument("--periods", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    res = analyse(Image(args.file))
    if args.json:
        print(json.dumps({"periods_ticks": res["periods"],
                          "alarm_cycle": res["alarm_cycle"],
                          "ticks_per_ms": TICKS_PER_MS,
                          "ns_per_tick": NS_PER_TICK,
                          "tb_hz_real": TB_HZ_REAL}, indent=2, sort_keys=True))
        return 0
    everything = not (args.tasks or args.timetable or args.dividers
                      or args.periods)
    if args.tasks or everything:
        print_tasks(res)
    if args.timetable or everything:
        print_timetable(res)
    if args.dividers or everything:
        print_dividers(res)
    if args.periods or everything:
        print_periods(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
