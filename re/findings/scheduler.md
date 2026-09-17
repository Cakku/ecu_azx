# Scheduler: ERCOSEK, the Time Base tick, the periodic tasks and the hook site

Agent B1, brief `docs/agent_briefs/B1_r2_context_and_scheduler.md`, issue #11.
Date: 2026-09-15. Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.

Everything below is static analysis of the dump plus
`re/findings/mpc5xx_registers.md` for the register names. Reproduce with
`tools/callgraph.py` and `tools/blobdis.py`; the exact commands are inline.

Tags: **VERIFIED-STATIC** = in the bytes of the dump; **COMMUNITY** = register
or field name from the MPC5xx manual as recorded by agent A2;
**HYPOTHESIS** = our inference.

---

## 1. The operating system is ETAS ERCOSEK V4.1.16

```bash
strings -a -t x data/passat_azx_ori.bin | grep -i ercosek
#  345d0 ERCOSEK   V4.1.16 MPC56x (c)ETAS Nov 29 2001
#  9c3f0 ERCOSEK   V4.1.16 MPC56x (c)ETAS Nov 29 2001
```

**VERIFIED-STATIC.** Two copies, one next to each instance of the kernel.
There are **two separately linked instances of the same kernel code**:

| Instance | Code | Kernel RAM variables |
|---|---|---|
| external flash | 0x098200-0x09C400 | r13-0x1A34, r13-0x1A24, r13-0x1A4C, … |
| on-chip flash | 0x475000-0x478600 | the same displacements **minus 0x18** |

The two are byte-identical except for those r13 displacements (verified by
comparing 0x098200+n with 0x274B00+n in the file: only the `stw/lwz r13`
displacements differ, consistently by 0x18). The **on-chip instance is the
live one** — its ISR wrappers are what reference the task tables of section 3.

## 2. Interrupt enable/disable: the RCPU EIE/EID special registers

Everywhere in the kernel:

```
000995DC  mfmsr  r3                  ; save MSR
000995E0  mtspr  81, r0              ; EID  -> MSR[EE] = 0, MSR[RI] = 1
000995E4  mtspr  275, r3             ; SPRG3 = saved MSR
  ...     critical section ...
000995F0  mfspr  r3, 275
000995F4  mtmsr  r3                  ; restore, EE back on
```

SPR **80 = EIE**, **81 = EID**, 275 = SPRG3 (**COMMUNITY**, MPC5xx RCPU
special registers; the *use* is **VERIFIED-STATIC**). This settles an open
item of `emu/`: the SPR numbers 80/81 the firmware writes are the external
interrupt enable/disable pair, and **the application does run with MSR[EE] =
1** — the boot-time MSR values 0x3942/0x2942 with EE = 0 describe the boot
only.

`re/findings/boot.md` section 2.4 notes the consequence: in both exception
tables present in the dump the external-interrupt slot is fatal, so the live
table (with the real handler) must be the one at 0x400000, inside the missing
16 KB.

## 3. Timer source

Two USIU timers are used, for two different jobs.

### 3.1 Time Base + reference B — this is the scheduler's clock

`os_alarm_dispatch` (external 0x099D30, on-chip 0x476630):

```
00099D3C  lis   r29,0x70 ; addi r29,r29,-0x3E00   ; r29 = 0x6FC200 = TBSCR
00099D48  lhz   r11,0(r29) ; andi. r11,r11,0xFF4B ; sth r11,0(r29)
                                                  ;   clear REFB, disable REFBE
00099D60  lwz   r27,-0x1A4C(r13)                  ; K = kernel object
00099D64  lwz   r10,0x2C(r27)                     ; head index of the alarm list
00099D68  lwz   r11,0x70(r27) ; lwzx r30,r11,r10*4 ; next index
00099D74  lwz   r12,0x78(r27) ; lwzx r28,r12,r30*4 ; absolute deadline
00099D80  mftb  r3                                ; TBL (SPR 268)
00099D84  subf. r27,r3,r28                        ; deadline - now
00099D88  ble   0x99DE0                           ; already due -> fire
00099DA0  rlwinm r11,r11,0,28,23 ; ori r11,r11,0x44 ; sth r11,0(r29)
                                                  ;   clear REFB, enable REFBE
00099DAC  mftb  r3 ; add r3,r3,r27 ; add r3,r3,r30
00099DB8  stw   r3,8(r29)                         ; TBREF1 (0x6FC208) = deadline
```

and when an alarm is due:

```
00099DE0  lwz   r5,-0x1A4C(r13)
00099DE8  lwz   r4,0x74(r5)  ; lwzx r31,r4,r30*4  ; cycle (period), 0 = one-shot
00099DEC  lwz   r10,0x60(r5) ; lwzx r27,r10,r30*4 ; callback
00099E00  add   r28,r28,r31                       ; deadline += cycle
00099E10  ... divwu/mullw catch-up if the deadline was missed ...
00099E38  stwx  r28,r4,r31                        ; store the new deadline
00099E4C  ... reinsert into the sorted list through K+0x70 ...
00099ECC  mtlr  r27 ; blrl                        ; call the alarm callback
```

**So the tick is the USIU Time Base with reference B**: `TBREF1` (0x6FC208)
is loaded with the next deadline and `TBSCR[REFBE]` (0x6FC200) is enabled;
`mftb` reads the current time. It is a tickless / next-deadline design, not a
fixed-rate tick. **VERIFIED-STATIC.**

Kernel object layout used above (**VERIFIED-STATIC** from the accesses;
field names ours):

| Offset | Content |
|---|---|
| +0x2C | head index of the deadline-sorted alarm list |
| +0x60 | array of alarm callback pointers |
| +0x6C | array of deadline-monitor timers, 8 bytes each: value, ~value |
| +0x70 | array of "next" indices (sorted singly linked list) |
| +0x74 | array of cycles (periods) in Time Base ticks, **-1 = alarm free** |
| +0x78 | array of absolute deadlines in Time Base ticks |

### 3.2 PIT — used as a software-triggerable interrupt, not as the tick

```
000997F8  lwz   r10,0x58(r4) ; li r11,0x100
00099800  lbzx  r10,r10,r31                       ; per-task interrupt level
00099804  lis   r12,0x70
0009980C  slw   r11,r11,r10 ; ori r11,r11,5
00099814  sth   r11,-0x3DC0(r12)                  ; PISCR (0x6FC240)
```

`0x100 << level` is the one-hot **PIRQ** field and `| 5` sets **PIE** and
**PTE** (field names **COMMUNITY**). The counterpart at 0x9A358-0x9A360 masks
PISCR with 0x83, i.e. clears PIRQ and PIE. So the PIT is programmed to raise
an interrupt at a *chosen SIU priority level* and switched off again — the
classic "software interrupt used to enter a task at its own priority" trick.
**VERIFIED-STATIC** for the writes, **HYPOTHESIS** for the purpose.

`PITC` (0x6FC244) is never written anywhere in the dump, which is consistent:
the PIT is used for its interrupt, not for its period.

### 3.3 Interrupt masking per priority

Every critical section and every task switch saves and restores the pair
**0x6FC048 / 0x6FC04C** (`SIMASK2`/`SIMASK3` by position in
`re/findings/mpc5xx_registers.md` section 9 — the *names* are
**HYPOTHESIS**, the use is **VERIFIED-STATIC**). The values come from
`tbl_os_prio_intmask` at **0x478B4C**, 8 bytes per priority, running
`0x82000001 / 0x33D00000` at the top down to `0x82000000 / 0x00000000`. The
seven ISR wrappers at 0x4171B0, 0x41733C, 0x4174CC, 0x41765C, 0x4177EC,
0x41797C and 0x417B0C all build that address into r3.

## 4. The task table

`tbl_os_task_control_blocks` at **0x478634** (file 0x274634), 25 entries,
anchored by the constant 0x004764FC at +0x04 of every entry
(**VERIFIED-STATIC**):

> **2026-09-16 (C2, issue #23) — the stride is not uniform. VERIFIED-STATIC.**
> "25 entries of 0x24 bytes" is right about the count and about most rows, but
> the seven ISR tasks (ids 1-7, rows at 0x4788A8, 0x4788C8, 0x4788E8,
> 0x478908, 0x478928, 0x478948, 0x478968) are **0x20** apart, and there are
> gaps before 0x4787DC and before 0x4789E8. Walk the table by the anchor word,
> not by a fixed stride — `tools/ram_survey.py --stack` does, and the 25 rows
> it recovers reproduce the table below exactly. The activation-flag bytes
> (+0x14) span 0x7FE5FC-0x7FE644 as documented.

> **2026-09-16 (C4, issue #44) — the anchor scan still misses twelve rows.
> VERIFIED-STATIC, section 11.2.** The table has **37** rows, not 25. The
> anchor word 0x004764FC is not a field of the row at all: it is the
> terminator of the task's *process list*, which for these 25 tasks happens to
> sit inline in the table. The other twelve tasks (ids 0, 8, 17, 18, 20, 22,
> 25, 29, 30, 31, 34, 37) point at multi-process lists in external flash at
> 0x0B1ED4-0x0B2A40 and carry no anchor, which is what the "gaps" above are.
> Walk the 37 ActivateTask thunks at 0x0B091C-0x0B0AD4 instead:
> `tools/ercosek_tasks.py --tasks`.

| Offset | Field |
|---|---|
| +0x00 | task entry point |
| +0x04 | 0x004764FC (common) |
| +0x08 | pointer to this entry |
| +0x0C | priority |
| +0x10 | 1 |
| +0x14 | per-task activation flag byte, in RAM 0x7FE5FC-0x7FE644 |
| +0x18 | task id |

| # | Entry | Prio | id | Flag | Note |
|---|---|---|---|---|---|
| 0 | 0x11B188 | 0x00 | 15 | 0x7FE610 | `task_background` — 79 `bl` then the OS init |
| 1 | 0x12AB78 | 0x0A | 28 | 0x7FE628 | |
| 2 | 0x12BC40 | 0x0A | 42 | 0x7FE644 | |
| 3 | 0x11CA44 | 0x0A | 16 | 0x7FE612 | |
| 4 | 0x11CAD4 | 0x0A | 9 | 0x7FE60E | |
| 5 | 0x4223B0 | 0x0A | 40 | 0x7FE640 | |
| 6 | 0x4224BC | 0x0A | 41 | 0x7FE642 | |
| **7** | **0x4240C8** | **0x0B** | 21 | 0x7FE61C | **10 ms**, on-chip |
| **8** | **0x424900** | **0x0A** | 24 | 0x7FE622 | **20 ms**, on-chip |
| 9 | 0x424AF8 | 0x0A | 26 | 0x7FE626 | |
| **10** | **0x4328E4** | **0x09** | 19 | 0x7FE618 | **100 ms**, on-chip, 183 `bl` |
| **11** | **0x45CAC4** | **0x08** | 23 | 0x7FE620 | **1000 ms**, on-chip, 161 `bl` |
| 12-18 | 0x41707C…0x4170DC | 0x0A/0x0B | 1-7 | 0x7FE5FC… | the seven ISR tasks; also listed at 0x0B0B30 |
| 19 | 0x11EB5C | 0x0A | 39 | 0x7FE63E | |
| **20** | **0x11EBF4** | **0x0B** | 33 | 0x7FE632 | **10 ms** |
| **21** | **0x11EC34** | **0x0A** | 36 | 0x7FE638 | **20 ms** |
| 22 | 0x11EC58 | 0x0A | 38 | 0x7FE63C | |
| **23** | **0x1205A0** | **0x09** | 32 | 0x7FE630 | **100 ms**, 66 `bl` |
| **24** | **0x120FAC** | **0x08** | 35 | 0x7FE636 | **1000 ms** |

## 5. Task periods — how they are pinned down

### 5.1 The two OS APIs

* **`os_set_deadline_timer(id, delta)` at 0x477204** (**VERIFIED-STATIC**):
  `K+0x6C[id*8] = mftb + delta` and `K+0x6C[id*8+4] = ~that`. A
  redundantly-stored absolute deadline.
* **`os_check_deadline_timer(id, 0)` at 0x47736C**: returns non-zero when a
  deadline has passed.
* **`os_SetRelAlarm(id, increment, cycle)` at 0x476EE8** — OSEK
  `SetRelAlarm`; it reads `K+0x74[id]` and treats -1 as "alarm free",
  otherwise returns error 7.

### 5.2 The tick unit

Every literal passed to these functions anywhere in the image is an exact
multiple of **701.754 Time Base ticks** (**VERIFIED-STATIC**, the literals are
in the dump; the interpretation of the unit is **HYPOTHESIS**):

| Literal | / 701.754 | Interpreted | Call site |
|---|---|---|---|
| 701 | 1 | 1 ms | `os_SetRelAlarm(1, 701, 35087)` at 0x11B15C |
| 7017 | 10 | 10 ms | `os_set_deadline_timer(2, …)` at 0x11B110, 0x11EBF0, 0x42408C; `os_SetRelAlarm(2, 7017, 0)` at 0xA7A34 |
| 7719 | 11 | 11 ms | `os_SetRelAlarm(0, 7719, 7719)` at 0x12CF5C |
| 14035 | 20 | 20 ms | `os_set_deadline_timer(3, …)` at 0x11B11C, 0x11ED74, 0x4248AC |
| 35087 | 50 | 50 ms | cycle of alarm 1 |
| 105263 | 150 | 150 ms | `os_set_deadline_timer(0, …)` at 0x11B140, 0x40BE2C |
| 280701 | 400 | 400 ms | `os_SetRelAlarm(0, 280701, 0)` at 0x12B19C, 0x135810 |
| 1052631 | 1500 | 1500 ms | `os_set_deadline_timer(1, …)` at 0x11B0E8, 0x11DA8C |

A set of {1, 10, 11, 20, 50, 150, 400, 1500} is unmistakably milliseconds, so
**1 ms = 701.754 Time Base ticks, i.e. TB = 701.754 kHz**. The MPC5xx PLL
settings the boot writes (`SCCR = 0x03217100`, `PLPRCR = 0x00015080`) have not
been decoded, so the system clock this implies is an open item — but the
*ratios* and therefore the periods do not depend on it.

> **WRONG — corrected 2026-09-16 (C4, issue #44); see sections 11.1 and 12.1.**
> The unit is **1 ms = 3508 ticks (285 ns each)**, five times finer, so this
> whole column reads {0.2, 2, 2.2, 4, 10, 30, 80, 300} ms. The kernel
> configuration record carries the generator's own `285 ns/tick` and
> `3508 ticks/ms` at 0x09B75C / 0x478EC0, and `SCCR[TBS] = 1` makes the Time
> Base the system clock / 16 = **3.5 MHz**, not 701.754 kHz. Every literal in
> the table above is exactly `floor(t_ns / 285)`. The ratios are unaffected,
> which is why section 5.4's *relative* ordering survived and its absolute
> numbers did not.

### 5.3 Each raster task re-arms its own deadline

This is what fixes the periods (**VERIFIED-STATIC**):

* **`task_10ms` (0x11EBF4, TCB 20, prio 0x0B)** ends `b 0x11EBDC`
  (`task_10ms_epilogue`), which increments the counter at r13-0x2890 and
  tail-calls `os_set_deadline_timer(2, 7017 = 10 ms)`. The on-chip sibling
  `task_10ms_int` (0x4240C8, TCB 7, same priority) does the same at 0x42408C.
  ⇒ **period 10 ms.**
* **`task_20ms` (0x11EC34, TCB 21, prio 0x0A)** ends `b 0x11ED50`
  (`task_20ms_epilogue`), which increments r13-0x2878 and calls
  `os_set_deadline_timer(3, 14035 = 20 ms)`. `task_20ms_int` (0x424900,
  TCB 8) does the same at 0x4248AC. ⇒ **period 20 ms.**
* **`task_100ms` (0x1205A0, TCB 23, prio 0x09)**: its last call, at 0x1206B0,
  is `task_100ms_epilogue` (0x120570), which increments r13-0x2898 and
  branches to `os_deadline_supervisor` (0x40BDC0). That checks deadline timers
  0-3 and re-arms **timer 0 with 105263 = 150 ms**.
* **`task_1000ms` (0x120FAC, TCB 24, prio 0x08)**: timer 1's window is
  1052631 = 1500 ms, armed at 0x11B0E8 and 0x11DA8C.

The 100 ms and 1000 ms numbers are **HYPOTHESIS**; what is verified is the
*window*: period(prio 0x09) <= 150 ms and period(prio 0x08) <= 1500 ms.
The argument for 100 ms rather than 150 ms is the check cadence:

```
0011ED78  lwz  r9,-0x3D24(r13) ; addic. r3,r9,-1 ; stw r3,-0x3D24(r13)
0011ED84  bgt  0x11EDB8                        ; not yet the 5th time
0011ED88  li   r12,5 ; stw r12,-0x3D24(r13)    ; reload the /5 divider
0011ED90  li   r3,0 ; addi r4,r3,0 ; bl 0x47736C   ; check deadline timer 0
0011ED9C  cmpwi r3,0 ; beq 0x11EDB8
0011EDA4  ... li r3,0x74 ; bl 0xBA444          ; expired -> halt, code 0x74
```

`task_20ms_epilogue` checks timer 0 **every fifth 20 ms activation, i.e. every
100 ms**. A raster with a 150 ms period and a 150 ms window could not survive a
100 ms check cadence; a 100 ms period with a 1.5x window is the standard
arrangement. The same reasoning gives 1000 ms for the 1500 ms window.

### 5.4 Summary

> **SUPERSEDED — 2026-09-16 (C4, issue #44). Read section 11 instead.** Every
> period in this table is too long: the tick unit was 5x too coarse (section
> 11.1) and the deadline windows are 2x their rasters rather than equal to
> them (section 11.5), so the verified rows are **10x** off (10 ms -> 1 ms,
> 20 ms -> 2 ms) and the two hypothesis rows further still (100 ms -> 10 ms,
> 1000 ms -> 20 ms). [Wording clarified at integration, 2026-09-16.] The settled values, VERIFIED-STATIC from the
> activation chain and VERIFIED-DYNAMIC from `emu/os_clock.py`:
> 0x11EBF4 / 0x4240C8 = **1 ms**, 0x11EC34 / 0x424900 = **2 ms**,
> 0x11EC58 / 0x424AF8 = **5 ms**, 0x1205A0 / 0x4328E4 = **10 ms**,
> 0x120FAC / 0x45CAC4 = **20 ms**. The 50 / 100 / 200 / 1000 ms rasters are
> tasks this table never listed (section 11.2: the table has 37 rows, not 25).

| Priority | Period | External-flash task | On-chip task | Deadline timer |
|---|---|---|---|---|
| 0x0B | **10 ms** (VERIFIED) | 0x11EBF4 | 0x4240C8 | 2, window 10 ms |
| 0x0A | **20 ms** (VERIFIED) | 0x11EC34 | 0x424900 | 3, window 20 ms |
| 0x09 | **100 ms** (HYPOTHESIS; <= 150 ms VERIFIED) | 0x1205A0 | 0x4328E4 | 0, window 150 ms |
| 0x08 | **1000 ms** (HYPOTHESIS; <= 1500 ms VERIFIED) | 0x120FAC | 0x45CAC4 | 1, window 1500 ms |
| 0x00 | background | 0x11B188 | — | — |

Note the priorities are rate-monotonic, which is a consistency check on the
table: 0x0B (fastest) to 0x08 (slowest), with 0x00 for the background task.

## 6. Call order inside the tasks

Every raster task is a flat sequence of **argument-less `bl`** instructions
after a three-instruction prologue, ending in a tail `b` to its epilogue. This
matters for the hook: no value is passed in or out, so the volatile registers
r3-r12 are dead at every boundary between two consecutive `bl`s.

```
0011EBF4  task_10ms:
          stwu r1,-8(r1) ; mflr r0 ; stw r0,0xC(r1)
          li r3,0 ; bl 0xB87C8      <- the only call in any raster with an argument
          bl 0x11EC5C   bl 0x4239D0   bl 0x423540
          bl 0x11EBA0   bl 0x11EBA4   bl 0x11EBD8   bl 0x424090
          lwz r0,0xC(r1) ; mtlr r0 ; addi r1,r1,8
          b  0x11EBDC   (task_10ms_epilogue)

0011EC34  task_20ms:
          stwu r1,-8(r1) ; mflr r0 ; stw r0,0xC(r1)
          bl 0x11ED00   bl 0x630C0
          lwz r0,0xC(r1) ; mtlr r0 ; addi r1,r1,8
          b  0x11ED50   (task_20ms_epilogue)

001205A0  task_100ms:
          stwu r1,-8(r1) ; mflr r0 ; stw r0,0xC(r1)
          66 x bl (0x1205AC .. 0x1206B0), the last being task_100ms_epilogue
          lwz r0,0xC(r1) ; mtlr r0 ; addi r1,r1,8
          b  0x478370
```

Full 100 ms sequence (address of the `bl` -> target):

```
1205AC 478310  1205B0 46C564  1205B4 46C2C4  1205B8 470594  1205BC 11F418
1205C0 1206C4  1205C4 11F600  1205C8 11F4F8  1205CC 11F468  1205D0 120358
1205D4 0BD894  1205D8 120214  1205DC 1201C0  1205E0 11FD94  1205E4 0BD9E8
1205E8 05F7D8  1205EC 0201DC  1205F0 0BDECC  1205F4 11FD58  1205F8 12046C
1205FC 11FC6C  120600 120470  120604 135F9C  120608 137588  12060C 11F708
120610 033200  120614 032EB0  120618 0333A4  12061C 11F054  120620 034AE0
120624 11F41C  120628 11F2B8  12062C 11F358  120630 43B9D4  120634 11F064
120638 437C4C  12063C 0BD3E0  120640 43C768  120644 11FB30  120648 120558
12064C 4435F4  120650 11FC54  120654 0236FC  120658 11F40C  12065C 120824
120660 11F060  120664 11F03C  120668 1208E8  12066C 11F464  120670 11F460
120674 0BE04C  120678 120A74  12067C 11F02C  120680 11F9AC  120684 11F70C
120688 46C48C  12068C 46C740  120690 061970  120694 061944  120698 0B8BA0
12069C 46EC7C  1206A0 137C2C  1206A4 0BE050  1206A8 443FC0  1206AC 40C064
1206B0 120570
```

## 7. Stack and register conventions at task level

**VERIFIED-STATIC** from the prologues and from `os_alarm_dispatch`:

* Tasks are ordinary PowerPC EABI functions. The dispatcher calls them with
  `mtlr` + `blrl`, so LR holds the return address and the task saves it in its
  own frame (`stwu r1,-8(r1); mflr r0; stw r0,0xC(r1)`).
* r1 is the task's own stack; the kernel switches r1 per task (the stack
  pointer chain lives at r13-0x1A50 in the external instance, r13-0x1A68 in
  the on-chip one).
  > **2026-09-16 (C2, issue #23).** There is only **one** stack, at
  > **0x7FF3C0-0x7FF76F**: the kernel stack descriptor at flash 0x09B6F8 is
  > `{0x7FFFEC, 0x7FF770, 0x7FF730, 0x7FF3C0, 0x36C}`, `app_entry_crt0` sets
  > r1 = 0x7FF768 = 0x7FF770-8, and `FUN_0012C25C` fills exactly
  > 0x7FF3C0-0x7FF76B at cold start. ISRs do not switch stacks — the
  > exception prologue starts `stwu r1,-0x48(r1)` on whatever r1 the
  > interrupted code had — so the 0x48-byte frames come out of the same
  > 0x3B0 bytes. The deepest static `stwu` chain from a task entry is 0x588 B
  > (task 0x4328E4), which already exceeds that, so either those paths are
  > mutually exclusive or the stack runs below 0x7FF3C0 into the unreferenced
  > RAM down to 0x7FF01B. `ram_survey.py --stack`, `re/findings/ram.md`
  > sections 4.1 and 5.
* r2 = 0x5C9FF0 and r13 = 0x7FFFF0 throughout the application, including
  inside tasks and ISRs (`app_sda_setup_int` 0x405588 reloads them at the
  start of the on-chip exception prologue). A patch must never write r2 or
  r13 — `tools/blobdis.py --check-sda` enforces this.
* r14-r31 are non-volatile and are saved by `stmw`/`lmw` where used.
* r3-r12, CR, CTR, XER are volatile. Between two consecutive `bl` in a raster
  task **none of them is live**, because the calls take no arguments and
  return nothing that is used.
* Exception prologues (0x4055A0 and the 0x98200-0x9947C copy) save
  r0, CTR, XER, CR, LR and r3-r12 into a 0x48-byte frame and end with `rfi`.

## 8. Hook site for issue #27

**Address: 0x12067C. Original word: `4B FF E9 B1` = `bl 0x11F02C`.
Task: `task_100ms` (0x1205A0). Period: 100 ms (see section 5.3).**

> **Corrected 2026-09-16 (C4, issue #44).** The hook site and every property
> claimed for it below are unchanged and still VERIFIED-STATIC, but the
> **period is 10 ms, not 100 ms** (section 11.4), and 0x1205A0 belongs to
> **task set B**, which only runs after the switch at 0x11DA64 sets the mode
> byte 0x7FAB55 to 2 (section 11.7). A stub hooked here therefore runs ten
> times more often than B1 assumed — or, if set A is the live one, never.
> Two consequences for issue #27 and for `patches/ff_counter`: the expected
> counter slope is 100 /s, not 10 /s, and the bench procedure must first
> establish which task set is live. The same-priority alternative in set A is
> `0x4328E4`, whose flat `bl` list offers equivalent sites.

```
00120674  4B F9 D9 D9  bl 0x0BE04C
00120678  48 00 03 FD  bl 0x120A74
0012067C  4B FF E9 B1  bl 0x11F02C     <-- hook here
00120680  4B FF F3 2D  bl 0x11F9AC
00120684  4B FF F0 89  bl 0x11F70C
```

Why this one:

* **The target is a leaf.** `0x11F02C` is four instructions and calls nothing:

  ```
  0011F02C  li   r12,0
  0011F030  stb  r12,-0x1767(r13)      ; RAM 0x7FE889
  0011F034  sth  r12,0xE28(r13)        ; RAM 0x800E18
  0011F038  blr
  ```

  (named `clr_ram_7FE889_800E18` in `re/symbols.csv`).
* **r3-r12 are dead across it.** The instructions on both sides are themselves
  argument-less `bl`s; nothing sets up a register before 0x12067C and nothing
  consumes one after it. A stub may use the whole volatile set freely.
* **It is a single word.** The patch is `bl <stub>` in place of `bl 0x11F02C`;
  the stub does its work, then `b 0x11F02C` (or `bl` + `blr`) so the original
  call still happens. No checksum-relevant layout change beyond the one word —
  and 0x12067C is inside the 64 KB block 0x120000-0x12FFFF covered by the
  descriptor table at file 0xA0000, so `tools/checksum.py fix` must be run
  after the patch.
* It runs **once per 100 ms activation**, with no conditional above it inside
  the task body, so the stub is called exactly at the raster rate.

Second choice, if a faster raster is wanted: **0x12061C**, `bl 0x11F054`
(a three-instruction leaf, same "bl on both sides" property) is also inside
`task_100ms`; for a verified-period task use `task_20ms` (0x11EC34) and the
`bl 0x630C0` at 0x11EC44, whose 20 ms period is VERIFIED-STATIC.

### 8.1 Added 2026-09-16 (brief D1, issue #32) — the task-set-A twin site, 0x432940

Section 11.7 leaves open which task set runs with the engine turning, and a
hook in the dead set never executes. `patches/ff_fuel` therefore hooks **both**
10 ms rasters; this is the set-A half. **VERIFIED-STATIC.**

```
0043293C  4B C8 B1 29  bl 0xbda64
00432940  4B C8 B0 A5  bl 0xbd9e4     <-- hook here (task set A, 10 ms)
00432944  4B FF F8 99  bl 0x4321dc
```

```
000BD9E0  4E 80 00 20  blr            ; end of the previous function
000BD9E4  4E 80 00 20  blr            ; nop_leaf_bd9e4 - the whole function
000BD9E8  94 21 FF F8  stwu r1,-8(r1) ; the next function
```

* **The target is an empty function.** `0xBD9E4` is a single `blr`, and
  `tools/sda_xref.py data/passat_azx_ori.bin --code 0xBD9E4` finds **exactly one
  call site in the image — 0x432940 itself**. So the stock work a tail branch
  has to preserve is literally nothing, which makes this site *cleaner* than
  0x12067C (whose leaf clears two RAM cells).
* **r3-r12 are dead across it.** 0x4328E4 is the same flat list of
  argument-less `bl`s as every raster task (section 7); no instruction between
  the calls sets a register, and the entry blocks of both neighbours' targets
  (0xBDA64, 0x4321DC) write r3-r12 before they read them, so no return value is
  consumed either. `HOOK_TAIL` is enough.
* **It is unconditional and at the top level** of the task body (0x4328F0
  onwards is an uninterrupted `bl` list), so it runs exactly once per 10 ms
  activation of task set A.
* **It is in the on-chip flash** (file 0x22E940), inside the code descriptor
  table at file 0x0A0000, so `tools/checksum.py fix` covers it — but
  `tools/patch_apply.py` guards 0x404000-0x47FFFF and the change needs the
  explicit `"onchip_edit": true` flag added by D1 (docs/06 §1).

Reproduce:

```bash
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
    --addr 0x4328E4 --file-off 0x22E8E4 --len 0x200
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
    --addr 0x0BD9D0 --file-off 0x0BD9D0 --len 0x20
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --code 0xBD9E4
```

A patch that hooks both sets must stay correct if both ever fired. `ff_fuel`
does that with a one-byte owner field in its state block: the first source to
call takes ownership, the other is counted and returns, and ownership moves
only after `FF_OWNER_SWITCH` consecutive calls from the other source with none
from the owner in between — which also covers the real case of set A running
for a few activations before 0x11DAF4 switches to set B.
`ff_src_seen` in that block is, as a side effect, the answer to section 11.7
readable from a single logger sample.

## 9. Which task computes injection and ignition (for briefs B6/B7)

Not settled here, and deliberately not guessed. What did fall out:

* The engine-synchronous work is **not** in the time rasters of section 4: the
  10 ms and 20 ms tasks are short (8 and 2 calls). The seven ISR tasks with
  ids 1-7 (TCB entries 12-18, functions 0x41707C-0x4170DC, listed again at
  0x0B0B30) are the hardware-interrupt tasks, and the TPU3 crank/cam channels
  are the obvious source — B6/B7 should start there and at
  `tbl_isr_task_control_blocks` (0x0B0B30).
* `task_100ms` reaches 318 functions and `task_100ms_int` 572, so the slow
  rasters carry most of the diagnosis and adaptation code.

## 10. Open items

| Question | Status |
|---|---|
| Decode `SCCR = 0x03217100` / `PLPRCR = 0x00015080` to get the real system clock, and confirm TB = 701.754 kHz | **CLOSED 2026-09-16 (C4) — section 12.1/12.2.** TBS = 1 -> TB = system clock / 16 = **3.5 MHz**; MF = DIVF = 0, so the part runs 1:1 on its 56 MHz clock input. TB is **not** 701.754 kHz |
| Confirm 100 ms / 1000 ms rather than 150 ms / 1500 ms | **CLOSED 2026-09-16 (C4) — section 11.** Neither: the two tasks are **10 ms** and **20 ms**, from the alarm-1 cycle and the /2 divider |
| Which ISR activates each raster task (nothing writes the TCB flag bytes with an r13-relative store; activation goes through the TCB pointer) | **CLOSED 2026-09-16 (C4) — sections 11.2-11.4.** `os_ActivateTask` (0x475E8C) is the only writer of the counter bytes; the fast rasters come from the time table on Time Base reference A, the slow ones from alarm 1 plus the divider chain 0x40BEF0 |
| What the seven ISR tasks (ids 1-7) are bound to | open — B6/B7. C4 adds: they are activated from the ISR wrappers at 0x40A9F0-0x40ACDC and 0x417C40-0x417D18, not from any timer |
| Are `0x6FC048`/`0x6FC04C` really SIMASK2/SIMASK3? | **CLOSED 2026-09-16 (C4) — section 12.3.** Yes; MPC561RM's USIU register map names 0x2FC048/0x2FC04C exactly that |
| Which of the two task sets (A: 0x4328E4/0x45CAC4, B: 0x1205A0/0x120FAC) is live with the engine running | **SETTLED 2026-09-17 (E1, issue #34) — section 11.8.** **Set A**, by necessity: the only producers of `prsoll` (0x45822C) and `zwstt` (0x431294) are reached from set A's tasks 23 and 19 and from nowhere else, so the engine cannot run on set B. The bench log of section 11.7 is now a confirmation, not the decision |

#### Added 2026-09-15 (integration, after brief B9) — the period of TCB 11 (0x45CAC4) is in doubt

> **RESOLVED 2026-09-16 (C4, issue #44) — section 11.4.** The period of
> 0x45CAC4 is **20 ms**: alarm 1 fires every 35087 ticks = 10 ms, activates
> the 10 ms task 0x4328E4, whose body runs the divider chain 0x40BEF0 whose
> /2 branch activates 0x45CAC4. Nothing about the priority-0x08 analogy was
> needed. A 20 ms rail-pressure controller is exactly what B9 expected.

B9 (`re/findings/rail.md` §7 and its open-items table) found that the on-chip
task entered at **0x45CAC4** (TCB 11, priority 0x08, 161 `bl`) contains the
whole rail-pressure PI controller (`hdrpsol_main` 0x45822C, controller
0x457BC8), the `%AWEA` angle maps and B6's `rkti_pre`. A rail-pressure
controller cannot run at 1 Hz. The 1000 ms figure above was derived for
`task_1000ms` (0x120FAC, TCB 24) from deadline timer 1's 1500 ms window and
then extended to 0x45CAC4 only because both share priority 0x08; that
extension is now the weakest link. Possibilities: the two priority-0x08 tasks
have different periods, or 0x45CAC4 is activated from a source the static scan
did not see (an alarm or an ISR chain). **Status: OPEN, HYPOTHESIS for 0x45CAC4
withdrawn to "period unknown, <= 1500 ms".** Resolution needs one dynamic run
(A5 harness or a bench log). Until then, treat every latency estimate that
depends on this task's period (B6 options C/D, B9 §7) as unknown.

---

## 11. Added 2026-09-16 (brief C4, issue #44) — the activation chain, and the periods are settled

Agent C4, `docs/agent_briefs/C4_task_periods.md`. Reproduce everything below
with

```bash
./.venv/bin/python3 tools/ercosek_tasks.py data/passat_azx_ori.bin
./.venv/bin/python3 -m emu.os_clock --set a --seconds 5
./.venv/bin/python3 -m emu.os_clock --set b --seconds 5
./.venv/bin/python3 -m unittest tests.test_ercosek_tasks
```

**Summary: the table of section 5.4 is wrong in the same direction for every
raster — 10x for the rows it had verified (1 ms and 2 ms, not 10 and 20).**
The tick unit, not the chain, was the error: 1 ms is **3508** Time Base ticks,
not 701.754 (a factor of 5), and the deadline windows read as periods are 2x
the periods (section 11.5), which together give the 10x. [Integration note
2026-09-16: an earlier wording here said "factor of 5" for the periods; the
per-address values in this section were always the 10x ones.] The chain itself is now read
out end to end, so every period is **VERIFIED-STATIC** and reproduced
**VERIFIED-DYNAMIC** by the emulator.

| Raster | Task set A (installed by `os_init`) | Task set B | Period |
|---|---|---|---|
| 1 ms | id 21 `0x4240C8` | id 33 `0x11EBF4` | 3508 ticks |
| 2 ms | id 24 `0x424900` | id 36 `0x11EC34` | 7016 |
| 5 ms | id 26 `0x424AF8` | id 38 `0x11EC58` | 17540 |
| **10 ms** | **id 19 `0x4328E4`** | id 32 `0x1205A0` | 35087 |
| **20 ms** | **id 23 `0x45CAC4`** | id 35 `0x120FAC` | 70174 |
| 50 ms | id 25 (list `0x0B1EE4`) | id 37 (list `0x0B28A0`) | 175435 |
| 100 ms | id 18 (list `0x0B1FC0`) | id 31 (list `0x0B28BC`) | 350870 |
| 200 ms | id 22 (list `0x0B231C`) | id 34 (list `0x0B29CC`) | 701740 |
| 1000 ms | id 17 (list `0x0B24DC`) | id 30 (list `0x0B29F8`) | 3508700 |

### 11.1 The tick unit: 1 ms = 3508 Time Base ticks (285 ns each)

Section 5.2 read the literal 7017 as 10 ms and concluded TB = 701.754 kHz.
Five independent facts say it is 2 ms and TB = 3.5 MHz, the first of them
decisive on its own:

0. **The firmware converts microseconds to Time Base ticks by dividing by
   285, in executable code.** `0x12BBE4`, feeding `os_SetRelAlarm(0, …)`:

   ```
   0012BBE4  lwz   r12,-0x4BA8(r13)   ; 0x7FB448, a timeout in microseconds
   0012BBE8  mulli r12,r12,0x3E8      ; x 1000            -> nanoseconds
   0012BBEC  li    r11,0x11D          ; 285
   0012BBF0  divwu r4,r12,r11         ; / 285 ns          -> Time Base ticks
   0012BBF4  li    r3,0 ; li r5,0
   0012BBFC  bl    0x476EE8           ; os_SetRelAlarm(0, ticks, one-shot)
   ```

   `0x011D = 285` appears as an immediate **exactly once in the whole image**,
   here. 0x7FB448 is loaded with 0x11940 = 72000 at 0x12BB10, i.e. 72 ms, and
   72000 x 1000 / 285 = 252631 ticks. So `ticks = t_ns / 285` is not an
   inference about a data table: it is what the CPU does.
   **VERIFIED-STATIC.**
1. **The ERCOSEK generator wrote the same constant into the kernel
   configuration record.** `tbl_os_kernel_config` (external `0x09B6BC`,
   on-chip `0x478E20` — the same record; the stack descriptor C2 found at
   `0x09B6F8` is at +0x3C of both) carries at **+0xA0..+0xAC** the quartet

   ```
   0x09B75C / 0x478EC0 :  0000DAC0  00000010  00000DB4  0000011D
                          = 56000    = 16      = 3508    = 285
   ```

   i.e. *system clock 56000 kHz*, *Time Base divider 16*, *3508 ticks per
   millisecond*, *285 ns per tick*. **VERIFIED-STATIC** (the words are in the
   dump, twice); the reading of the four fields is HYPOTHESIS, but everything
   else below agrees with it.
2. **Every timing literal in the image is `floor(t_ns / 285)`** — exactly, for
   all of them, with no exception:

   | Literal | /285 ns | Literal | /285 ns |
   |---|---|---|---|
   | 701 | **0.2 ms** | 35087 | **10 ms** |
   | 3508 | **1 ms** | 105263 | **30 ms** |
   | 7017 | **2 ms** | 280701 | **80 ms** |
   | 7719 | **2.2 ms** | 1052631 | **300 ms** |
   | 14035 | **4 ms** | | |

   Under section 5.2's unit the same set reads {1, 5, 10, 11, 20, 50, 150,
   400, 1500} ms; both look plausible in isolation, which is why B1's reading
   survived a whole wave. Facts 0 and 1 are what decide between them.
3. **The hardware agrees.** `SCCR = 0x03217100` has **TBS = 1** (bit 6), and
   MPC561RM section 8.11.1 Table 8-9 plus Table 8-2 make that "time base
   source is the system clock divided by **16**" — see section 12 below. With
   `sys_clock_hz = 56 000 000` (`can.md` section 3, itself pinned by the
   TouCAN PRESDIV 6 / 16 Tq timing giving exactly the 500 kbit/s of the VW
   powertrain bus) the Time Base runs at **3.5 MHz**, so 35087 ticks is
   10.025 ms — never 50 ms.
4. The 0.25 % gap between the generator's 285 ns and the real 285.714 ns is
   the usual truncation: every raster runs **0.25 % slow** against its nominal
   name (a "10 ms" task is 10.025 ms). Irrelevant for control, but it matters
   for a counter-slope check on the bench.

### 11.2 `os_ActivateTask` at 0x475E8C, and the 37 task descriptors

`0x475E8C` is the OSEK `ActivateTask` primitive (the checked API wrapper
`0x4770F0`, called only from 0x11DB28, ends in the same body). It takes the
task handle in r3, and its tail at `0x476058` does exactly

```
00476058  lbz  r12,0(r28)      ; r28 = [handle+0x0C] = the activation counter
0047605C  addi r12,r12,1
00476060  stb  r12,0(r28)
```

so the "activation flag byte" of section 4 is an activation **counter** and
`0x475E8C` is its only writer — which answers section 10's "nothing writes the
TCB flag bytes with an r13-relative store". **VERIFIED-STATIC.**

The generated per-task wrappers are a table of 37 three-instruction thunks at
**0x0B091C-0x0B0AD4**, stride 0xC:

```
000B09AC  3C600048  lis  r3,0x48
000B09B0  806387FC  lwz  r3,-0x7804(r3)   ; r3 = [0x4787FC] = 0x4787E4
000B09B4  483C54D8  b    0x475E8C          ; ActivateTask(task 23 = 0x45CAC4)
```

Walking those 37 constants recovers the **complete** descriptor table, which
is bigger and differently shaped than section 4 records. Corrections:

* The handle (the OSEK `TaskType`) is **not** the row start; it is the address
  of the *core* record:

  | Offset from handle | Field |
  |---|---|
  | +0x00 | pointer to the task's **process list** |
  | +0x04 | priority |
  | +0x08 | maximum activations (always 1) |
  | +0x0C | address of the activation counter byte (0x7FE5FC-0x7FE644) |
  | +0x10 | task id |
  | +0x14 | 0 |
  | +0x18 | pointer back to the handle (the word every thunk loads) |

  Section 4's field list is the same record read from 8 bytes earlier.
* The **process list** is an array of function pointers terminated by
  `0x004764FC`. Section 4's "+0x04 = 0x004764FC (common)" is that terminator:
  the on-chip tasks have a one-element list `{entry, 0x4764FC}` inline in the
  table, so their handle is row+8 and the anchor scan finds them. The other
  twelve tasks point at multi-process lists in external flash at
  **0x0B1ED4-0x0B2A77** (up to 214 processes each), have no `0x4764FC` word
  in the table, and are therefore **missed by the anchor scan** — that is the
  whole of the "gaps before 0x4787DC and before 0x4789E8" that C2 recorded.
  `0x004764FC` is `os_TerminateTask`.
* There are **37** tasks, not 25: ids 0-9, 15-26 and 28-42 (10-14 and 27 are
  unused), one activation-counter byte each, two bytes apart, filling
  0x7FE5FC-0x7FE645 exactly.

### 11.3 The fast rasters: Time Base **reference A** and a cyclic time table

Section 3.1 found reference B. There is a second, independent timer:
`os_time_table_dispatch` at **0x4768C0** runs off **TBREF0 (0x6FC204)**,
reference A.

```
004768DC  lwz  r3,-0x1A10(r13)     ; current entry
004768E0  lwz  r11,-0x1A08(r13)    ; accumulated deadline T
004768E4  lwz  r10,4(r3)           ; delta of this entry
004768E8  addi r9,r3,8             ; next entry
004768EC  add  r11,r11,r10 ; stw r11,-0x1A08(r13)
004768F4  lwz  r30,0(r3) ; mtlr r30
004768FC  stw  r9,-0x1A10(r13)
00476900  blrl                     ; call the action
00476904  ... TBSCR |= 0x88 ; stw r12,4(r31)         ; TBREF0 = T
0047691C  mftb r3 ; subf. r11,r3,r11 ; ble 0x4768DC  ; already due -> next
```

So a time table is a cyclic array of 8-byte `{action, delta}` records; the
action of record *k* runs at `sum(delta[0..k-1])`, and `0x47820C`
(`os_time_table_wrap`: `[-0x1A10] = [-0x1A0C]`) is the end marker.
`0x478034` (`os_start_time_table`) and `0x478218` (`os_switch_time_table`)
install one; the state is `[r13-0x1A10]` = current entry, `[r13-0x1A0C]` =
base, `[r13-0x1A08]` = accumulated deadline. **VERIFIED-STATIC.**

Two tables exist, both 18 entries, both a 35080-tick (50 ms) cycle built out
of ten 3508-tick (1 ms) steps:

| Activations per cycle | Table A `0x478EE4` | Table B `0x478F80` |
|---|---|---|
| 10 | id 21 `0x4240C8` -> **1 ms** | id 33 `0x11EBF4` -> **1 ms** |
| 5 | id 24 `0x424900` -> **2 ms** | id 36 `0x11EC34` -> **2 ms** |
| 2 | id 26 `0x424AF8` -> **5 ms** | id 38 `0x11EC58` -> **5 ms** |

Their descriptors `{0, base, base}` are at `0x478F74` (A) and `0x479010` (B).
`os_init` installs **A** (`bl 0x478218` at 0x11B130 with r3 = 0x478F74).

### 11.4 The slow rasters: alarm 1 and a chain of five dividers

The **alarm callback vector** is at **0x478DF8** (kernel config +0x68, which
becomes K+0x60); it has three entries:

| Alarm | Callback | Armed by |
|---|---|---|
| 0 | `0x0B0934` = ActivateTask(id 42, `0x12BC40`) | `os_SetRelAlarm(0, 7719, 7719)` at 0x12CF5C (**2.2 ms**); `(0, 280701, 0)` at 0x12B19C / 0x135810 (**80 ms** one-shot) |
| 1 | **`0x443F74`** = `os_raster_select` | `os_SetRelAlarm(1, 701, 35087)` at 0x11B15C — first shot 0.2 ms, cycle **10 ms** |
| 2 | `0x40C1D8` (`b 0x46080`) | `os_SetRelAlarm(2, 7017, 0)` at 0xA7A34 (**2 ms** one-shot) |

`os_raster_select` (0x443F74) picks the task set from one RAM byte:

```
00443F80  lbz   r3,-0x549B(r13)            ; 0x7FAB55
00443F84  cmpwi r3,0 ; bne 0x443F9C
00443F8C  lis r3,0x48 ; lwz r3,-0x7844(r3) ; bl 0x475E8C   ; id 19 = 0x4328E4
00443F9C  cmpwi r3,2 ; bne 0x443FB0
00443FA4  lis r3,0x48 ; lwz r3,-0x7568(r3) ; bl 0x475E8C   ; id 32 = 0x1205A0
```

**So `0x4328E4` and `0x1205A0` are the same raster in two builds, and their
period is alarm 1's cycle = 35087 ticks = 10 ms.** `os_init` writes 0 to
0x7FAB55 at 0x11B148, so set A is live from reset; 0x11DAF4 writes 2 and at
the same time switches the time table to B (`bl 0x478218` at 0x11DAEC with
r3 = 0x479010), gated on the byte 0x7FEB5E.

The 10 ms task's body then runs the divider chain — `bl 0x40BEF0` at
0x432BBC inside `0x4328E4`, `bl 0x40C064` at 0x1206AC inside `0x1205A0`,
both unconditional members of the flat `bl` list — five identical blocks of

```
0040BF8C  lwz    r12,-0x3D20(r13)
0040BF90  addic. r3,r12,-1 ; stw r3,-0x3D20(r13) ; bne +0x18
0040BF9C  li     r12,2     ; stw r12,-0x3D20(r13)
0040BFA4  lis r3,0x48 ; lwz r3,-0x7804(r3) ; bl 0x475E8C     ; id 23 = 0x45CAC4
```

| Counter | Divider | Set A | Set B | Period |
|---|---|---|---|---|
| 0x7FC2D0 | /2 | id 23 **`0x45CAC4`** | id 35 `0x120FAC` | **20 ms** |
| 0x7FC2D4 | /5 | id 25 | id 37 | 50 ms |
| 0x7FC2D8 | /10 | id 18 | id 31 | 100 ms |
| 0x7FC2DC | /20 | id 22 | id 34 | 200 ms |
| 0x7FC2E0 | /100 | id 17 | id 30 | 1000 ms |

The counters are seeded staggered — `{1, 6, 8, 0x18, 0x5E}` at 0x1344D0-
0x1344F4 — so the slow rasters land in different 10 ms slots.
**VERIFIED-STATIC.**

The tail of `0x40BEF0` (0x40BFB0-0x40C040) recomputes alarm 1's cycle every
fifth call through `os_set_alarm_cycle(1, …)` at `0x476E94`:
`cycle = (0x80 + (V-100)/2) * 35087 / 128`, clamped to [0x890F, 0xAB53], from
the byte `V` at 0x7FCE95. So the 10 ms raster — and with it every divided
raster — can be **stretched to 12.5 ms**. At `V <= 100` (its cold-start
value) the cycle is the nominal 35087. This is the only source of raster
jitter in the image; what `V` is was not chased (it is written outside the
scheduler).

### 11.5 The deadline windows now make sense

With the corrected unit every `os_set_deadline_timer` window is 1.5x or 2x the
period of the raster it guards, which it was not before:

| Timer | Window | Guards | Ratio |
|---|---|---|---|
| 2 | 7017 = 2 ms | the 1 ms raster (re-armed in its own epilogue, 0x42408C / 0x11EBF0) | 2x |
| 3 | 14035 = 4 ms | the 2 ms raster (0x4248AC / 0x11ED74) | 2x |
| 0 | 105263 = 30 ms | the 20 ms raster | 1.5x |
| 1 | 1052631 = 300 ms | the 200 ms raster | 1.5x |

and section 5.3's `/5` divider in the 2 ms epilogue (0x11ED78, 0x4248B0)
checks timer 0 every **10 ms**, three times per 30 ms window, instead of once
per window. **HYPOTHESIS** for which raster each timer guards; VERIFIED-STATIC
for the windows and for the check cadence.

### 11.6 The emulated cross-check (VERIFIED-DYNAMIC)

`emu/os_clock.py` runs `os_time_table_dispatch`, `os_raster_select` and both
divider chains out of the dump against a virtual Time Base (`mftb` rewritten
*in emulator memory only* to read a scratch cell the driver advances to
whatever the code programs into TBREF0; `os_ActivateTask` and
`os_set_alarm_cycle` stubbed to a `blr` plus a hook that does what their tails
do). Five simulated seconds, task set A:

```
flag      id  entry     activations   period (ticks)   period (ms)
007FE61C  21  004240C8         5002             3508      1.000
007FE622  24  00424900         2501             7016      2.000
007FE626  26  00424AF8         1001            17540      4.999
007FE618  19  004328E4          500            35087     10.000
007FE620  23  0045CAC4          250            70174     20.000
007FE624  25  000C78F4           99           175435     49.999
007FE616  18  000FB974           50           350870     99.998
007FE61E  22  00115AE0           24           701740    199.996
007FE614  17  0005BD3C            5          3508700    999.980
```

Set B gives the same nine periods on flags 0x7FE632 / 0x7FE638 / 0x7FE63C /
0x7FE630 / 0x7FE636 / 0x7FE63A / 0x7FE62E / 0x7FE634 / 0x7FE62C. There is no
jitter: every gap equals every other gap for the same task.

### 11.7 What one bench log must still record

Two things the dump cannot answer:

> **SETTLED 2026-09-17 (E1, issue #34), item 1 only — see §11.8.** Task set A
> is live by necessity; item 2 (the absolute rate) is still a bench read, and
> the counter log below is still worth doing as the confirmation.

1. **Which task set runs with the engine turning.** Set A (`0x4328E4`,
   `0x45CAC4`) is installed by `os_init`; `0x11DA64` — a process of the
   priority-0 init task — switches to set B if the byte 0x7FEB5E is non-zero
   (written 1 at 0x0BDB28, 0 at 0x1341C4, both alongside a state byte at
   0x7FCED8). Set A's process lists are much longer (214 against 67 processes
   at 100 ms), which suggests A is the normal one, but that is **HYPOTHESIS**.
2. **The absolute rate**, i.e. the 56 MHz of `can.md` section 3.

One log settles both. The raster counters are 32-bit words in
0x7FD740-0x7FD7A0; the five that matter are

| Cell | Incremented by | Expected rate |
|---|---|---|
| 0x7FD75C | set A 1 ms, at `0x424078` | 1000 /s |
| 0x7FD754 | set A 10 ms, at `0x4328C8` | 100 /s |
| 0x7FD760 | set B 1 ms, at `0x11EBE0` | 1000 /s |
| 0x7FD778 | set B 2 ms, at `0x11ED5C` | 500 /s |
| 0x7FD758 | set B 10 ms, at `0x120584` | 100 /s |

C3's `wave_b_confirm.json` already logs the three set-B cells; **0x7FD754 and
0x7FD75C should be added to it**, because if set A is the live one the three
set-B counters stay frozen and the log proves nothing about the periods. Read
any pair of these counters twice, N seconds apart, at idle:

* set B live and this section right -> 0x7FD760 gains ~1000 N, 0x7FD778
  ~500 N, 0x7FD758 ~100 N (0.25 % low, and lower still if the load byte
  0x7FCE95 stretches the cycle);
* section 5.4 right instead -> one fifth of that (200 / 100 / 20 per second);
* set A live -> the three set-B counters do not move at all, and 0x7FD75C /
  0x7FD754 carry the 1000 /s and 100 /s instead.

A 2 % tolerance separates the two hypotheses, so any logger that timestamps to
50 ms and runs for 10 s does it.

### 11.8 Added 2026-09-17 (brief E1, issue #34) — **task set A is live by necessity** (VERIFIED-STATIC)

§11.7's first question does not need the bench after all. Two values the
engine cannot run without are produced **only** by processes of task set A,
and nothing in set B — or in the shared event/ISR tasks — reaches a producer
of either. SETTLED for §11.7 item 1; item 2 (the absolute rate) is still a
bench read, and a bench read-back of the raster counters is still the proof
of *this* result, because the argument below is a static reachability
argument and cannot see an indirect call.

**The two producers, and their single call sites.**

```bash
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x45822C
#   file 0x258C08  cpu 0x45CC08  bl        <- the only caller
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin 0x431294
#   file 0x22EB04  cpu 0x432B04  bl        <- the only caller
```

| Producer | Writes | Only caller | Inside |
|---|---|---|---|
| `hdrpsol_main` 0x45822C | `prsoll` 0x8031F4 (`sth r26,0x3204(r13)` at 0x45872C) | 0x45CC08 | **set A** 20 ms task 0x45CAC4 (id 23) |
| `zwstt` builder 0x431294 | `zwstt` 0x802096 (`stb r31,0x20A6(r13)` at 0x431384) | 0x432B04 | **set A** 10 ms task 0x4328E4 (id 19) |

**Every writer of the two cells, from the whole image.**

```bash
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x8031F4
./.venv/bin/python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x802096
```

`prsoll` has 11 references and exactly **two** stores: 0x45872C (above) and
0x132300. The second one is a one-shot default inside the small function at
**0x1322E0**

```
001322E0  lis   r12,0x5D ; lbz r12,0x521E(r12) ; rlwinm. (bit 7 of 0x5D521E)
001322EC  beq   0x132308
001322F0  li    r12,0 ; sth r12,0x31F2(r13)            ; 0x8031E2
001322F8  lis   r3,0x5D ; lhz r3,0x5576(r3)            ; a calibration word
00132300  sth   r3,0x3204(r13)                          ; prsoll <- constant
00132304  sth   r3,0x31F0(r13)
00132308  blr
```

which copies a **calibration constant** into `prsoll`, has no `bl` caller
anywhere in the image (`callgraph.py`), and is referenced by exactly one
pointer word, **0x0B1E94** — below the task process-list block that starts at
0x0B1ED4, i.e. it is in the one-shot init list, not in any task's process
list. It is an initialiser, not the running setpoint producer.

`zwstt` has 5 references and exactly **one** store to 0x802096, at 0x431384.
(The `stb r4,0x20A7(r13)` at 0x1137E0 writes 0x8020**97**, the byte *after*
`zwstt`, so it is not a writer of it.)

**The reachability walk.** `tools/callgraph.py`'s `reachable()` seeded with
*every process* of every task descriptor (`tools/ercosek_tasks.py`
`decode_thunks`, the `procs` list, wrappers 0x0B5878/0x0B5978 removed):

| Seeds | Tasks | Processes | Functions reached | `hdrpsol_main` | `zwstt` builder |
|---|---|---|---|---|---|
| set A (ids 17-19, 21-26) | 9 | 478 | 1,633 | **reached** (only from id 23) | **reached** (only from id 19) |
| set B (ids 30-38) | 9 | 105 | 572 | not reached | not reached |
| event/ISR/common (ids 0-9, 15, 16, 20, 28, 29, 39-42) | 19 | 163 | 804 | not reached | not reached |

The common row matters as much as the set-B row: the segment task (id 40) and
the ignition task (id 41) are in it, so the two producers are not hiding in
the engine-synchronous half either.

**Conclusion.** If task set B were the live one, `prsoll` would keep whatever
the init list left in it and `zwstt` would never be computed at all — the
high-pressure pump would have no setpoint and the start ignition angle no
producer. The engine cannot run that way, so **set A is the live set**, which
is also what §11.7's process-count argument suggested (214 against 67
processes at 100 ms). VERIFIED-STATIC.

**Caveats, both real.**

* The walk resolves relative branches only. Set B's reach contains **125**
  `bctrl`/`blrl` sites and 11 computed `bctr` jumps that truncate it (set A:
  162 and 28; common: 62 and 5). None of the four dispatch tables that feed
  them is a plausible route to `%HDRPSOL` or the `zwstt` builder, but the walk
  cannot prove it.
* The bench read-back of the five raster counters in §11.7 is still worth
  doing, and now has a prediction to falsify: 0x7FD754 and 0x7FD75C move,
  0x7FD758 / 0x7FD760 / 0x7FD778 stay frozen.

**No hook was changed.** `patches/ff_fuel` keeps both 10 ms hooks
(0x432940 in set A, 0x12067C in set B) and its `ff_src_owner` arbitration:
this is a static argument, the patch's redundancy costs one flash word plus
`ff_src_seen`, and `ff_src_seen` is the one-sample bench confirmation of
exactly this section.

## 12. Added 2026-09-16 (brief C4) — the clock registers, from MPC561RM

`documents/MPC561RM.pdf`, extracted with
`pdftotext -layout documents/MPC561RM.pdf -` (method: `re/findings/fr_index.md`).
This closes two of section 10's open items.

### 12.1 `SCCR = 0x03217100` (0x6FC280, MPC561RM section 8.11.1, Figure 8-16)

Bits are numbered MSB = 0.

| Bits | Field | Value | Meaning |
|---|---|---|---|
| 0 | DBCT | 0 | timers follow the clock mode in limp mode |
| 1:2 | COM | 00 | CLKOUT enabled, full-strength buffer |
| 3 | DCSLR | 0 | clock switching on loss of lock during reset enabled |
| 4 | MFPDL | 0 | MF / DIVF stay writable |
| 5 | LPML | 0 | LPM / CSRC stay writable |
| **6** | **TBS** | **1** | **time base source = system clock / 16** |
| 7 | RTDIV | 1 | RTC and PIT clock divided by 256 |
| 8 | STBUC | 0 | do not switch to the backup ring oscillator |
| 9 | CQDS | 0 | |
| 10 | PRQEN | 1 | switch to the DFNH frequency on an interrupt |
| 11 | RTSEL | 0 | OSCM is the RTC/PIT source |
| 12 | BUCS | 0 | the system clock is not the backup clock |
| 13:14 | EBDF | 00 | CLKOUT = GCLK2 / 1 |
| 15 | LME | 1 | limp mode enabled |
| 16:17 | EECLK | 01 | ENGCLK full-strength output |
| 18:23 | ENGDIV | 0b110001 = 49 | ENGCLK = VCO/2 / 50 |
| 25:27 | DFNL | 000 | low frequency = /2 (unused, PRQEN = 1) |
| 29:31 | DFNH | 000 | **system clock = FREQsysmax / 1** |

Table 8-2 ("TMBCLK Divisions") gives division 16 whenever TBS = 1, and section
6.1.6 says the decrementer — which is coherent with the Time Base — "is
clocked by the TMBCLK clock", one increment per TMBCLK. So

> **Time Base frequency = system clock / 16 = 56 MHz / 16 = 3.5 MHz,
> one tick = 285.714 ns.** VERIFIED (manual + the register value in the dump).

### 12.2 `PLPRCR = 0x00015080` (0x6FC284, section 8.11.2, Figure 8-17)

MF (bits 0:11) = 0 and DIVF (bits 27:31) = 0, and
`System Frequency = OSCCLK / (DIVF+1) x (MF+1) / 2^DFNH` (section 8.5), so the
part runs in **1:1 mode: system clock = the oscillator/EXTCLK input**, and the
56 MHz of `can.md` section 3 is the board's clock input, not a PLL product.
The bit the boot sets and polls at 0x116FC/0x11704 (0x8000 = bit 16) is
**SPLSS**, the sticky loss-of-lock bit: write 1 to clear, then wait for it to
stay 0. Note that the boot at 0x116DC-0x116F0 writes MF and DIVF from
registers, so 0x00015080 is the settled value, not a literal in the code.

### 12.3 `0x6FC048` / `0x6FC04C` are SIMASK2 / SIMASK3 — confirmed

MPC561RM's USIU register map lists `0x2F C048 Interrupt Mask2 Register
(SIMASK2)` and `0x2F C04C Interrupt Mask3 Register (SIMASK3)`; with the ISB=1
relocation those are 0x6FC048 / 0x6FC04C. Section 3.3's names move from
HYPOTHESIS to **VERIFIED** (manual). The neighbours 0x6FC040 / 0x6FC044 are
SIPEND2 / SIPEND3 and 0x6FC050 / 0x6FC054 are SISR2 / SISR3.
