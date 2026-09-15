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

`tbl_os_task_control_blocks` at **0x478634** (file 0x274634), 25 entries of
0x24 bytes, anchored by the constant 0x004764FC at +0x04 of every entry
(**VERIFIED-STATIC**):

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
| Decode `SCCR = 0x03217100` / `PLPRCR = 0x00015080` to get the real system clock, and confirm TB = 701.754 kHz | open — needs `documents/MPC561RM.pdf`, which is not in the repo (`documents/SOURCES.md` lists it; only `pico2_pinout.*` are present) |
| Confirm 100 ms / 1000 ms rather than 150 ms / 1500 ms | open — one dynamic run, or the periods of the OS alarm that activates the tasks |
| Which ISR activates each raster task (nothing writes the TCB flag bytes with an r13-relative store; activation goes through the TCB pointer) | open |
| What the seven ISR tasks (ids 1-7) are bound to | open — B6/B7 |
| Are `0x6FC048`/`0x6FC04C` really SIMASK2/SIMASK3? | open — needs the manual |
