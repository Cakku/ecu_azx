# Brief C4 — The period of the on-chip tasks 0x45CAC4 / 0x4328E4 and the raster activation chain

Issue **#44** (the task-period row) and the open item at the end of
`re/findings/scheduler.md`. Wave C, second pair (parallel with C3). No
`sudo`; Ghidra allowed on a copy of the project (recipe:
`re/findings/injection.md` §0).

Read `00_common_rules.md`, `re/findings/scheduler.md` (all),
`re/findings/rail.md` §7 and §13, `re/findings/injection.md` §11,
`emu/README.md`, `re/findings/mpc5xx_registers.md`, and the Time Base, SCCR
and PLPRCR chapters of `documents/MPC561RM.pdf` (present locally;
`pdftotext` from poppler, see `re/findings/fr_index.md` for the method).

## Why it matters
B1 assigned 1000 ms to TCB 11 (0x45CAC4) by priority analogy with
`task_1000ms`; B9 then found the whole rail-pressure PI controller, the
`%AWEA` angle maps and B6's `rkti_pre` inside it, which a 1 Hz raster cannot
run. Every latency estimate in the flex-fuel design that involves that task
(B6 options C/D, rail.md §7) is unknown until this is settled.

## Facts
- Tickless ERCOSEK V4.1.16: `os_alarm_dispatch` (0x099D30 external, 0x476630
  on-chip) programs TBREF1 (0x6FC208) from a sorted alarm list in the kernel
  object K (pointer at r13-0x1A4C = RAM 0x7FE5A4): +0x60 callback pointers,
  +0x6C deadline-monitor timers, +0x70 next indices, +0x74 cycles (-1 =
  free), +0x78 absolute deadlines. `os_SetRelAlarm` 0x476EE8,
  `os_set_deadline_timer` 0x477204, `os_check_deadline_timer` 0x47736C,
  `os_deadline_supervisor` 0x40BDC0.
- All timing literals are multiples of 701.754 TB ticks = 1 ms (unit is
  HYPOTHESIS): alarm 1 = 701 then cycle 35087 (50 ms) at 0x11B15C; alarm 2 =
  7017 one-shot at 0xA7A34; alarm 0 = 7719/7719 (11 ms) at 0x12CF5C and
  280701 one-shot at 0x12B19C / 0x135810.
- TCB table 0x478634, 25 x 0x24: +0x00 entry, +0x08 self pointer, +0x0C
  priority, +0x14 activation flag byte (0x7FE5FC-0x7FE644), +0x18 id.
  TCB 10 = 0x4328E4 (prio 9, flag 0x7FE618), TCB 11 = 0x45CAC4 (prio 8, flag
  0x7FE620), TCB 23 = 0x1205A0 (prio 9, flag 0x7FE630), TCB 24 = 0x120FAC
  (prio 8, flag 0x7FE636), TCB 20/21 = 0x11EBF4/0x11EC34 (10/20 ms, flags
  0x7FE632/0x7FE638), TCB 7/8 = 0x4240C8/0x424900 (10/20 ms on-chip).
- No r13-relative store writes a flag byte: activation goes through the TCB
  pointer. 10 and 20 ms are VERIFIED-STATIC from the self re-arm of deadline
  timers 2/3; 100 and 1000 ms are HYPOTHESIS (<= 150 / <= 1500 ms VERIFIED).
- `sys_clock_hz` = 56 000 000 at 0x144878 (can.md §3). The boot writes
  SCCR = 0x03217100 and PLPRCR = 0x00015080.

## Tasks (time-box each approach to about 3 h; report whichever settles it)
1. **Static — the activation chain.** Find the ERCOSEK ActivateTask
   primitive (sets a TCB's flag byte through +0x14 and links the TCB into the
   ready structure) and all its callers. Find the OS start-up that fills
   K+0x60 / K+0x74 (the tail of `task_background` 0x11B188) and decode every
   alarm callback: which TCBs it activates and with what sub-dividers.
   ERCOSEK uses *time tables* (cyclic lists of expiry points); look for
   tables of {offset, task} pairs in flash (near 0x0B0B30, 0x4764FC, the K
   image). Deliver, per raster TCB: activating alarm, cycle, divider, period,
   with the disassembly lines as evidence.
2. **Emulated — run the OS clock.** With `emu.Med9Emu`: (a) run the OS
   start-up that initialises K, stubbing TBSCR/TBREF/PIT reads; (b) provide
   `mftb`/`mftbu` if Unicorn lacks them (code hook on the instruction, a
   virtual Time Base you advance by 701.754 ticks per simulated ms);
   (c) invoke `os_alarm_dispatch` whenever the virtual TB reaches TBREF1 and
   hook writes to 0x7FE5FC-0x7FE644 with the timestamp; run 5 simulated
   seconds. The write cadence per flag byte is the period. Script
   `emu/os_clock.py` printing a table; a test that the 10 and 20 ms flags
   come out at 10 / 20 ms (VERIFIED facts validate the method).
3. **Clock decode.** From MPC561RM: what SCCR = 0x03217100 and PLPRCR =
   0x00015080 mean (MF, DIVF, TBS, EBDF, ...), the resulting system and Time
   Base frequencies, and whether TB = 701.754 kHz is consistent with 56 MHz
   (if not, say which of the two facts is wrong). Also settle the
   `0x6FC048`/`0x6FC04C` SIMASK2/SIMASK3 question in scheduler.md §10 if the
   manual answers it in passing.
4. Write a dated section in `re/findings/scheduler.md` (settled periods with
   tags, the activation chain, the method), fix §5.4 and the open-items
   table, and propagate: `re/findings/rail.md` §7/§13,
   `re/findings/injection.md` §11, `docs/05_flexfuel_design.md` where a
   latency depends on it, `re/symbols.csv` (ActivateTask, alarm callbacks,
   time tables). Commit on `agent/C4`; comment on #44 (the task-period row).

## Acceptance
The periods of 0x45CAC4 and 0x4328E4 carry a tag better than HYPOTHESIS
(VERIFIED-STATIC from the chain or VERIFIED-DYNAMIC from the emulated
clock), or the report states exactly why neither approach can settle it and
what one bench log must record (C3's `wave_b_confirm.json` already logs the
raster counters for that purpose).
