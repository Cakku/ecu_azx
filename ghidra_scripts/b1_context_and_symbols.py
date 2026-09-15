#!/usr/bin/env python3
# Set the per-function r2 (SDA2) context and apply the brief-B1 symbols.
# @category MED9
# @runtime PyGhidra
"""b1_context_and_symbols.py -- issues #8 (r2 context) and #11 (scheduler).

Two jobs, both idempotent:

1. **r2 context (issue #8).**  ``med9_setup.py`` sets r2 = 0x5C9FF0 over all
   code and, with ``--boot-r2``, 0x017FF0 over the blanket range
   0x001000-0x01FFFF.  That blanket range is wrong at its top end: 100
   application functions live at 0x019948-0x01E848 (``tools/callgraph.py``).
   This script computes the boot module from the call graph -- every function
   reachable from ``boot_start`` (0x1004) and ``boot_main_init`` (0x12328)
   without passing through an application SDA-setup routine -- and sets
   r2 = 0x017FF0 over exactly those bodies, 0x5C9FF0 everywhere else.

2. **Symbols (issues #8 and #11)** with the confidence tag in the plate
   comment, so ``export_symbols.py`` round-trips it.

Run as a Ghidra post-script; the single argument is the repository root
(needed to import ``tools/callgraph.py``)::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B1 med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript b1_context_and_symbols.py "$PWD"

Violations are listed by ``tools/r2_context.py`` (standalone, no Ghidra).
"""
from __future__ import annotations

import os
import sys

R2_BOOT = 0x017FF0
R2_APP = 0x5C9FF0
BOOT_SEEDS = (0x1004, 0x12328)
APP_SDA_SETUP = (0x986AC, 0x9E3E0, 0x405588)
CODE_RANGES = ((0x000000, 0x1FFFFF), (0x404000, 0x47FFFF))

# Names an earlier brief corrected but med9_setup.py still seeds, so every
# export_symbols.py run would append a duplicate row for that address.
STALE_LABELS = ((0x000000, "tbl_exception_vectors"),)

# (address, name, kind, plate comment).  kind: "func" | "label" | "data"
SYMBOLS = [
    # ---- correction carried over from A2 (issue #6) -----------------------
    # med9_setup.py still seeds the stale name `tbl_exception_vectors` at 0x0.
    # A2 corrected it to `tbl_etr_branch_table` in re/symbols.csv, but the
    # Ghidra project keeps the old one, so every export_symbols.py run appends
    # a duplicate row for 0x000000.  Rename it here so the round trip is clean.
    (0x000000, "tbl_etr_branch_table", "data",
     "VERIFIED-STATIC (A2, issue #6, corrected 2026-09-15). BBC exception-"
     "table-relocation branch table, 32 x 8 B, MPC561RM Table 4-1 -- not a "
     "0x100-spaced vector table. Renamed from the stale `tbl_exception_vectors` "
     "that med9_setup.py seeds."),
    # ---- issue #8: boot module -------------------------------------------
    (0x0110D0, "boot_swsr_service", "func",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Watchdog service: writes "
     "0x556C then 0xAA39 to SWSR (0x6FC00E). Called through mtlr/blrl from "
     "boot_main_init at 0x12480, 0x12538 and 0x125C0."),
    (0x011350, "decram_routine_c_src", "label",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Third DECRAM-resident "
     "routine, 0x1D4 bytes, copied to 0x6F8000 by the loop at 0x12F78-0x12F80 "
     "and called at 0x12F84. emulation_boot_path.md section 4 lists only the "
     "two at 0x10FEC and 0x11118."),
    (0x012D4C, "boot_select_code_directory", "func",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Picks the code/calibration "
     "directory: if the word at 0x5FB0 equals 0x11223344 it takes the "
     "addresses from 0x5FB4-0x5FC0, otherwise the defaults 0x80100 (code) and "
     "0x1C0100/0x1C0000 (calibration). Result goes to RAM 0x7FE9F0 (r13-0x1600) "
     "and DECRAM 0x6F8410-0x6F8420. In this dump 0x5FB0 is 0xFFFFFFFF, so the "
     "default branch is taken."),
    (0x080100, "tbl_code_sections", "data",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Code-section directory. "
     "+0x20 = 0x9E3B4 is the application entry that boot_main_init calls at "
     "0x1307C (lwz r9,-0x1600(r13); lwz r9,0x20(r9); mtlr; blrl)."),
    (0x080000, "tbl_etr_branch_table_app", "data",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Second BBC exception-table-"
     "relocation branch table, 32 x 8 B, same layout as the one at 0x0 "
     "(MPC561RM Table 4-1). Targets are the application handlers at "
     "0x4055A0-0x4062A4 in on-chip flash; +0x28 (external interrupt) is "
     "ba 0x0B0B4C and +0xE8 is ba 0x0B4458."),
    (0x09E3B4, "app_entry_crt0", "func",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Application entry. Sets "
     "r1 = 0x7FF768, zeroes 0x80002C-0x80007C and 0x800080-0x8000E8, then "
     "r13 = 0x7FFFF0 and r2 = 0x5C9FF0 (that pair is app_sda_setup_b at "
     "0x9E3E0). Reached from boot_main_init 0x1307C via tbl_code_sections+0x20."),
    (0x0B0B4C, "unexpected_ext_interrupt", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). li r3,0xCA; b 0xBA444. "
     "Target of tbl_etr_branch_table_app+0x28 (external interrupt), which the "
     "table at 0x0 chains to. Ends in the fatal spin, so the external-interrupt "
     "vector *in the two tables we can see* is fatal; the live table is at "
     "0x400000 (the missing 16 KB)."),
    (0x0B0B30, "tbl_isr_task_control_blocks", "data",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Seven pointers to "
     "tbl_os_task_control_blocks entries 12-18 (+8), i.e. the ERCOSEK ISR "
     "tasks with ids 1-7."),
    (0x0BA444, "app_fatal_exception_handler", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Stores the exception index "
     "from r3 to RAM 0x7F8450 (r13-0x7BA0), sets MSR = 0x2942 via 0x6D360, "
     "then loops writing 0x2222 to 0x704002 forever. The boot's equivalent is "
     "fatal_exception_handler at 0x110F0."),
    (0x0862F4, "reloc_enter_ram_driver", "func",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Computes the flash->RAM "
     "relocation delta 0x804800 - 0x081A00 = 0x782E00, stores it at "
     "r13-0x2B8C/-0x2B88 and loads r2 = 0x5C9FF0 + 0x782E00 = 0xD4CDF0 at "
     "0x86330. That is the unexplained r2 outlier of docs/02 section 4: it is "
     "the application SDA2 base with the relocation delta folded in. Harmless: "
     "the relocated block 0x81A00-0x85400 contains zero r2-relative "
     "references, and reloc_leave_ram_driver restores r2 = 0x5C9FF0."),
    (0x0862A4, "reloc_leave_ram_driver", "func",
     "VERIFIED-STATIC (B1, issue #8, 2026-09-15). Negates both relocation "
     "deltas and restores r2 = 0x5C9FF0 at 0x862DC."),
    # ---- issue #11: ERCOSEK kernel ---------------------------------------
    (0x0345D0, "str_ercosek_version", "data",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). "
     "'ERCOSEK   V4.1.16 MPC56x (c)ETAS Nov 29 2001'. Second copy at 0x9C3F0."),
    (0x099D30, "os_alarm_dispatch", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). ERCOSEK alarm expiry and "
     "reschedule. Walks the deadline list sorted through K+0x70, compares "
     "K+0x78[i] against mftb TBL, adds K+0x74[i] (the cycle) and reinserts, "
     "then calls K+0x60[i] via mtlr/blrl. Programs TBREF1 (0x6FC208) with the "
     "next deadline and enables TBSCR[REFBE] (ori 0x44 at 0x99DA4). The "
     "on-chip instance of the same code is at 0x476630."),
    (0x476630, "os_alarm_dispatch_int", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). On-chip instance of "
     "os_alarm_dispatch; identical code with all r13 displacements 0x18 lower."),
    (0x476EE8, "os_SetRelAlarm", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). OSEK SetRelAlarm(r3 = alarm "
     "id, r4 = increment, r5 = cycle). Reads K+0x74[id]; -1 means the alarm is "
     "free, otherwise it reports error 7. Called with (1, 701, 35087) at "
     "0x11B15C = first shot 1 ms, cycle 50 ms."),
    (0x477204, "os_set_deadline_timer", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). SetDeadline(r3 = timer id, "
     "r4 = delta in Time Base ticks): K+0x6C[id*8] = mftb TBL + delta and "
     "K+0x6C[id*8+4] = ~that (redundant copy). The raster tasks re-arm their "
     "own timer at every activation, which is what fixes their periods."),
    (0x47736C, "os_check_deadline_timer", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Checks one deadline timer; "
     "returns non-zero when it has expired. Called for ids 0-3 by "
     "os_deadline_supervisor and for id 0 by task_20ms_epilogue."),
    (0x478634, "tbl_os_task_control_blocks", "data",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). 25 ERCOSEK task control "
     "blocks, stride 0x24: +0x00 task entry, +0x04 common 0x4764FC, +0x08 self "
     "pointer, +0x0C priority, +0x10 = 1, +0x14 per-task activation flag byte "
     "in 0x7FE5FC-0x7FE644, +0x18 task id. Priorities seen: 0x00 (background), "
     "0x08, 0x09, 0x0A, 0x0B."),
    (0x478B4C, "tbl_os_prio_intmask", "data",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Priority -> interrupt-mask "
     "table, 8-byte entries (0x6FC048, 0x6FC04C). Values run 0x82000001/"
     "0x33D00000 down to 0x82000000/0x00000000. Loaded as r3 by the seven ISR "
     "wrappers at 0x4171B0, 0x41733C, 0x4174CC, 0x41765C, 0x4177EC, 0x41797C "
     "and 0x417B0C."),
    (0x11B188, "task_background", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). ERCOSEK task control block "
     "0, priority 0x00 -- the background/init task. 79 consecutive bl calls, "
     "then b 0x11B044, which arms the four deadline timers (10, 20, 150 and "
     "1500 ms) and SetRelAlarm(1, 1 ms, 50 ms)."),
    (0x11EBF4, "task_10ms", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). TCB 20, priority 0x0B. "
     "Its epilogue task_10ms_epilogue re-arms deadline timer 2 with 7017 Time "
     "Base ticks = 10 ms at every activation, so the activation period is "
     "10 ms. The on-chip sibling at the same priority is task_10ms_int."),
    (0x11EBDC, "task_10ms_epilogue", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Increments the 10 ms "
     "activation counter at r13-0x2890 and tail-calls "
     "os_set_deadline_timer(2, 7017 = 10 ms)."),
    (0x4240C8, "task_10ms_int", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). TCB 7, priority 0x0B, "
     "on-chip flash. Re-arms deadline timer 2 with 10 ms at 0x42408C."),
    (0x11EC34, "task_20ms", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). TCB 21, priority 0x0A. "
     "bl 0x11ED00; bl 0x630C0; b task_20ms_epilogue, which re-arms deadline "
     "timer 3 with 14035 ticks = 20 ms at every activation."),
    (0x11ED50, "task_20ms_epilogue", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Increments the 20 ms "
     "counter at r13-0x2878, calls 0xB5578, re-arms "
     "os_set_deadline_timer(3, 14035 = 20 ms), then counts r13-0x3D24 down "
     "from 5 -- i.e. every fifth activation, every 100 ms -- and on that pass "
     "checks deadline timer 0 (the 100 ms task's) and halts with code 0x74 if "
     "it has expired."),
    (0x424900, "task_20ms_int", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). TCB 8, priority 0x0A, "
     "on-chip flash. Re-arms deadline timer 3 with 20 ms at 0x4248AC."),
    (0x1205A0, "task_100ms", "func",
     "HYPOTHESIS for the 100 ms period, VERIFIED-STATIC for everything else "
     "(B1, issue #11, 2026-09-15). TCB 23, priority 0x09. 66 consecutive "
     "no-argument bl calls; the last, at 0x1206B0, reaches "
     "os_deadline_supervisor, which re-arms deadline timer 0 with 105263 "
     "ticks = 150 ms. task_20ms_epilogue checks that timer every fifth 20 ms "
     "activation, i.e. every 100 ms, so the period is 100 ms with a 1.5x "
     "deadline window (a 150 ms period could not survive a 100 ms check "
     "cadence). Upper bound 150 ms is VERIFIED."),
    (0x4328E4, "task_100ms_int", "func",
     "HYPOTHESIS for the period (B1, issue #11, 2026-09-15). TCB 10, priority "
     "0x09, on-chip flash; 183 consecutive bl calls. Same priority as "
     "task_100ms and also reaches os_deadline_supervisor."),
    (0x120FAC, "task_1000ms", "func",
     "HYPOTHESIS (B1, issue #11, 2026-09-15). TCB 24, priority 0x08, the "
     "slowest raster. Deadline timer 1 has a 1052631-tick = 1500 ms window."),
    (0x45CAC4, "task_1000ms_int", "func",
     "HYPOTHESIS (B1, issue #11, 2026-09-15). TCB 11, priority 0x08, on-chip "
     "flash; 161 consecutive bl calls."),
    (0x120570, "task_100ms_epilogue", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Called at 0x1206B0, the "
     "last call of task_100ms. bl 0x1447C0; bl 0x13CA1C; increments the "
     "activation counter at r13-0x2898; b os_deadline_supervisor."),
    (0x40BDC0, "os_deadline_supervisor", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Calls "
     "os_check_deadline_timer for ids 0, 1, 2 and 3; if all are still within "
     "their window it calls 0x4708F4 and re-arms timer 0 with 105263 ticks = "
     "150 ms. Reached once per activation of task_100ms."),
    (0x12067C, "hook_site_100ms", "label",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Chosen patch point for "
     "issue #27. Original word 4BFFE9B1 = bl 0x11F02C (a four-instruction "
     "leaf). The instructions on both sides are themselves argument-less bl, "
     "so no value in r3-r12 is live across this call: the whole volatile set "
     "is free for a stub. Runs once per activation of task_100ms."),
    (0x11F02C, "clr_ram_7FE889_800E18", "func",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). The leaf originally called "
     "from hook_site_100ms: li r12,0; stb r12,-0x1767(r13) (RAM 0x7FE889); "
     "sth r12,0xE28(r13) (RAM 0x800E18); blr. No arguments, no result."),
    # ---- USIU timer registers (issue #11) ---------------------------------
    (0x6FC00E, "USIU_SWSR", "label",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Software service register; "
     "boot_swsr_service writes 0x556C/0xAA39. MPC561RM Table 5-1 + "
     "re/findings/mpc5xx_registers.md section 9."),
    (0x6FC200, "USIU_TBSCR", "label",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Time Base status and "
     "control, halfword. os_alarm_dispatch does lhz/andi 0xFF4B/sth to clear "
     "REFB and disable REFBE, and ori 0x44 to clear REFB and enable REFBE."),
    (0x6FC204, "USIU_TBREF0", "label",
     "HYPOTHESIS (B1, issue #11, 2026-09-15). Time Base reference A. Not "
     "written anywhere in the dump; named by position from TBSCR."),
    (0x6FC208, "USIU_TBREF1", "label",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Time Base reference B. "
     "os_alarm_dispatch writes the next deadline here (stw at 0x99D98 and "
     "0x99DB8) and enables TBSCR[REFBE], so REFB is the scheduler's timer "
     "interrupt source."),
    (0x6FC240, "USIU_PISCR", "label",
     "VERIFIED-STATIC (B1, issue #11, 2026-09-15). Periodic Interrupt status "
     "and control, halfword. Written as (0x100 << level) | 5 at 0x99814 and "
     "0x999E8 (one-hot PIRQ plus PIE and PTE) and masked with 0x83 at 0x9A360 "
     "to switch the periodic interrupt off -- the PIT is used as a "
     "software-triggerable interrupt at a chosen SIU level, not as the tick."),
    (0x6FC048, "USIU_SIMASK2", "label",
     "HYPOTHESIS for the name, VERIFIED-STATIC for the use (B1, issue #11, "
     "2026-09-15). 32-bit interrupt mask, saved and restored in pairs with "
     "0x6FC04C around every ERCOSEK critical section and loaded per task "
     "priority from tbl_os_prio_intmask. Register name from "
     "re/findings/mpc5xx_registers.md section 9."),
    (0x6FC04C, "USIU_SIMASK3", "label",
     "HYPOTHESIS for the name, VERIFIED-STATIC for the use (B1, issue #11, "
     "2026-09-15). Second half of the interrupt-mask pair; see USIU_SIMASK2."),
]


def _boot_functions(repo_root, image):
    sys.path.insert(0, os.path.join(repo_root, "tools"))
    from callgraph import Image, scan_bl_targets, reachable  # noqa: E402

    img = Image(image)
    entries = scan_bl_targets(img)
    fns = reachable(img, entries, BOOT_SEEDS, APP_SDA_SETUP)
    ranges = []
    for fn in fns.values():
        addrs = sorted(fn.insns)
        start = prev = addrs[0]
        for a in addrs[1:]:
            if a != prev + 4:
                ranges.append((start, prev + 3))
                start = a
            prev = a
        ranges.append((start, prev + 3))
    return sorted(fns), _merge(ranges)


def _merge(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


class Apply:
    def __init__(self, program, monitor, repo_root):
        self.program = program
        self.monitor = monitor
        self.repo_root = repo_root
        self.space = program.getAddressFactory().getDefaultAddressSpace()
        self.lines = []

    def addr(self, value):
        return self.space.getAddress(value)

    def say(self, text):
        self.lines.append(text)
        print("[b1] " + text)

    # -- issue #8 ---------------------------------------------------------
    def set_r2_context(self):
        from java.math import BigInteger

        image = os.path.join(self.repo_root, "data", "passat_azx_ori.bin")
        boot_fns, boot_ranges = _boot_functions(self.repo_root, image)
        ctx = self.program.getProgramContext()
        r2 = ctx.getRegister("r2")
        if r2 is None:
            raise RuntimeError("PowerPC:BE:32:default did not provide r2")

        for start, end in CODE_RANGES:
            ctx.setValue(r2, self.addr(start), self.addr(end),
                         BigInteger.valueOf(R2_APP))
        self.say("r2 = %#08X over %s"
                 % (R2_APP, ", ".join("%06X-%06X" % r for r in CODE_RANGES)))

        total = 0
        for start, end in boot_ranges:
            ctx.setValue(r2, self.addr(start), self.addr(end),
                         BigInteger.valueOf(R2_BOOT))
            total += end - start + 1
        self.say("r2 = %#08X over %d boot functions in %d ranges (%d bytes), "
                 "%06X-%06X"
                 % (R2_BOOT, len(boot_fns), len(boot_ranges), total,
                    boot_ranges[0][0], boot_ranges[-1][1]))
        for start, end in boot_ranges:
            self.say("    boot range %06X-%06X" % (start, end))
        self.boot_fns = boot_fns
        return boot_fns, boot_ranges

    def verify_context(self, boot_ranges):
        """Read the context back at a few probes on each side."""
        ctx = self.program.getProgramContext()
        r2 = ctx.getRegister("r2")
        probes = [(0x1004, R2_BOOT), (0x12328, R2_BOOT), (0x11E44, R2_BOOT),
                  (0x1978C, R2_BOOT), (0x19948, R2_APP), (0x1E848, R2_APP),
                  (0x86330, R2_APP), (0x1205A0, R2_APP), (0x478634, R2_APP)]
        bad = 0
        for address, expect in probes:
            value = ctx.getValue(r2, self.addr(address), False)
            got = None if value is None else int(value.longValue())
            ok = got == expect
            bad += 0 if ok else 1
            self.say("    probe %08X r2 = %s (expected %#08X) %s"
                     % (address, "None" if got is None else "%#08X" % got,
                        expect, "ok" if ok else "MISMATCH"))
        return bad

    # -- symbols ----------------------------------------------------------
    def drop_stale_labels(self):
        """Remove names that a later brief corrected but the project still has."""
        symtab = self.program.getSymbolTable()
        dropped = 0
        for address, stale in STALE_LABELS:
            for sym in symtab.getSymbols(self.addr(address)):
                if sym.getName() == stale:
                    sym.delete()
                    dropped += 1
                    self.say("    dropped stale label %s at %08X" % (stale, address))
        if not dropped:
            self.say("    no stale labels to drop")

    def apply_symbols(self):
        from ghidra.program.model.symbol import SourceType
        from ghidra.app.cmd.function import CreateFunctionCmd

        listing = self.program.getListing()
        symtab = self.program.getSymbolTable()
        created = renamed = commented = skipped = 0

        for address, name, kind, comment in SYMBOLS:
            a = self.addr(address)
            block = self.program.getMemory().getBlock(a)
            if block is None:
                self.say("    skipped %s: no memory block at %08X" % (name, address))
                skipped += 1
                continue

            if kind == "func":
                func = listing.getFunctionAt(a)
                if func is None:
                    CreateFunctionCmd(a).applyTo(self.program, self.monitor)
                    func = listing.getFunctionAt(a)
                if func is not None:
                    if func.getName() != name:
                        func.setName(name, SourceType.USER_DEFINED)
                        renamed += 1
                    func.setComment(comment)
                    commented += 1
                    continue
                # fall through to a plain label if the function would not take
                kind = "label"

            existing = [s for s in symtab.getSymbols(a) if s.getName() == name]
            if not existing:
                symtab.createLabel(a, name, SourceType.USER_DEFINED)
                created += 1
            listing.setComment(a, 3, comment)        # 3 = PLATE_COMMENT
            commented += 1

        self.say("symbols: %d labels created, %d functions renamed, "
                 "%d plate comments, %d skipped"
                 % (created, renamed, commented, skipped))

    def run(self):
        boot_fns, boot_ranges = self.set_r2_context()
        bad = self.verify_context(boot_ranges)
        self.drop_stale_labels()
        self.apply_symbols()
        self.say("context probe mismatches: %d" % bad)
        return bad


def _run_as_ghidra_script():
    args = list(getScriptArgs())              # noqa: F821 - injected by Ghidra
    repo_root = args[0] if args else os.getcwd()
    program = currentProgram                  # noqa: F821
    try:
        mon = monitor                         # noqa: F821
    except NameError:
        from ghidra.util.task import TaskMonitor
        mon = TaskMonitor.DUMMY
    job = Apply(program, mon, repo_root)
    tx = program.startTransaction("b1_context_and_symbols")
    ok = False
    try:
        job.run()
        ok = True
    finally:
        program.endTransaction(tx, ok)


try:
    currentProgram                             # noqa: F821, B018
    _IN_GHIDRA = True
except NameError:
    _IN_GHIDRA = False

if _IN_GHIDRA:
    _run_as_ghidra_script()
elif __name__ == "__main__":
    print(__doc__)
    raise SystemExit(2)
