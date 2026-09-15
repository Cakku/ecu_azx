# Brief B1 — r2 context per function and the scheduler / hook point

Issues: **#8**, **#11**. Prerequisite: brief A1 merged (Ghidra project via
`ghidra_scripts/med9_setup.py`, PyGhidra working). No `sudo`.

Read `00_common_rules.md` first. Facts you start from (`docs/02` §3-§4):
boot sets r2 = 0x17FF0 at file 0x10E8; the application sets r13/r2 =
0x7FFFF0/0x5C9FF0 at 0x986AC, 0x9E3E0 and 0x405588; MSR is written at
0x1044, 0x11104, 0x12448, 0x6D370, 0x71FC8, 0x9D3F0, 0x14454C.

## Tasks
1. **#8 r2 context.** In the Ghidra project build the call graph from
   `boot_start` (0x1004) and `boot_main_init` (0x12328). Every function
   reachable before control passes to an application SDA setup routine is a
   boot-module function: set r2 = 0x17FF0 over its body with
   `ProgramContext.setValue`; leave 0x5C9FF0 elsewhere. Check each side with
   a script: r2-relative loads must resolve to non-0xFF data under the
   assigned base; list violations. Explain the `lis r2,0xD5; addi -0x3210`
   outlier at 0x86330. Write `re/findings/boot.md` (boot flow, module
   boundary, list of boot functions) and update `docs/02` §4.
2. **#11 scheduler.** Find the timer source (USIU PIT/RTC/TB registers at
   0x6FC200-0x6FC248 have refs at file 0x99D50-0x9A360 and in the on-chip
   flash 0x272114-0x273A10) and the interrupt/dispatch path to the periodic
   tasks. Determine task periods (counters/divisors), the order of calls in
   each task, and the stack/register conventions at task level. Choose the
   hook site for our patch: a `bl` inside the 100 ms (or 20 ms if 100 ms does
   not exist) task calling a leaf function, where r3-r12 are dead after the
   call. Record: address of the `bl`, original target, task period, evidence
   (decompiled lines). Write `re/findings/scheduler.md`; add symbols
   (`task_10ms`, `task_100ms`, `timer_isr`, `hook_site_100ms`) to
   `re/symbols.csv` with tag static.
3. Also note which task computes injection and ignition outputs (helps
   briefs B6/B7) if it falls out of the analysis.
4. Commit on `agent/B1`; comment on #8 and #11.

## Acceptance
- No r2 violations remain, or each is explained.
- The hook site is named with its period and register facts; a later counter
  patch (#27) can be written from `scheduler.md` alone.
