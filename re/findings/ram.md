# RAM usage survey — where a patch may put its variables

Agent C2, brief `docs/agent_briefs/C2_ram_survey_static.md`, issue **#23**
(static half). Date: 2026-09-16. Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.

Tools written for this: `tools/ram_survey.py` (per-byte survey, indexed-region
hunt, stack estimate), `tools/ram_snapshot_diff.py` (the dynamic half),
`emu/ext_sram_probe.py`. Data: `re/ram_map.csv`,
`logging/sessions/ram_snapshot.json`. Tests: `tests/test_ram_survey.py`.

Reproduce everything in this file with

```bash
python3 tools/ram_survey.py data/passat_azx_ori.bin --csv re/ram_map.csv
python3 tools/ram_survey.py data/passat_azx_ori.bin --indexed --indexed-min 0x40
python3 tools/ram_survey.py data/passat_azx_ori.bin --stack
python3 -m emu.ext_sram_probe
python3 tools/ram_snapshot_diff.py --self-test
```

Tags: **VERIFIED-STATIC** = derived from the bytes of the dump;
**VERIFIED-DYNAMIC** = observed in the emulator; **HYPOTHESIS** = inference.

---

## 1. The short answer

**Recommended patch RAM: `0x7FFB00`, 0x100 bytes (`PATCH_RAM` /
`PATCH_RAM_SIZE` for brief C1).** It sits in the middle of
**0x7FF770-0x7FFFEB**, 2,172 bytes of internal SRAM that

* no instruction in the image references — not by an r13 displacement, not by
  an absolute `lis`+D-form, not through a pointer word in either flash region
  (section 2);
* the cold start does not fill (section 3), so the patch must validate its own
  contents with a magic word;
* lies **above** the task stack, which grows *down* from 0x7FF770 (sections 4
  and 5);
* the KWP programming copy never touches (section 6);
* is outside the KWP protected window, so C3's logger can read it back
  (section 8).

**C1's placeholder `PATCH_RAM = 0x807F00` must be changed.** 0x807F00 is
inside 0x804800-0x807FFF, the destination of the block the KWP programming
service copies out of flash and then *executes* (section 6). A patch that
writes there during a flash session would corrupt the running flash driver.

The tag to use in `re/symbols.csv` and `docs/06_patch_pipeline.md` is
**VERIFIED-STATIC for "nothing in the image names these bytes"**, and
**dynamic confirmation pending #23** for "nothing writes them at run time" —
the protocol is in section 8.

---

## 2. Method

`tools/ram_survey.py` builds a per-byte picture of 0x7F8000-0x807FFF out of
four independent scans and a table of known structures.

1. **r13 small-data accesses.** r13 = 0x7FFFF0 in the boot module *and* the
   application (file 0x10E0, 0x986AC, 0x9E3E0, 0x405588), so one base covers
   the whole image. The scan decodes every D-form load/store/`addi` whose rA is
   13 and marks the *width* of the access, so the halfword at 0x8031DA marks
   two bytes, not one. `addi` is counted separately as `addr`: it forms a
   pointer, and the byte it names is normally the base of a structure
   (section 4).
   **64,717 loads/stores + 2,640 `addi`/`ori` = 67,357**, spanning
   0x7F8000-0x8076D6.
   > *Correction to `docs/02_memory_map.md` section 4.* That section says
   > "64,727 r13-relative accesses … RAM 0x7F8000-0x8073E9". The count is
   > close but the span is short: the highest r13 target is **0x8076D6**
   > (`addi`, and 0x8070EC for a load). The 10-access difference is an opcode
   > set difference, not a fact about the ECU. **VERIFIED-STATIC.**
2. **Absolute `lis` + D-form / `addi` / `ori` pairs**, the resolver of
   `tools/find_abs_refs.py` restricted to RAM targets: **3,783**.
3. **Pointer words**: every aligned 32-bit word of *both* flash regions whose
   value is a RAM address: **1,010** (874 in code, 136 in the calibration
   block). `tools/find_branch_refs.py` already scans both regions — the brief
   asked whether it covers the on-chip flash 0x404000-0x47FFFF and it does,
   because it walks the whole dump and maps offsets through
   `med9lib.file_to_cpu`. No change was needed.
4. **Measuring variables**: the 660 cells of `re/measuring_vars.csv` that lie
   in RAM are marked as used. They are read through the dispatch table at
   0x045768, so some have no ordinary reference of their own.

**The calibration block 0x1C0000-0x1FFFFF is excluded from the instruction
scans.** Disassembling it invents references — 51 of them are
`lbzu/lfdu/stfsu rX,d(r13)` *update* forms, which no compiler emits because
they would overwrite the small-data base. Including the block also produced
the bogus "highest reference 0x807CB9" (a `stfdu` at file 0x089784 inside a
data table). Pointer words *are* still looked for there, because the
memory-test descriptor lists live at 0x5C2E14-0x5C2E8C.

Coverage: **22,578 of the 65,536 RAM bytes (34.5 %) carry at least one static
reference**; 362 of the 2,048 32-byte lines carry none and are not inside a
known structure.

The whole map is `re/ram_map.csv`: one row per 32-byte line with
`r13_read, r13_write, r13_addr, abs_read, abs_write, abs_addr, ptr_words,
ptr_cal, init_bytes, known, free_candidate`.

### 2.1 Page map

One character per 256 bytes; `#` read+write, `w` write only, `r` read only,
`p` pointer/`addi` only, `K` known structure with no per-byte reference,
`.` nothing.

```
  0x7F8000  ########p...p.pp#####p#pppp##r#rrKKp#.#wr###r###################
  0x7FC000  ###r##ppp####################p###....#pp########p..pKKKpKKKKKKr#
  0x800000  ###KKK#ppppp###########################################ppp#####w
  0x804000  w.ppppppp#KKKKpKKKKpKpppKKKKKKKKKKKKKKKKKKKKKKKKrKKKKKKKKpKKwpKp
```

The `KKK…` block from 0x804A00 on is the programming copy (section 6); the
`KKKpKKKKKK` at 0x7FF300-0x7FFF00 is the stack and the recommended region.

---

## 3. What the cold start fills — and what it does not

`app_entry_crt0` (0x09E3B4) reaches **160 functions**. Walking all of them and
extracting the `lis`+`addi` bounds of every `stwu rV,4(rP)` / `bdnz` fill loop
gives fourteen ranges (`ram_survey.py` prints them; `COLDSTART_FILLS` in the
tool carries the evidence per range). **VERIFIED-STATIC.**

| Range | Bytes | Filled by |
|---|---|---|
| 0x7F802C-0x7F807B | 80 | `app_entry_crt0` 0x09E3B4 @ 0x09E3D0 |
| 0x7F8080-0x7F80E7 | 104 | `app_entry_crt0` @ 0x09E410 |
| 0x7F80EC-0x7F80FB | 16 | `FUN_0012C25C` @ 0x12C348 |
| 0x7F8104-0x7F8232 | 303 | `app_init` 0x04CCD4 @ 0x04CD8C |
| 0x7F8490-0x7FA62F | 8,608 | `app_init` @ 0x04CF94 |
| 0x7FA630-0x7FAABF | 1,168 | `app_init` @ 0x04CFCC |
| 0x7FAAC4-0x7FB32F | 2,156 | `app_init` @ 0x04CEDC |
| 0x7FB330-0x7FDA8F | 10,080 | `app_init` @ 0x04CDC4 |
| 0x7FDA90-0x7FE0BF | 1,584 | `ram_clear_block` 0x06D8F8 @ 0x06DAC0 |
| 0x7FE588-0x7FEFDF | 2,648 | `app_init` @ 0x04CDFC |
| 0x7FF3C0-0x7FF76B | 940 | `FUN_0012C25C` @ 0x12C40C — **the task stack** |
| **0x800004-0x800D07** | 3,332 | `app_init` @ 0x04CF1C |
| **0x800D08-0x80361F** | 10,520 | `ram_clear_block` @ 0x06DB24 |
| **0x803620-0x80498F** | 4,976 | `ram_clear_block` @ 0x06DB88 |

The call chain is
`app_entry_crt0 0x09E3B4 → … → app_init 0x04CCD4 (reached by the thunk
0x0BA9A8, which the on-chip start-up 0x405588 also branches to) →
bl 0x06D8F8 at 0x04CD84`.
`python3 tools/callgraph.py data/passat_azx_ori.bin --callers 0x06D8F8` gives
the single caller 0x04CD84.

### 3.1 Correction: the external SRAM **is** cleared

> **2026-09-16 (C2, issue #23) — REFUTES `re/findings/eeprom.md` section 6
> point 2 and the premise of this brief. VERIFIED-STATIC.**
> eeprom.md says "Neither application start-up clears it … the external SRAM
> has no initialised-data image at all, so the region is **not** cleared".
> The first half is right — the `.data` copy really is empty (src = dst =
> 0x800000 at 0x08A1AC and at 0x09E438) — but the `.bss` fill is done by a
> *different* routine, `ram_clear_block` at **0x06D8F8**, which zeroes
> **0x800004-0x80498F** in three loops, and by `app_init` 0x04CCD4.
> Of the external SRAM only **0x800000-0x800003** (the word
> `ext_sram_probe` saves and restores) and **0x804990-0x807FFF** survive a
> reset, and all of the latter is the programming copy's destination.
>
> **Consequence for D1 and for the flex-fuel state:** the external SRAM cannot
> be used to carry an ethanol estimate across a key cycle. It is ordinary
> `.bss`. The EEPROM proposal of `eeprom.md` section 5 (block 8, payload
> offset +0) remains the way to persist the value; the "keep the byte in
> external SRAM and mirror it at key-off" fallback in that section is **no
> longer available** and should not be used.

### 3.2 Not filled at cold start

```
0x7F8000-0x7F802B   44 B      0x7FAAC0-0x7FAAC3    4 B
0x7F807C-0x7F807F    4 B      0x7FE0C0-0x7FE587 1224 B
0x7F80E8-0x7F80EB    4 B      0x7FEFE0-0x7FF3BF  992 B
0x7F80FC-0x7F8103    8 B      0x7FF76C-0x800003 2200 B
0x7F8233-0x7F848F  605 B      0x804990-0x807FFF 13936 B
```

A patch block in a filled range gets `.bss` semantics for free; a block in an
unfilled range is **undefined at power-on** and the patch must carry a magic
word (and preferably a checksum) and initialise itself when it does not match.
The recommended block is in an unfilled range, so it needs that header.

### 3.3 The memory-test descriptors have no consumer — still true

`eeprom.md` section 6 says nothing reads the four start/end lists at
0x5C2E14 / 0x5C2E24 / 0x5C2E50 / 0x5C2E78. That holds: no `lis`+`addi`, no
r2/r13 displacement and no pointer word reaches them, and the routines that
*do* clear or check RAM carry their bounds as inline constants instead. The
lists are nevertheless informative, because the ranges they name
(0x7F8104-0x7F8368, 0x7F8490-0x7FAAC0, 0x7FAAD0-0x7FE0BC, 0x7FF770-0x7FFFEC
and the external SRAM) exclude exactly the stack and the kernel area — see
section 6. `FUN_0006D384` is a *read-only* accumulator over
0x7F84A0-0x7FA62C (an "is this RAM still zero?" check), not a destructive
pattern test. **VERIFIED-STATIC.**

---

## 4. Known structures, with sources

The table below is `KNOWN` in `tools/ram_survey.py`; each entry carries its
source there as well.

| Range | Name | Tag | Source |
|---|---|---|---|
| 0x7F8012 | `ext_sram_size_code` | VERIFIED-DYNAMIC | `ext_sram_probe` 0x011898; section 7 |
| 0x7F802C-0x7F807B | `crt0_bss_clear_1` | VERIFIED-STATIC | `app_entry_crt0` 0x09E3C0-0x09E434 |
| 0x7F8080-0x7F80E7 | `crt0_bss_clear_2` | VERIFIED-STATIC | same |
| 0x7F9E3C-0x7FA47F | `kwp_protected_window` | VERIFIED-DYNAMIC | `kwp_upload_range_check` 0x0A3160, kwp.md 5.1 |
| 0x7F9E80-0x7FA47F | `eep_mirror` | VERIFIED-STATIC | `ptr_eep_mirror_base` 0x0B3184, eeprom.md 3 |
| 0x7FD2CC-0x7FD2EB | `immo_eeprom_mirror` | VERIFIED-STATIC | `eeprom_read_immo_block` 0x085F44 |
| 0x7FE588-0x7FE837 | `os_kernel_ram` | HYPOTHESIS | kernel configuration block 0x09B5EC-0x09B76C |
| 0x7FE588 / 0x7FE5A0 / 0x7FE5A4 | stack-pointer chain, kernel object ptr | VERIFIED-STATIC | scheduler.md 7 |
| 0x7FE5FC-0x7FE644 | `os_task_activation_flags` | VERIFIED-STATIC | `tbl_os_task_control_blocks` +0x14 |
| 0x7FF01C-0x7FF3BF | `stack_overshoot_estimate` | HYPOTHESIS | section 5 |
| 0x7FF3C0-0x7FF76F | `os_task_stack` | VERIFIED-STATIC | kernel stack descriptor + the fill at 0x12C40C |
| 0x7FF770-0x7FFFEB | `free_above_stack` | HYPOTHESIS | this section; **recommended** |
| 0x7FFFF0-0x7FFFFF | `r13_sda_anchor` | VERIFIED-STATIC | r13 = 0x7FFFF0 |
| 0x800000-0x800687 | `kwp_prog_copy_alias_tail` | VERIFIED-STATIC | section 6 |
| 0x803DA4-0x803DB3 | `kwp_io_struct` | VERIFIED-STATIC | kwp.md 1.4 |
| 0x803EE4-0x803FEB | `can_rx_shadow` | VERIFIED-STATIC | can.md 4, 22 × 12 B |
| 0x804800-0x807FFF | `kwp_prog_copy_dest` | VERIFIED-STATIC | section 6 |

### 4.1 The kernel stack descriptor — new

Three RAM addresses appear as pointer words at flash **0x09B6F8-0x09B704**
(and again in the on-chip copy):

```
0009B6F8  007FFFEC        <- top of the RAM the OS is told it owns
0009B6FC  007FF770        <- stack top; app_entry_crt0 sets r1 = 0x7FF768 = this - 8
0009B700  007FF730        <- stack top - 0x40, the watermark window's upper end
0009B704  007FF3C0        <- stack limit
0009B708  0000036C        <- 0x7FF730 - 0x7FF3C0 - 4, the checked size
...
0009B72C  007FE64C  007FD710  007FE834  007FE820  007FE828
0009B740  00000004  0000000C  00000048   <- 0x48 = the ISR frame of scheduler.md 7
0009B750  007FE5F8   <- just below tbl_os_task_control_blocks' flag bytes
```

Independent confirmation: `FUN_0012C25C` fills exactly **0x7FF3C0-0x7FF76B**
with a constant at 0x12C40C — the stack, and nothing else in that
neighbourhood. **VERIFIED-STATIC** for the stack region 0x7FF3C0-0x7FF76F.

The first word, 0x7FFFEC, is read here as *the end of the OS RAM*, not as the
top of a second stack, for three reasons (**HYPOTHESIS**, three independent
arguments):

1. ISRs do **not** switch stacks: the exception prologue at 0x405588+ starts
   `stwu r1,-0x48(r1)` (file 0x2015A0), i.e. it pushes onto whatever r1 the
   interrupted code had. There is no second stack to have a top.
2. The only three constants the image ever loads into r1 are 0x7FEFFC (boot,
   file 0x10DC) and 0x7FF768 (application, at 0x0986A8, 0x09E3C4 and
   0x405584). `ram_survey.py` finds no other `lis`+`addi r1` anywhere.
   0x7FFFEC is never loaded into r1.
3. 0x7FFFEC is `0x800000 - 0x14`, the last word below the r13 anchor, i.e. the
   natural "end of RAM" constant.

### 4.2 The boot stack top and the application stack top, reconciled

`docs/02_memory_map.md` gives stack top 0x7FEFFC; `boot.md` section 2.3 gives
r1 = 0x7FF768 for `app_entry_crt0`. Both are right, at different times, and
the boot one is dead afterwards:

* the boot module runs on 0x7FEFFC downwards until the single `blrl` at
  0x1307C hands control to `app_entry_crt0`, which immediately re-bases r1 to
  0x7FF768. `boot_main_init` never returns (0x10F4 is `ba 0x110F0`, the fatal
  spin), so the boot frame is never used again.
* the RAM under the old boot stack is then reused as ordinary application
  data: 0x7FEF00-0x7FEFFC carries dense r13 traffic (e.g. 0x7FEF6A has 27
  reads and 16 writes), and 0x7FE588-0x7FEFDF is in the cold-start fill list.
* **so 0x7FEFFC is not a stack address in the running ECU and must not be used
  to reason about free space.** The live stack is 0x7FF3C0-0x7FF76F.
  **VERIFIED-STATIC.**

### 4.3 Correction to `re/findings/boot.md` section 2.3 and `re/symbols.csv`

Both say `app_entry_crt0` "zeroes 0x80002C-0x80007C and 0x800080-0x8000E8".
The disassembly at 0x09E3C0-0x09E434 computes
`0x800000 - 0x7FD4 = 0x7F802C` … `0x800000 - 0x7F84 = 0x7F807C` and
`0x800000 - 0x7F80 = 0x7F8080` … `0x800000 - 0x7F18 = 0x7F80E8`: the ranges are
**0x7F802C-0x7F807B and 0x7F8080-0x7F80E7, in the *internal* SRAM**, not
0x8000xx. `eeprom.md` section 6 has it right. Corrected in `re/symbols.csv`
on 2026-09-16. **VERIFIED-STATIC.**

### 4.4 Correction to `re/findings/scheduler.md` section 4

`tbl_os_task_control_blocks` at 0x478634 has 25 rows but **not** a uniform
0x24 stride: the seven ISR tasks (ids 1-7, 0x4788A8-0x478968) use **0x20**,
and there are gaps before 0x4787DC and before 0x4789E8. Find the rows by the
anchor word 0x004764FC at +0x04, which is what `ram_survey.py --stack` does;
the 25 entries it recovers match scheduler.md's table exactly.
**VERIFIED-STATIC.**

---

## 5. Stack estimate

`ram_survey.py --stack` walks, from each of the 25 task entries, the deepest
chain of `stwu r1,-N(r1)` prologues through the static call graph.

| Task | Depth | Chain head |
|---|---|---|
| 0x4328E4 (100 ms, on-chip) | **0x588** | → 0x431EE8 → 0x04D8A4 → 0x04D9C0 → 0x04BDD0 → 0x06BCC4 … |
| 0x45CAC4 (1000 ms) | 0x538 | → 0x45822C → 0x06E8E0 → 0x06E408 → 0x069574 … |
| 0x11CA44 | 0x530 | → 0x1323EC → 0x06BCC4 … |
| 0x424900 (20 ms) | 0x528 | → 0x40B778 → 0x04BDD0 … |
| 0x11B188 (`task_background`) | 0x150 | |

Worst chain **0x588 B**, plus one 0x48-byte ISR frame = **0x5D0 B**. From the
stack top 0x7FF770 that reaches **0x7FF1A0**, which is 0x220 *below* the
descriptor limit 0x7FF3C0.

Two readings, and the conservative one is used (**HYPOTHESIS**):

* the walk assumes every `bl` in a function is taken, so the deep paths
  (diagnostics and EEPROM code reached from a raster task) may be mutually
  exclusive at run time; **or**
* the stack really does run below 0x7FF3C0 and the descriptor's 0x36C only
  bounds the *watermark check*, not the stack.

The RAM below 0x7FF3C0 is unreferenced down to **0x7FF01B**, which is exactly
the room the second reading needs. So **0x7FF01C-0x7FF76F is marked "stack
(estimate)"** and must not be used. 74 of the 1,351 functions walked contain
an indirect call (`bctrl`/`blrl`) that truncates a chain, so 0x5D0 is a *lower*
bound on the true worst case — another reason to keep away from the whole
region below the stack top.

Whichever reading is right, the stack grows **down**, away from
0x7FF770-0x7FFFEB.

---

## 6. The KWP programming copy — and why 0x807F00 is the wrong address

`FUN_0008A12C` (file 0x08A12C) copies flash **0x081A00-0x085887**, i.e.
**0x3E88 bytes**, to **0x804800**:

```
0008A12C  lis r4,8 ; addi r4,r4,0x1a00      ; src   = 0x00081A00
0008A134  lis r11,8 ; addi r11,r11,0x5888   ; src_e = 0x00085888
0008A13C  subf r11,r4,r11 ; srawi r5,r11,2  ; 0x3E88 -> 0xFA2 words
0008A148  lis r7,0x80 ; addi r7,r7,0x4800   ; dst   = 0x00804800
```

so the destination is **0x804800-0x808687**. Its callers are 0x086A28,
0x087494 and 0x088828, all KWP/flash-programming entry points (eeprom.md
section 6), and 0x0861A8-0x0861B8 then *runs* the relocated block
(`bl 0x806EA0`, flash original 0x0840A0, with r2 = 0xD4CDF0 — boot.md 3.2).

**0x808688 is 0x688 bytes past the end of a 32 KB CS1 part**, and OR1 =
0xFFFC0000 makes 0x808000-0x83FFFF the same 32 KB again (docs/02 section 3,
confirmed by emulation in section 7). So on the 32 KB hardware the copy also
overwrites **0x800000-0x800687**. Both ranges are in the `KNOWN` table.

`ram_survey.py --indexed --base 0x804800` reproduces the extent as 0x3E80
(the linear walk loses one loop iteration out of 0x7D1; the true value 0x3E88
is the one above).

### 6.1 Calibration of the indexed-region method

The brief names two regions whose extent is known independently. The hunt is
calibrated on both:

| base | tool: extent | tool: stride | truth | source of truth |
|---|---|---|---|---|
| 0x804800 | **0x3E80** (loop-bounded) | — | **0x3E88** | the copy's flash source bounds, above |
| 0x803EE4 `can_rx_shadow` | 0xC | **0xC** | 22 × 12 = **0x108** | `can.md` 4 |

So the method recovers a **loop** extent almost exactly and a **record size**
exactly (`mulli r11,r25,0xc` at 0x233AF0 for `can_rx_shadow`), but it cannot
recover a record *count* when the index is a function argument, as it is for
`can_rx_shadow` — that count comes from `can.md`'s enumeration of the
`can_init_mb` call sites. The neighbour bound is no help there either (slot 1's
data at 0x803EF4 is referenced, so it reports 0x10).

**Read the output accordingly:** `stride` and `extent` are facts about the
code, the *count* usually is not. For the placement decision that is enough,
because what matters is whether an array reaches into a candidate run, and the
neighbour bound answers that whenever the array is contiguous.

Consequences:

* **Nothing in 0x804800-0x807FFF may hold patch state**, and not because the
  data would be lost — the ECU resets after a flash session anyway — but
  because a patch that *writes* there while the relocated driver is
  *executing* there would corrupt the flash driver mid-session. C1's
  placeholder **0x807F00 is inside this block and must be changed.**
* `docs/06_patch_pipeline.md` section 3 suggests "the top of external SRAM
  (0x8057xx-0x807FFF, no static references)". That is the same block, and
  0x805784 is not free either: it is the base of a **RAM-resident dispatch
  table of 0x1C-byte entries with a function pointer at +0x18**, indexed by a
  state byte and called through `mtlr`/`blrl` at 0x082C00-0x082C08.
  **VERIFIED-STATIC.** The suggestion is withdrawn in this document and in
  docs/06.

---

## 7. `ext_sram_probe` emulated — 32 KB or 64 KB

`emu/ext_sram_probe.py` runs 0x011898 twice (**VERIFIED-DYNAMIC**,
`python3 -m emu.ext_sram_probe`):

| model | instructions | RAM 0x7F8012 | 0x800000 |
|---|---|---|---|
| no aliasing (64 KB part) | 33, returned | **0x44** = 64 KB | sentinel preserved |
| 0x808000 folded onto 0x800000 (32 KB part) | 27, returned | **0x41** = 32 KB | sentinel preserved |

The probe touches exactly **0x7F8012, 0x7F8013, 0x800000-0x800003 and
0x808000-0x808003** and restores both memory words it used; 0x7F8013 is
cleared on entry and never written again. On the aliased model the branch at
0x118F8 is taken, so the `stwx r3` restore of 0x808000 at 0x11910 is skipped —
harmless, because the alias means `stw r8,0(r9)` at 0x11914 restores the same
cell.

**Placement consequence:** unless a bench read of 0x7F8012 returns 0x44,
nothing above 0x807FFF is extra RAM, and the linker must never be given a
RAM region that starts at or above 0x808000. `logging/sessions/ram_snapshot.json`
asks C3 to read 0x7F8012 as the first thing it does.

---

## 8. Candidate blocks, ranked

Need (brief C1 / `docs/05_flexfuel_design.md`): **64 B for `ff_fuel` + 16 B for
`ff_counter`**, 256 B preferred. Ranking criteria: size, distance to the
nearest static reference, whether an indexed region reaches into it, whether
the programming copy overlaps it, and whether the cold start clears it.

| # | Block | Size | Cleared at start | Prog. copy | Verdict |
|---|---|---|---|---|---|
| **1** | **0x7FF770-0x7FFFEB** | **2,172 B** | no | no | **RECOMMENDED.** Zero references of any kind; above the stack top, which grows away from it; the largest such region in either SRAM. HYPOTHESIS that 0x7FFFEC is "end of OS RAM" (section 4.1). |
| 2 | 0x7FE0C0-0x7FE587 | 1,224 B | no | no | Fallback. Zero references, but it sits directly below the kernel RAM at 0x7FE588 and could be a second stack. Same dynamic test settles it. |
| 3 | 0x7F8233-0x7F848F | 605 B | no | no | Partly referenced (0x7F8428, 0x7F842C, 0x7F83A0-0x7F83A8 are SPI pointers); the free part is short. |
| — | 0x7FF01C-0x7FF3BF | 932 B | no | no | **REJECTED**: stack (estimate), section 5. |
| — | 0x804549-0x8046CB | 387 B | yes | no | **REJECTED**: it is the body of the 32 × 12-byte array based at 0x804548 (`mulli r3,r31,0xc` at 0x05E538, index from `cntlzw` of a 32-bit mask, so 0..31 → 0x804548+4+0x180 = 0x8046CC, exactly the next referenced byte). |
| — | 0x8040F6-0x804237 | 322 B | yes | no | **REJECTED**: body of the 16-byte-record array at 0x8040F5 (`slwi r11,r3,4; stb r10,0(r12)` at 0x233934-0x233940). |
| — | 0x8043C9-0x8044C7 | 255 B | yes | no | **REJECTED**: 0x8043C8 and 0x8044C8 are buffer pointers in the flash tables 0x0B3190 and 0x072558, exactly 0x100 apart. |
| — | 0x7F8CC1-0x7F8EDF | 543 B | yes | no | **REJECTED**: 0x7F8CC0 and 0x7F8EE0 are buffer pointers in the flash table at 0x01E510, 0x220 apart. |
| — | 0x7FA4A6-0x7FA62B | 390 B | yes | no | **REJECTED**: 0x7FA4A5 is passed as a pointer argument to `FUN_000340EC` at 0x032768; the callee decides the length. |
| — | 0x7F88B0-0x7F8CBF | 1,040 B | yes | no | **REJECTED**: 0x7F8890 is a base with 102 `lis`+`addi` sites and an unbounded indexed extent. |
| — | 0x804990-0x807FFF | 13,936 B | no | **yes** | **REJECTED**: section 6. |

**This is the main result of the indexed-access hunt (task 2):** *every*
reference-free run of more than 128 bytes inside the used `.bss` areas that
was examined turned out to be the body of an array or buffer whose base is the
last referenced byte before it. Only the three regions at the top of the table
have no owner at all. What could not be bounded: the bases with many sites and
an `st?x`/`lwzx` index that the linear walk cannot bound —
0x7F8890 (102 sites), 0x7F802C (75), 0x7F8080 (71), 0x80485C (24),
0x803620 (20), 0x7FC39C (18), 0x804088 (17) — for all of them the *neighbour
bound* (distance to the next referenced byte) is under 0x100, so they do not
affect the recommendation. Where the walk sees a `mulli`/`slwi` it also reports
the **record size** even when it cannot bound the count — 0xC for 0x804548 and
for `can_rx_shadow`, 0x10 for 0x8040F5, 0x1C for the dispatch table at
0x805784. Time spent on the hunt: within the brief's 2 h box.

### 8.1 The recommended block

```
PATCH_RAM      = 0x7FFB00
PATCH_RAM_SIZE = 0x100          /* 256 bytes */
```

* 0x390 bytes above the stack top 0x7FF770 and 0x4EC below 0x7FFFEC — the
  largest margin on both sides inside the free region.
* Suggested layout: `+0x00` magic `u32`, `+0x04` version/length, `+0x08` `u16`
  checksum over the rest, `+0x10..+0x4F` `ff_fuel` (64 B), `+0x50..+0x5F`
  `ff_counter` (16 B), `+0x60..+0xFF` spare.
* **The patch must initialise it.** 0x7FF770-0x7FFFEB is not in any cold-start
  fill, so at power-on it holds whatever survived; on a mismatch of magic or
  checksum the patch must zero the block and set defaults. Do not rely on RAM
  retention for anything the engine needs — persist the ethanol value in the
  EEPROM (eeprom.md section 5, block 8 offset +0).
* **Address it absolutely.** `tools/blobdis.py --check-sda` fails on *any*
  reference to r2 or r13, including as a base register, so patch code must
  build the address with `lis r11,0x80 ; addi r11,r11,-0x500` (= 0x7FFB00) and
  never with an r13 displacement.
* The block is internal SRAM, so it is on the fast on-chip bus; the external
  SRAM is an 8-bit port (BR1 = 0x800403), four bus cycles per word.

---

## 9. The dynamic half of #23 — what the snapshots must show

`logging/sessions/ram_snapshot.json` holds the ranges; they cover
0x7F8000-0x807FFF except the KWP protected window **0x7F9E3C-0x7FA47F**, which
`kwp_upload_range_check` (0x0A3160) rejects with NRC 0x31 — 63,932 bytes,
1,031 TransferData blocks of 62 bytes.

**Protocol** (C3's logger, one JSON file per session in
`logging/sessions/`):

1. **key-on**, ignition on, engine not started, as soon as the ECU answers.
   Read 0x7F8012 first and record it: 0x41 = 32 KB, 0x44 = 64 KB (section 7).
2. **idle**, engine running and warm, at least 2 min after start.
3. **after a drive**, engine off but ignition still on, after a run that has
   exercised knock control, the fuel adaptations and the rail controller.
4. **key cycle 1, 2, 3**: key off, wait for the ECU to power down (the main
   relay drops ~10 s after key-off), key on, snapshot immediately.

Then

```bash
python3 tools/ram_snapshot_diff.py logging/sessions/*.json --free 64 \
        --csv work/ramdiff.csv
```

Every byte is classified `changed` (differs between snapshots — live RAM),
`constant` (identical everywhere but not 0x00/0xFF — live RAM holding a
constant, treat as used) or `blank` (0x00 or 0xFF in every snapshot).

**The placement is confirmed when, and only when:**

* every byte of **0x7FF770-0x7FFFEB** is `blank` in all six snapshots. A
  single `changed` byte there means the region is live and the recommendation
  falls back to candidate 2 (0x7FE0C0-0x7FE587), and if that also moves, to a
  pair of small blocks in the cleared `.bss` with the array bounds of section 8
  re-checked by hand.
* the stack range 0x7FF000-0x7FF76F shows `changed` bytes down to some floor —
  that floor is the measured stack low-water mark and should be compared with
  the 0x5D0 B static estimate of section 5. If it reaches above 0x7FF770, the
  recommendation is wrong and must be withdrawn immediately.
* 0x804990-0x807FFF is `changed` only across a programming session, never
  between the six normal snapshots.
* the three key cycles answer the retention question of `eeprom.md` section 6
  for the four bytes 0x800000-0x800003, the only external-SRAM bytes the cold
  start leaves alone: if they keep their value across a key cycle, the CS1
  SRAM is on a permanent supply.

The snapshots cannot prove a byte is free — only that nothing wrote it in the
sessions recorded. Combined with "no instruction in the image names it"
(section 2) that is as strong as this project can get without the ECU's source.

---

## 10. Open questions

| Question | Status |
|---|---|
| Is 0x7FFFEC "end of OS RAM" or the top of a second stack? Three static arguments say the former (section 4.1); the snapshots decide. | open, blocks nothing — the recommendation is safe either way only if the snapshots are clean |
| Is 0x7FE0C0-0x7FE587 a second stack? It is unreferenced, uncleared, and sits right under the kernel RAM. | open |
| Is our hardware's CS1 SRAM 32 KB or 64 KB? Only RAM 0x7F8012 at run time answers it. | open, needs the bench |
| Exact extent of the RAM dispatch table at 0x805784 (0x1C-byte entries, function pointer at +0x18). Who fills it? | open; it is inside the programming copy area, so it does not affect placement |
| The 74 functions with indirect calls that truncate the stack estimate. | open; the measured low-water mark replaces the estimate |
| What consumes the memory-test descriptor lists at 0x5C2E14-0x5C2E8C? Still nothing found. | open, inherited from eeprom.md 6 |
