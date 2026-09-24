# End-to-end workflow

Issue **#42**, step 1. Written 2026-09-17 by brief **E7** for a reader who did
not build any of this: how to go from a clean checkout to a changed ECU and
back, with the commands that were actually run on this Mac and their real
output.

Everything here except chapter 3 was executed in this worktree on 2026-09-17
against `data/passat_azx_ori.bin`
(SHA-256 `b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`).
**Chapter 3 is the one chapter nobody has run** — no ECU has ever been written
by this project — so every step in it is marked with what it is predicted to
do and what would prove it.

This document cross-links rather than repeats. The reference for each area:

| Area | Document |
|---|---|
| Addresses, checksums, dump layout | [`02_memory_map.md`](02_memory_map.md) |
| Installing the tools | [`03_tooling.md`](03_tooling.md) |
| Evidence rules, the pre-flash checklist | [`04_re_guidelines.md`](04_re_guidelines.md) §6, §7 |
| Flex-fuel design and its test plan | [`05_flexfuel_design.md`](05_flexfuel_design.md) |
| Patch pipeline internals | [`06_patch_pipeline.md`](06_patch_pipeline.md) |
| Tool-by-tool reference | [`../tools/README.md`](../tools/README.md) |
| Log format and the logger | [`../logging/README.md`](../logging/README.md) |
| TunerPro definition | [`../re/README.md`](../re/README.md) |

---

## 0. Before anything

### 0.1 The four rules that never bend

1. **`data/passat_azx_ori.bin` is never modified.** It is the car's own ECU
   read and the roll-back file. Its SHA-256 must stay
   `b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`; the test
   suite fails if it changes. Work on copies under `work/` (gitignored).
2. **Nothing in this repository ever talks to an ECU.** Every tool here writes
   files. A human does the flashing, following chapter 3.
3. **Checksums are corrected, never disabled** (docs/01 §3 principle 4).
4. **Bench before car, spare before own** (docs/01 §3 principle 2), and
   **one variable at a time** (principle 5).

### 0.2 Set-up, once

The Python environment (docs/03 §1). **Python 3.13, not 3.14** — Ghidra 12's
jpype wheels stop at `cp313`:

```bash
python3.13 -m venv .venv
```

```bash
./.venv/bin/pip install -r requirements.txt
```

Use `./.venv/bin/python3` for everything. On this Mac the bare `python3` is a
different interpreter (miniconda 3.9) without the project's packages.

The cross toolchain for chapter 2 only (docs/03 §3.1): LLVM 23.1.1 for macOS
ARM64, unpacked at `/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64`, which is
`patches/common/patch.mk`'s default `LLVM_DIR`. Nothing in chapters 1, 4 or 5
needs it.

### 0.3 The check that starts every session

```bash
./.venv/bin/python3 tools/checksum.py verify -q data/passat_azx_ori.bin
```

```
ALL OK (65 blocks)
```

If that prints anything else, stop: the working copy of the dump is not the
car's read and nothing downstream means anything.

The whole suite, which asserts the dump's SHA-256 before and after every test
that loads it:

```bash
./.venv/bin/python3 -m unittest discover -s tests
```

```
Ran 591 tests in 118.721s

OK
```

(2026-09-17, integration/wave-E. About two minutes; the patch-framework and
simulator tests dominate.)

---

## 1. A calibration-only change

Changing numbers in the stock calibration. No compiler, no patch directory —
this is the TunerPro route, and it is the cheapest thing that can change how
the engine runs.

### 1.1 The definition file

`re/med9_draft.xdf` is the TunerPro definition: 1,079 tables and 179 constants
built from `re/calibration_draft.csv` (the machine-detected inventory) plus
`re/calibration_names.csv` (the hand-maintained names, units and scalings) plus
`patches/ff_fuel/ffcal001_rows.csv` (our own calibration block). It is checked
in; regenerating it is the **integrator's** job at merge time, not a step in
this workflow. To confirm the checked-in one is structurally sound:

```bash
./.venv/bin/python3 tools/draft_to_xdf.py --validate re/med9_draft.xdf
```

```
re/med9_draft.xdf: 1079 tables, 179 constants, 0 problems
```

Seven things about it that will otherwise cost an afternoon — the full list is
`re/README.md` "Using it in TunerPro":

* **Addresses in the XDF are file offsets**, `<baseoffset>` is 0. Open the
  `.bin` directly, apply no offset. The calibration window the ECU calls
  0x5C0000-0x5FFFFF is file 0x1C0000-0x1FFFFF.
* Values are **raw counts** wherever no scaling has been derived
  (`equation="X"`). Read the table's description before treating a number as a
  physical quantity.
* `cand_` means the name is a candidate, not a Bosch label; `cand_KF_…` plus an
  address means nobody has looked at that object at all.
* Every FFCAL001 cell reads 255 until a patched image is loaded — 0x5E2510
  upwards is erased flash in the stock file.

### 1.2 Edit on a copy

```bash
cp data/passat_azx_ori.bin work/tune.bin
```

Open `work/tune.bin` in TunerPro with `re/med9_draft.xdf`, change what you
meant to change, save. The worked example below is `KFZW`, the base ignition
map (CPU 0x5C75FE = file 0x1C75FE, 16 rows × 12 columns of s8, 0.75 degCA/LSB,
`re/findings/ignition.md` §2): one cell taken from 40 counts (30.00 degCA) to
36 (27.00 degCA), which lands at file 0x1C7663.

That example is not arbitrary — it is the last step of issue **#45**, the list
of bench edits that would turn the wave-B map addresses from VERIFIED-STATIC
into VERIFIED-DYNAMIC. The other four (`KFZW`'s bank offset 0x5C753A, `KFKSTT`
0x5C6E24, `KFPRSOLOFF` 0x5D5424, `KRKATE` 0x5D3DBC) each name the map, the
change and the logged variable that must move; #45 is the natural first use of
this chapter once a bench ECU exists.

### 1.3 Fix the block checksums

A saved file always fails, because the edit is inside one of the 65 Bosch
blocks:

```bash
./.venv/bin/python3 tools/checksum.py verify work/tune.bin
```

```
  desc 0x1c3320  0x5c2e00-0x5cffff  stored 165e1139  actual 165e1135  BAD
1 BAD (65 blocks)
```

`fix` does **not** edit in place — it writes `FILE.fixed.bin` unless `-o` names
an output:

```bash
./.venv/bin/python3 tools/checksum.py fix work/tune.bin -o work/tune_fixed.bin
```

```
  desc 0x1c3320  0x5c2e00-0x5cffff  165e1139 -> 165e1135
1 descriptor(s) updated
wrote work/tune_fixed.bin
ALL OK (65 blocks)
```

```bash
./.venv/bin/python3 tools/checksum.py verify -q work/tune_fixed.bin
```

```
ALL OK (65 blocks)
```

Run `fix` on an untouched dump and it changes nothing — that round trip is part
of the test suite.

### 1.4 Diff it, and read the diff

```bash
./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/tune_fixed.bin
```

```
descriptor cpu 0x1c332b-0x1c332b [alias 0x5c332b]  file 0x1c332b (1 B)  39 -> 35  desc 0x1c3320 (table 0x1c3300 entry 2)
descriptor cpu 0x1c332f-0x1c332f [alias 0x5c332f]  file 0x1c332f (1 B)  c6 -> ca  desc 0x1c3320 (table 0x1c3300 entry 2)
unexpected cpu 0x1c7663-0x1c7663 [alias 0x5c7663]  file 0x1c7663 (1 B)  28 -> 24
3 changed range(s): 0 patch (0 B), 2 descriptor (2 B), 1 unexpected (1 B)
block checksums of work/tune_fixed.bin: ALL OK
RESULT: FAILED
```

**`RESULT: FAILED` and exit status 1 are the expected outcome here**, and this
is the one place in the whole workflow where a red result is read rather than
obeyed. `bindiff` classifies every changed byte into three buckets:

| Bucket | Meaning |
|---|---|
| **patch** | a change listed in the `patch.json` given with `-p`. A calibration-only edit has no `patch.json`, so this is always 0 here |
| **descriptor** | a checksum `sum`/`~sum` word that `checksum.py fix` rewrote. Two bytes per affected block: one in the sum, one in its complement. Always expected |
| **unexpected** | everything else |

With no `-p`, *your own edit* lands in **unexpected**. So the check is manual
and it is the whole point: **every unexpected range must be inside the map you
meant to change**. Here `file 0x1c7663` is inside `KFZW`
(0x1C75FE + 8×12 + 5 = 0x1C7663), one byte, `28` → `24` — which is 40 → 36
counts, the edit from §1.2, and nothing else moved. If a range appears that you
cannot place in a table you opened, the definition is wrong about that table's
size, or TunerPro wrote something you did not look at. Stop and find out which.

The other half of the check — cheap, and it catches the worst mistake:

```bash
cmp -l data/passat_azx_ori.bin work/tune_fixed.bin | awk '$1 >= 0x1CEE21 && $1 <= 0x1CEE70'
```

Nothing printed means the identification block (0x1CEE20-0x1CEE6F, 1-based for
`cmp`) is untouched. It must never move: docs/04 §6 item 3, and
`tools/patch_apply.py` enforces the same thing for code patches with a flag
that nothing can unlock.

### 1.5 The E0 rule

docs/01 §3 principle 3: **a patched file at E0 must behave identically to
stock.** For a calibration change that means the honest version of the same
rule — you have changed the engine deliberately, so record a stock baseline log
before flashing and a matching log after, and compare them with chapter 5. A
calibration edit has no "disabled" state to fall back to; the roll-back file is
the only way back, which is why chapter 6 exists.

---

## 2. A code change

The flex-fuel patch is the worked example. Read
[`06_patch_pipeline.md`](06_patch_pipeline.md) once before doing this for real;
this chapter is the operating procedure, not the design.

### 2.1 What a patch directory is

```
patches/ff_fuel/
  Makefile           NAME := ff_fuel, plus include ../common/patch.mk
  patch.json         metadata + `build` (what you write) + `changes` (generated)
  ffcal001.py        this patch's own calibration-block generator
  ffcal001_rows.csv  its descriptor rows, in re/calibration_draft.csv's columns
  src/*.c            the features, one file each
  src/hooks.S        the trampolines
  test/              bench procedures and tolerance.json (NOT the unit tests)
  README.md          hooks, RAM, calibration, and the disassembly of every hook
```

The unit tests live in `tests/` with everything else
(`tests/test_ff_fuel_patch.py` and its four siblings). `patches/common/` holds
the shared framework: `patch.ld`, `patch.mk`, `hooks.S`, `types.h` and the
**generated** `med9_stock.h`. A new patch starts as
`cp -r patches/examples/hello_patch patches/<name>`; the six steps are in
`patches/README.md`.

Two rules that the tools enforce so you do not have to remember them:

* **`changes` is generated, never hand-edited** — `make gen` and the test suite
  both overwrite it and compare.
* **Never a literal stock address in patch code.** Add the `re/symbols.csv`
  name to `WANTED` in `tools/gen_stock_header.py` and regenerate
  `patches/common/med9_stock.h`.

### 2.2 Build: check, dump, gen, apply

Four targets, in this order. They are the whole pipeline.

```bash
cd patches/ff_fuel && make check
```

```
== stock header is current ==
OK: …/patches/common/med9_stock.h is up to date with …/re/symbols.csv
== sections ==
  [ 1] .text             PROGBITS        00152000 002000 001e0c 00  AX  0   0  4
  [ 2] .rodata           PROGBITS        00153e0c 003e0c 000000 00   A  0   0  1
  [ 3] .bss              NOBITS          007ffb00 00fb00 00005c 00  WA  0   0  4
== blob size vs linked flash size ==
OK: ff_fuel: blob 7692 B == linked flash size, .bss 92 B <= 256 B
== r2 / r13 usage in the emitted code (must be empty) ==
OK: r2 and r13 are never referenced
== small data sections (must be empty) ==
OK: no .sdata/.sbss/.srodata/.sdata2
```

`make check` fails on a stale `med9_stock.h`, on any reference to **r2 or r13**
(patch code must address RAM absolutely — docs/06 §3), on a non-empty small-data
section, and if the blob is not exactly the size the linker says, which is how
an orphan section gets caught.

```bash
cd patches/ff_fuel && make dump
```

```
00152000  94 21 FF F0  stwu     r1, -0x10(r1)
00152004  7C 08 02 A6  mflr     r0
00152008  90 01 00 0C  stw      r0, 0xc(r1)
0015200C  48 00 0E 0D  bl       0x152e18
00152010  80 01 00 0C  lwz      r0, 0xc(r1)
00152014  7C 08 03 A6  mtlr     r0
00152018  38 21 00 10  addi     r1, r1, 0x10
0015201C  48 41 C3 A2  ba       0x41c3a0
```

That is the first trampoline of 1,926 lines. `make dump` is
`tools/blobdis.py --addr 0x00152000 --check-sda` on the **raw blob**, not on
the ELF: `llvm-objdump` has no `-b binary`, and reading the ELF instead of the
bytes the CPU will fetch is exactly the mistake the pre-flash checklist exists
to prevent. Note `ba 0x41c3a0` (AA=1, absolute) rather than `b` — docs/06 §4
explains why a numeric `b` operand silently assembles as a displacement.

**Read this output.** Paste the hook extracts into the patch's `README.md`;
`patches/ff_fuel/README.md` has one per hook site.

```bash
cd patches/ff_fuel && make gen
```

```
wrote patch.json: 31 change(s)
  blob 7692 B at 0x152000, .bss 92 B of 256 B at 0x7ffb00
  hook 0x42247c: bl 0x41c3a0 -> bl 0x152000 (ff_fuel_rk_hook), word 4bff9f25 -> 4bd2fb85
  hook 0x12067c: bl 0x11f02c -> bl 0x152040 (ff_fuel_hook_b), word 4bffe9b1 -> 480319c5
  hook 0x41d40c: the instruction 7c635214 -> bl 0x152060 (ff_zw_hook), word 7c635214 -> 4bd34c55
  data 0x5e2510: 332 B from build/ffcal001.bin
  data 0x0a78a8: 16 B = ff_diag_e_pct 0x152174, ff_diag_f_pct 0x1521f8, ff_diag_t_degc 0x15227c, ff_diag_mode 0x152310
  ram symbol ff_state at 0x7ffb00
```

(Extract; the real run lists all 8 hooks and all 23 data entries.)
*(Note 2026-09-23, G2: this transcript and the `make apply` one below predate
G1. Since FFCAL001 v5 the data line reads 334 B, and G1's PID 0x52 edits add
data entries. The blob size, range counts and SHA-256 differ from the ones
printed here. Your own run is the reference; the hook count is still 8.)*
`tools/patch_gen.py` resolves every hook target from the linker's `.sym`,
encodes the branch word with its reach and alignment checked, reads each
change's `old` bytes out of the stock image, and asserts that a new block's
target bytes really are 0xFF. `make gen` on an unchanged tree leaves
`patch.json` byte-identical — `git status` staying clean is the check.

```bash
cd patches/ff_fuel && make apply
```

```
ff_fuel: 159 patch range(s) (7977 B), 18 descriptor range(s) (54 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: 193223e2ea47c8274f97d961683466b168cf75f484adcaf0b92d4d856e94a817
WARNING: ff_fuel: "ram_status": "static" - the RAM block at 0x007FFB00 is VERIFIED-STATIC only (re/findings/ram.md): no instruction references it, but the runtime snapshots of issue #23 are still pending. Do not flash this image.
WARNING: change at 0x42247c+0x4 writes the MPC561 on-chip flash (0x404000-0x47FFFF). The block checksums are handled, but a KESSv2 write of this region has not been demonstrated: read the image back and compare before trusting it.
[…six more on-chip warnings…]
wrote ../common/../../work/ff_fuel.bin
wrote ../common/../../work/ff_fuel.diff.json
wrote ../common/../../work/ff_fuel.sha256
```

`tools/patch_apply.py` is **the only tool in this repository that modifies an
image**. It never touches its input, never writes into `data/`, and writes
nothing at all unless all six of docs/06 §5 pass: `base_sha256`, the forbidden
regions, every `old`, checksum fix-then-verify, the identification block, and a
clean `bindiff`.

### 2.3 The three guards, and what unlocks them

| Region | Unlocked by |
|---|---|
| 0x000000-0x00FFFF boot block / immobiliser | nothing, ever |
| 0x400000-0x403FFF the 16 KB absent from our read | nothing, ever |
| 0x1CEE20-0x1CEE6F identification block | nothing, ever |
| 0x1C0000-0x1DFFFF stock calibration | `"calibration_edit": true` on that change |
| 0x404000-0x47FFFF on-chip flash | `"onchip_edit": true` on that change |

The check folds every CPU alias to one canonical address first, so the
calibration cannot be reached through 0x5Cxxxx to get around it.
`"onchip_edit"` prints a warning on **every** apply even when present: a clean
`ff_fuel` apply prints seven of them, one per on-chip hook word.

`"ram_status"` is the third guard and it is not a region: anything other than
`"verified"` makes `patch_apply.py` print a loud do-not-flash line.
`patches/ff_fuel` and `patches/ff_counter` are both `"static"` — the RAM block
0x7FFB00/0x100 has no static reference anywhere in the image
(`re/findings/ram.md` §8.1), but the runtime RAM snapshots of issue **#23**
have not been taken. **That warning is a blocker, not a formality.**

### 2.4 FFCAL001, and the rule for changing it

The patch's own calibration block: **v4, 332 bytes at CPU 0x5E2510**, inside
checksum block 0x5E0000-0x5EFFFF, built by `patches/ff_fuel/ffcal001.py` before
`make gen` reads it. It grew v1 → v4 across briefs D1, E1, E2 and E5.

> **Updated 2026-09-23 (G2):** it is now **v5, 334 bytes** (`LENGTH = 0x014E`
> in `patches/ff_fuel/ffcal001.py`). Brief G1 appended the one-byte
> `ff_pid52_enable` gate at +0x14A for OBD PID 0x52 (docs/05 §3.7, note of
> 2026-09-23). The rule below is how it was done.

The rule (docs/agent_briefs/00_common_rules.md, 2026-09-17) is that **FFCAL001
changes append and nothing moves**. Adding a value means, in one commit:

1. append the field in `ffcal001.py` past the current end;
2. bump `VERSION` and `LENGTH` there and mirror both in `src/ff_state.h`;
3. make `ff_cal_ok()` accept **only** the new version;
4. regenerate `ffcal001_rows.csv` (the XDF descriptor rows — the integrator
   appends them, you never edit `re/calibration_draft.csv`);
5. update `emu/models/flexfuel.py`, the `tests/test_ff_*` files and
   `logging/sessions/ff_fuel.json`.

`struct ff_state` grows only past its current end for the same reason, and the
core/annex checksum split documented in `ff_state.h` must stay true.

Every feature ships **disabled**: an `ff_<feature>_enable` byte defaulting to 0
*and* a neutral table underneath it. Of the four features only the fuel scaling
is on in the shipped file.

### 2.5 Prove it in the emulator, before anything else

The standard of proof for a feature is **two bit-identity runs** — the hooked
stock code must be bit-identical (a) with the feature disabled and (b) with it
enabled at neutral calibration — shown by a whole-SRAM diff that moves nothing
outside the state block.

```bash
./.venv/bin/python3 -m unittest tests.test_ff_rail_patch
```

The tests that carry that standard read as their own documentation:
`test_prail_add_0_is_bit_identical_on_every_path`,
`test_enabled_with_the_neutral_curve_is_also_inert`,
`test_with_dzw_e_zero_the_patched_image_is_bit_identical`,
`test_an_invalid_state_block_is_bit_identical_too`. `emu/README.md` explains the
harness and its seven Unicorn limitations; the one that bites patch work most
is that **XER is not observable through the Python API** — whether a trampoline
restored it can only be decided with `mfxer` inside emulated code.

### 2.6 The regression check, before the file goes anywhere

```bash
./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/ff_fuel.bin -p patches/ff_fuel/patch.json -q
```

```
177 changed range(s): 159 patch (7977 B), 18 descriptor (54 B), 0 unexpected (0 B)
block checksums of work/ff_fuel.bin: ALL OK
RESULT: OK
```

**With a `patch.json`, `unexpected` must be 0 and `RESULT: OK`.** This is the
opposite of §1.4: there, your edit was unexpected by construction; here, every
intended byte is declared, so anything unexpected is a bug. `make apply` already
ran this and kept the report in `work/ff_fuel.diff.json`; running it by hand is
how you check a **read-back** from a real ECU (chapter 3).

Then the suite, and the disassembly of every hook site one last time
(docs/06 §5 items 4 and 5).

---

## 3. Flashing — the human's chapter

**Nothing below has been done.** No ECU has been written by this project; there
is no spare ECU, no BDM tool and no `data/backup_bdm/MANIFEST` yet (issues
#1-#4, #26-#28). Every step is marked **[unverified]** where its behaviour is a
prediction rather than an observation. The first write is called **Flash 0**
(`patches/ff_fuel`) or **Flash 1** (`patches/ff_counter`, the no-op counter,
issue #27) — Flash 1 first, because it changes **two flash words** (one of them
on-chip since brief F1, 2026-09-22 — see the note in §3.4) and nothing else.

> **Corrected 2026-09-24 (H4):** the naming in the sentence above predates
> #26 and is superseded. Now **Flash 0** = the *unmodified* dump re-saved
> through our tools (#26; item 2 of the list below, §6.4), **Flash 1** =
> `patches/ff_counter` (#27), and `patches/ff_fuel` is the E0-equivalence
> flash after both (#32; `docs/08` step 7). The first write of all is Flash 0,
> then Flash 1 — no patch is "Flash 0".

> **2026-09-24 (G6):** the day-one order across this chapter, `logging/README.md` §8, the #44 reads, the #23 RAM snapshots and the Flash 1 decision table, with the stop list, is [`08_bench_playbook.md`](08_bench_playbook.md) — follow it on the bench day; this chapter stays the procedure it points at.

What the dump *does* now tell us comes from brief **E6**,
`re/findings/flash_programming.md` (2026-09-17, VERIFIED-STATIC), and it is
better news than this chapter assumed when it was drafted. Quoting that file:

* The ECU's own OBD programming service — session `10 85`, which **reboots the
  ECU** into a second, 13-entry KWP stack at 0x088174 with SID 0x34
  `RequestDownload` — can erase and program **0x404000-0x47FFFF**. That range is
  in the hard-coded whitelist `kwp_download_range_allowed` (0x0889C8) together
  with 0x020000-0x07FFFF and 0x0A0000-0x1BFFFF, so **all seven on-chip hook
  words of `patches/ff_fuel` are reachable over OBD in principle.**
* There is **no boot-time integrity gate on flash content**: the 65 block sums
  are never recomputed at boot, and the runtime CRC-32 the firmware publishes
  is compared with nothing.
* The one hard check is the **`5A5A` marker at file 0x1E2500**. If it is wrong
  the ECU reboots into the flash loader instead of starting the application —
  recoverable, not a brick, but it must be recognised as such.

What that does **not** settle: whether KESSv2 protocol 179 *drives* that route
for that range. That is a property of the tool, not the firmware, and the
read-back in §3.3 is still the only proof.

### 3.1 Pre-flight — docs/04 §6, verbatim in intent

Every item must be true before any write. Quoting `04_re_guidelines.md` §6:

| # | Check | How |
|---|---|---|
| 1 | `checksum.py verify -q FILE` prints `ALL OK (65 blocks)` | `make apply` already did it; do it again on the file you are about to send |
| 2 | The diff against the source file is listed and every changed byte range is explained | §2.6 with `-p`, `unexpected` = 0 |
| 3 | The file was built from **this ECU's own read**, never a downloaded file, and the identification block (0x1CEE20) is unchanged | `patch_apply.py` checks `base_sha256` and the ident block and cannot be overridden |
| 4 | The target ECU has a verified full backup (external flash, on-chip flash, EEPROM) with its SHA-256 in `data/backup_bdm/MANIFEST` | **[unverified — the file does not exist. K-TAG/BDM read still to be taken; it is also the only way to get the 16 KB at 0x400000-0x403FFF that KESSv2 does not read.]** |
| 5 | First run on the **bench spare**; only files that ran there go to the car | **[unverified — no spare ECU yet, issue #2]** |
| 6 | Stable supply/charger at ≥ 13.5 V, no accessories, no interruptions, laptop on mains | physical |
| 7 | After writing: read back, compare, clear DTCs, log a short drive/idle, compare with the baseline | §3.3 and chapters 4-5 |
| 8 | Roll back with the original file if anything is unexplained. Do not "fix forward" on the car | chapter 6 |

And from docs/04 §6's closing line: never disable the ROM check, immobiliser
pairing or component protection to make something work.

### 3.2 Write

Per docs/06 §6: write with **KESSv2 (protocol 179)** from the Windows machine.
KESS applies its own checksum correction; **because our file already verifies,
its correction must be a no-op** — if KESS reports that it corrected something,
stop and find out what. **[unverified]**

Before the write, record the stock baseline log of the scenario you will repeat
afterwards (chapter 4). There is no second chance to record a baseline on a
flashed ECU.

### 3.3 Read back, and compare — the step that decides everything

```bash
./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/readback.bin -p patches/ff_fuel/patch.json --json work/readback.diff.json
```

Exit 0 means every changed byte is one of ours or a checksum descriptor.
docs/06 §6: *if the read-back differs from what we wrote outside the
descriptors, stop and investigate — that would mean a check we do not know
about.*

Then the three checks E6's `flash_programming.md` §7.2 adds, in order:

1. **Read back 0x404000-0x47FFFF and bindiff it against the written file.**
   This is the one measurement that decides issue #32. If KESS writes the
   on-chip array the bytes match; if it silently skips it they are the stock
   bytes. Per-word, for the two D1 hooks
   (`patches/ff_fuel/test/procedure.md` §1 has the full table):

   ```bash
   ./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x21E47C --addr 0x42247C --len 4
   ```

   | Read-back | Meaning |
   |---|---|
   | `bl 0x152000` | KESS writes the on-chip flash; the fuel hook is live |
   | unchanged `4B FF 9F 25` | KESS wrote only the external flash. The patch is inert on the fuel path — the external set-B raster hook 0x12067C still fills the state block, so §§2-3 of the bench procedure still work as a receive test, but `rk` is never scaled. Record it and reopen #32 with a BDM/BSL plan |
   | some on-chip words changed, others not | **stop.** That is a partial write and the image is not what either tool thinks it is |

2. **Confirm file 0x1E2500 reads `5A 5A 5A 5A`** in whatever was flashed. A
   wrong calibration marker means the ECU loops into the flash loader rather
   than starting — expected and recoverable, but recognise it rather than
   mistaking it for a brick.

3. **Read EEP_CONF block 10 (EEPROM offset 0x260, 32 B) before and after.** The
   ECU stamps an identification string and a status word there on every
   `10 85`, so a changed block 10 is independent evidence that the ECU's own
   programming route ran; the classifier byte at +0x13 says which range family
   the tool asked for. `tools/eeprom_map.py` decodes the block table.

**Do not** let any tool address 0x000000-0x01FFFF, 0x080000-0x09FFFF or
0x400000-0x403FFF. The firmware refuses all three; a BDM tool does not, and
0x400000-0x403FFF carries the reset configuration word and the censorship bits.

### 3.4 Then, in this order

1. Clear DTCs.
2. **Flash 0 is the first write of all** (issue **#26**): the *unmodified* dump
   re-saved through our tools. It changes nothing and proves everything the
   later flashes assume — that the write route works, that KESS's own checksum
   correction is a no-op on a file that already verifies, that the read-back
   equals what was written, and that no unknown signature exists. Its exit
   criterion is in #26; #28 repeats Flash 0 and Flash 1 on the *car's* ECU
   afterwards, with the BDM backup in hand.
3. **Flash 1 next**:
   `patches/ff_counter/test/procedure.md`. **Two** flash words, 224 bytes of
   blob, a counter at `PATCH_RAM+0x00` that must rise by **100/s** — not 10/s;
   C4 corrected every raster in the earlier documents by a factor of ten. Read
   the **five stock raster counters first**. A frozen counter because the other
   task set is live looks exactly like a failed flash and is not one. See the
   note below before flashing it.

> **Corrected 2026-09-22 (brief F1, checked by F2).** This chapter described
> Flash 1 as the single external word 0x12067C → `48 02 F9 85` with a 96-byte
> blob. Since F1 the patch **hooks the 10 ms raster of both task sets**, one
> word each, and records which one ran (`patches/ff_counter/README.md`):
>
> | Site | Where | Old → new | Task |
> |---|---|---|---|
> | **0x432940** | **on-chip flash** 0x404000-0x47FFFF | `4B C8 B0 A5` → `4B D1 D6 C1` (`bl 0x150000`) | `task_100ms_int` 0x4328E4, id 19, 10 ms, **task set A** |
> | 0x12067C | external flash | `4B FF E9 B1` → `48 02 F9 A5` (`bl 0x150020`) | `task_100ms` 0x1205A0, TCB 23, 10 ms, task set B |
>
> Three consequences for this chapter:
>
> 1. **Flash 1 now writes the on-chip array**, so it inherits §3.1's on-chip
>    caveat and §3.3's warning. `patch_apply.py` says so itself:
>
>    ```bash
>    ./.venv/bin/python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter -o work/ff_counter.bin
>    ```
>
>    ```
>    ff_counter: 5 patch range(s) (229 B), 6 descriptor range(s) (16 B), 0 unexpected
>    checksums: ALL OK (65 blocks); identification block unchanged
>    sha256: 3cd20443c068ed009b7d48b32210790eb320cb159489fc36cc1ef1ea67696498
>    WARNING: ff_counter: "ram_status": "static" - the RAM block at 0x007FFB00 is VERIFIED-STATIC only (re/findings/ram.md): no instruction references it, but the runtime snapshots of issue #23 are still pending. Do not flash this image.
>    WARNING: change at 0x432940+0x4 writes the MPC561 on-chip flash (0x404000-0x47FFFF). The block checksums are handled and the firmware's own OBD programming route whitelists the range (re/findings/flash_programming.md), but a KESSv2 write of it has not been demonstrated: read the image back and compare before trusting it.
>    ```
>
>    So **§3.3's check 1 — read back 0x404000-0x47FFFF and `bindiff` it — is
>    part of Flash 1, not only of Flash 0 and `ff_fuel`.** Per word:
>
>    ```bash
>    ./.venv/bin/python3 tools/blobdis.py work/ff_counter.bin --file-off 0x22E940 --addr 0x432940 --len 4
>    ./.venv/bin/python3 tools/blobdis.py work/ff_counter.bin --file-off 0x12067C --addr 0x12067C --len 4
>    ```
>
>    ```
>    00432940  4B D1 D6 C1  bl       0x150000
>    0012067C  48 02 F9 A5  bl       0x150020
>    ```
>
>    against the stock word at the same place, `4B C8 B0 A5  bl 0xbd9e4`
>    (`tools/blobdis.py data/passat_azx_ori.bin --file-off 0x22E940 --addr
>    0x432940 --len 4`). If the on-chip word reads the stock bytes back, KESS
>    skipped the array — and because set A is the live set
>    (`re/findings/scheduler.md` §11.8), **no build of this patch can then
>    produce a moving counter**: there is no external-flash alternative to move
>    the hook to (`flash_programming.md` §7.3).
> 2. **The read-out step gains a byte.** The RAM block is now `ff_ticks` (u32,
>    `PATCH_RAM+0x00`), `ff_alive` (u16, +0x04) and **`ff_src_seen` (u8,
>    +0x06): 1 = set A ran, 2 = set B, 3 = both** — with the counter then
>    rising at 200/s. It is re-derived from scratch at every cold start, so it
>    cannot survive a power cycle stale. `logging/sessions/flash1_counter.json`
>    logs all three.
> 3. **The bench-day decision table is `patches/ff_counter/test/procedure.md`
>    §4**, rows A-I: the stock raster counters (§3a), the on-chip read-back
>    (§3b) and the slope plus `ff_src_seen` (§3c) together pick one row, and
>    the row says what has been proved and which issue to write it into. Row D
>    is "KESS skipped the on-chip array"; row A upgrades §11.8 to
>    VERIFIED-DYNAMIC and settles the on-chip half of #32. §4.5 says when the
>    `make HOOKS=external` build (the pre-F1 patch, byte for byte) is worth
>    building: **only** if set B turns out to be live and the on-chip array
>    cannot be written.
3. Then `patches/ff_fuel`: `patches/ff_fuel/test/procedure.md` §§2-6, then
   `procedure_d2.md`, then `procedure_e1.md` / `_e2.md` / `_e5.md` for whichever
   feature you enable. **One feature at a time** (docs/01 §3 principle 5): every
   feature ships with its `ff_*_enable` byte at 0, and turning one on is a
   calibration change (chapter 1) to FFCAL001, not a rebuild.
4. Log the same scenario as the baseline (chapter 4) and compare (chapter 5).

> **Drift note, 2026-09-17 (E7):** `patches/ff_fuel/test/procedure.md` §1 still
> says "two of the three hook words are in the on-chip flash" and lists only
> 0x42247C and 0x432940. Since E1/E2/E5 the patch has **eight hooks, seven of
> them on-chip**; the current list is in `patches/ff_fuel/README.md`. Read the
> README's hook table, not procedure.md §1, when doing the read-back.
>
> **RESOLVED (2026-09-23, G2, checked against G1):** procedure.md §1 was fixed
> on 2026-09-17 (6113843). It now lists all seven on-chip words and matches the
> README's eight-hook table, so either one can be used for the read-back.

---

## 4. Logging

`logging/med9log.py` is the logger, `logging/med9kwp/` the protocol stack
(TP2.0 + KWP2000), `logging/ecu_sim.py` an emulated ECU that answers with the
**firmware's own KWP handlers**. Full detail: `logging/README.md`.

```bash
./.venv/bin/python3 logging/med9log.py --help
```

```
usage: med9log.py [-h] {log,dump,groups,probe} ...
…
Try every command with --sim first; it never touches hardware.
```

### 4.1 Rehearse on this Mac, always

```bash
./.venv/bin/python3 logging/ecu_sim.py --self-test
```

```
  10 89 session               5089                         ok
  21 F0 read                  61f0000005cbfc01             ok
  35 protected window         7f3531                       ok

RESULT: PASS
```

(Extract of 18 services.)

```bash
./.venv/bin/python3 logging/med9log.py probe --sim
```

```
bus: virtual:med9sim74220
channel setup      0.17 ms   we transmit on 0x740, module on 0x300
parameters                    BS=15 T1=100.0 ms T3=5.0 ms
10 89 session      1.59 ms
3E x20            0.29 ms   min 0.21  max 0.91
21 F0 x20         0.44 ms   -> 2283.1 samples/s, nmot_w = 826.8 rpm
```

On real hardware this is the exit criterion of issue #3, and the round trips
are milliseconds rather than microseconds.

`--sim` runs the simulator **in this process** on a python-can `virtual` bus.
That bus does not cross process boundaries, so starting `ecu_sim.py` in a second
terminal on `virtual:` will not work.

### 4.2 A session

A session file names what to read; `value = raw * scale + offset`, and a
variable may give `"symbol"` instead of `"addr"`, or `"patch_offset": N` so its
address follows `patch.json`'s `build.ram` and cannot go stale.

```bash
./.venv/bin/python3 logging/med9log.py log --sim --seconds 2 --session logging/sessions/wave_b_confirm.json -o work/sim.csv
```

```
session wave_b_confirm: 26 variables, 17 chunks on 0xf0, 56 bytes per sample
37 samples in 2.01 s (18.4 Hz total, 0.7 Hz per variable)
962 rows -> work/sim.csv
```

The four session files and the question each answers are tabulated in
`logging/README.md` §4. The logger merges adjacent variables into one DDLI
chunk and bridges holes of up to 8 bytes, because the firmware allows only 20
chunks on dynamic id 0xF0 and 3 on each of 0xF1-0xF9.

### 4.3 The CSV format

```
# session: wave_b_confirm, 2026-09-17T16:31:35+03:00
# ecu: 03H906032 / 1037382557
# dump_sha256: b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3
# transport: KWP2000 0x2C/0x21 over TP2.0
# source_file: logging/sessions/wave_b_confirm.json
# simulated: true
# simulated_by: logging/ecu_sim.py -- NOT a recording of an ECU
# sim_time_scale: 1
time_s,var,value,unit
0.0536,dwkrz_1,-96,degCA
```

Long form, one sample per row, `#` comments, `# key: value` metadata before the
header. Variables are sampled independently at whatever rate the transport
gives, which is exactly why the format is long rather than wide. `logcmp.py`
also reads the wide form that most VCDS exports produce.

> **`# simulated: true` is load-bearing.** Every CSV and every snapshot manifest
> produced by `ecu_sim.py` carries it, and a file that carries it is **never**
> bench evidence: not in a #23 comparison set, not behind a `VERIFIED-DYNAMIC`
> claim about the ECU, not in a regression baseline. It debugs the procedure,
> the session file and the tolerances — nothing else.

### 4.4 Measuring blocks

The four VCDS groups this project added, on top of the stock ones:

| Group | Feature | Ids | Brief |
|---|---|---|---|
| **111** | fuel scaling + diagnostics | 2196-2199 | D2 (#39) |
| **108** | ignition blend | 2192-2195 | E1 (#34) |
| **69** | start enrichment | 2188-2191 | E2 (#35) |
| **109** | rail-pressure adder + injection window | 2184-2187 | E5 (#36) |

```bash
./.venv/bin/python3 logging/med9log.py groups --sim --sim-patch patches/ff_fuel --eeprom work/eeprom.bin 111 108
```

```
group 111
  field 1: fmt 0x21 A=0x64 B=0x00 -> 0.000 % [community]
  field 2: fmt 0x21 A=0x64 B=0x64 -> 100.000 % [community]
  field 3: fmt 0x25 A=0x00 B=0x00 -> - [community]
  field 4: fmt 0x36 A=0x00 B=0x03 -> 3.000 count [community]
group 108
  field 1: fmt 0x21 A=0x64 B=0x00 -> 0.000 % [community]
  field 2: fmt 0x22 A=0x4b B=0x80 -> 0.000 degCA [crosschecked]
  field 3: fmt 0x22 A=0x4b B=0x80 -> 0.000 degCA [crosschecked]
  field 4: fmt 0x36 A=0x00 B=0x00 -> 0.000 count [community]
```

Group 111 field 4 = 3 is mode FAULT, which is correct here: no ethanol frame
has arrived. Group 108 reads zeros because the ignition blend is disabled and
its map is neutral — that *is* the shipped state.

Before taking a new group or id for a new feature, confirm it is still free:

```bash
./.venv/bin/python3 tools/measuring_vars.py data/passat_azx_ori.bin --free
```

### 4.5 The simulator rehearsal (E4)

`logging/ecu_sim.py --sim-patch` applies the patch to a temporary image and
drives **the patch's own hooks** on a simulated 10 ms raster, with a simulated
Pico on the same in-process bus (`logging/ethanol_frame_send.py`) and a
simulated SPI EEPROM behind the block manager (`emu/qspi_eeprom.py`).

```bash
./.venv/bin/python3 logging/med9log.py log --sim --sim-patch patches/ff_fuel --eeprom work/eeprom.bin --sim-node --node-e-pct 85 --session logging/sessions/ff_fuel.json --patch patches/ff_fuel/patch.json --sim-seconds 60 --time-scale 5 -o work/rehearsal.csv
```

```
session ff_fuel: 96 variables, 37 chunks on 0xf0, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 157 bytes per sample
58 samples in 12.20 s (4.8 Hz total, 0.0 Hz per variable)
5568 rows -> work/rehearsal.csv
```

All eleven numbered procedure steps, graded:

```bash
./.venv/bin/python3 logging/bench_rehearsal.py --fresh-eeprom
```

```
  e85          5c the slew limit holds (<= 2 %/s of ECU time)               ok    1.816 %/s
  fault3       5.3 HOLD is not a FAULT and F is frozen                      ok    faults=0.0 F=1.3291
  d2_restart   B3 a distinctive E60-E80 value came back (NOT the block id)  ok    e_key=85.0 e_persist=85.0

65/69 checks passed
every log carries `# simulated: true` and lives in logging/samples/
```

**65/69, not 69/69, as of integration/wave-E** — the four failures are a real
defect in the merged session file, not in the patch. See the drift note in
§5.4. Re-running this regenerates `logging/samples/ff_fuel_sim_*.csv`; those
files are checked in only so a reader can see what a passing run looks like.

**Wall-clock vs ECU time.** `--seconds` is wall-clock (what the CSV's `time_s`
holds); `--sim-seconds` is ECU seconds and divides by `--time-scale`. The
node's clock is scaled with the ECU's. Therefore **every rate criterion must be
evaluated against the ECU's own clock**, which is why the live raster counter
(100/s) is in the session file: `bench_rehearsal.py::ecu_slope` is the two-line
helper that does the division.

### 4.6 On real hardware

`logging/README.md` §§6-9 is the reference and should be read before buying
anything. The short version: a **candleLight/gs_usb-class adapter (CANable 2.0
or equivalent)** whose 120 Ω termination can be switched off, because the ECU is
itself the bus's central termination. Not an ELM327 clone — it cannot do TP2.0
channel setup at the frame level. The Raspberry Pi in `pi_can_setup/` with
`socketcand` is the fallback and needs no extra Python package at all.

Bench wiring (T94 pins **CAN-L 67, CAN-H 68**), the in-car OBD pinout, the
first-contact sequence and the table of "things that will go wrong and what
they mean" are all in `logging/README.md` §§7-8. The whole in-car half is
**HYPOTHESIS** until the first OBD session happens: the gateway is known to
filter CCP, and whether it forwards our TP2.0 channel setup is untested.

The node can be exercised on its own first, which is what you want before the
ECU is involved:

```bash
./.venv/bin/python3 logging/ethanol_frame_send.py --bus virtual:doc --e-pct 85 --seconds 1 -v
```

```
ethanol node on virtual:doc, id 0x0ec, 10 Hz
  E 85%  T  25 C  f 134 Hz  cnt   0  v1  OK

10 frames sent
```

Swap `--bus virtual:doc` for `--bus gs_usb:0` on the bench.

---

## 5. Log review

### 5.1 What the comparison can and cannot do

`tools/logcmp.py` resamples the candidate onto the baseline's timestamps inside
the overlapping window and summarises `candidate - baseline` per variable as
mean, mean-absolute, max-absolute (with the time it occurred) and RMS.

Two logs from two runs are never sample-aligned, so **the numbers are only as
good as the scenario**: run the same bench scenario both times, and expect
deviation wherever the signal moves fast — the resampling error is roughly the
signal's slope times the time skew. Steady-state sections are where the
comparison has power.

The *systematic* part of that skew — the power-up offset — is measurable and
the tool now removes it: `--align-on` (§5.4). What is left is jitter.

### 5.2 A pass and a fail

```bash
./.venv/bin/python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_ok.csv -t logging/samples/tolerance.json
```

```
PASS  tmot_w                   n=19     mean=-0.0476 mean|d|=0.1177 max|d|=0.2519@3.000s (limit 1)
PASS  wkr_w                    n=199    mean=+0.00356 mean|d|=0.09726 max|d|=0.2605@7.500s (limit 1)
7 common variable(s): 7 pass, 0 fail, 0 skipped; 0 baseline-only, 0 candidate-only
RESULT: OK
```

```bash
./.venv/bin/python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_bad.csv -t logging/samples/tolerance.json
```

```
7 common variable(s): 6 pass, 1 fail, 0 skipped; 0 baseline-only, 0 candidate-only
RESULT: FAILED
```

`candidate_bad.csv` is the same synthetic run with `ti_1_w` deliberately +8 %.
Exit status is 1 on any variable outside tolerance, so it gates a build.
Variables present in only one log are listed, not compared; `--strict` makes
that a failure too.

These files under `logging/samples/` — the three above, `tolerance.json` and
the `stock_run*.csv` pair of §5.5 — are **synthetic**, generated by
`logging/make_samples.py`, not read from an ECU. They test the tooling.

### 5.3 Tolerance files

JSON (recommended) or CSV with `var,max_abs,mean_abs,rel,interp`; `var` = `*`
sets the defaults. The keys are `max_abs` (largest absolute deviation),
`mean_abs` (the one that catches a small constant offset), `rel` (a fraction of
the largest absolute baseline value; the effective limit is the larger of the
two) and `interp` — `linear`, or **`hold` for enumerations, bit flags and
anything that steps rather than ramps**. Format in `logging/README.md` §2.

**Tolerances live with the thing they judge**, not with the tool:
`patches/ff_fuel/test/tolerance.json` encodes what *that patch* is allowed to
change. Its 97 rows include a convention worth copying — a variable that is
*expected* to differ carries NULL limits, so it is visibly considered rather
than silently falling to the default; and the raster counters are excluded
explicitly, with the reason, because they exist for the alignment step below.
`logcmp.py derive` writes both kinds of row for you from two runs (§5.5).

### 5.4 Aligning the two runs — `--align-on`

Two logs are two separate power-ups, and the tens of milliseconds between "the
ECU powered on" and "the tester finished the DDLI setup" are not the same twice.
On an rpm ramp that offset alone is worth a third of the `nmot_w` budget in
`patches/ff_fuel/test/tolerance.json` and says nothing about the software.

Both logs carry the live raster activation counter, which is the ECU's own
clock at 100/s, so the offset is **measurable rather than guessable**:

```
shift = (raster_cand[0] - raster_base[0]) / 100 - (t_cand[0] - t_base[0])
```

> **Closed 2026-09-22 (brief F2).** This section used to say "`tools/logcmp.py`
> cannot express this step" and told you to shift the candidate CSV by hand.
> The tool does it now:
>
> ```bash
> ./.venv/bin/python3 tools/logcmp.py base.csv cand.csv -t tolerance.json \
>         --align-on raster_setA_10ms_count:100
> ```
>
> `VAR:RATE` is the counter and its counts per second (100 if left out). The
> shift is printed in the report header (`ALIGN …  candidate shifted by
> +250.0 ms at 100/s`) and is in the JSON report as `summary.shift_s`;
> `--align-shift SECONDS` sets it by hand. **A counter missing from either log
> is an error, exit 2** — silently comparing two unaligned power-ups is the
> failure the option exists to stop. Use `raster_setA_10ms_count` or
> `raster_setB_10ms_count` according to what `ff_src_seen` says is live
> (`patches/ff_counter/test/procedure.md` §4). `logging/bench_rehearsal.py`
> calls the same function, so the bench and the rehearsal cannot drift apart.

What it is worth, on the two synthetic runs of `logging/samples/` that differ
only by a 0.25 s power-up offset (§5.5's recipe, command 3): **five of seven
variables fail unaligned** — `nmot_w` by `max|d| 404.7` against a limit of
16.16 — and all of them pass aligned, `nmot_w` at `max|d| 10.77`, which is the
sensor noise the pair was built with.

And on the E4 rehearsal: running `logcmp` **without** the alignment on two runs
that differ only by the power-up offset fails three variables (`dwkrz_1`,
`rk_fuel_mass`, `ti_sum`); **with** the alignment, only `ti_sum` failed — and
that one was a defect in the session file, not a real deviation:

> **Drift, found by E7 on 2026-09-17, integration/wave-E.**
> `logging/sessions/ff_fuel.json` declares three variable **names twice**:
> `dwi_inj_angle` and `prist_w` harmlessly (both copies identical), but
> **`ti_sum` with two different definitions** — once as 4 bytes × 0.001 ms and
> once as 2 bytes × 1 us, both at 0x8030C4. The log therefore carries two
> contradictory series under one name (`n=70` where its neighbours have `n=35`,
> `max|d| = 1.546e+05` ≈ 157286 − 2400), so `logcmp` can never pass on it. The
> duplicates entered with E5's commit `e257b39`; the file had 77 variables and
> no duplicates before it, 96 and three after. That accounts for three of the
> four `bench_rehearsal.py` failures. The file belongs to the patch briefs, so
> E7 did not edit it.

**Settled since (checked 2026-09-22, F2):** `logging/sessions/ff_fuel.json`
holds **93 variables and no duplicate name**, and the rehearsal is 69/69.

### 5.5 Two stock runs → measured tolerances, and the E0 recipe

Every `tolerance.json` in this repo says the same thing about itself: its
limits are a **starting point from the signals' idle behaviour, not from two
recorded runs**. C1 made that a rule for Flash 1 (#27) and docs/05's "E0
equivalence" repeats it — record the scenario **twice on the stock image**, and
tighten every line to what the ECU actually repeats. Until 2026-09-22 no
command did that step. `derive` does:

```bash
# 1. two stock runs of the same scenario -> limits that are measured
./.venv/bin/python3 tools/logcmp.py derive \
        logging/samples/stock_run1.csv logging/samples/stock_run2.csv \
        --align-on raster_setA_10ms_count:100 --exclude 'raster_*' \
        -o work/tolerance_measured.json
```

```
ALIGN raster_setA_10ms_count   candidate shifted by +250.0 ms at 100/s
LIMIT B_stend                  max_abs=0          mean_abs=0
LIMIT lamsoni_w                max_abs=0.0108     mean_abs=0.004071
LIMIT nmot_w                   max_abs=16.16      mean_abs=5.575
LIMIT rl_w                     max_abs=1.134      mean_abs=0.4141
LIMIT ti_1_w                   max_abs=0.02295    mean_abs=0.008332
LIMIT tmot_w                   max_abs=0.501      mean_abs=0.2096
LIMIT wkr_w                    max_abs=0.4287     mean_abs=0.1529
EXCL  raster_setA_10ms_count   not compared
7 variable(s) measured over 0.25-9.95 s x1.5, 1 excluded -> work/tolerance_measured.json
```

Each limit is that pair's own spread times `--factor` (1.5 by default):
`max_abs` = max|d| × factor, `mean_abs` = mean|d| × factor, and the `_note` on
every row keeps the raw numbers. `--exclude` takes names or globs and writes
them as explicit `"max_abs": null` **"not compared"** rows — the free-running
counters belong there, because the alignment is computed *from* them and
comparing them afterwards is circular. The file keeps the format `logcmp`
already reads (§5.3), plus a `_derived` block naming the two runs, the factor,
the alignment and the common time range. Its `default` is deliberately
`0.0 / 0.0`: a variable the pair never saw is not judged by a guess.

```bash
# 2. the comparison itself: candidate against baseline, on the ECU's clock
./.venv/bin/python3 tools/logcmp.py \
        logging/samples/stock_run1.csv logging/samples/stock_run2.csv \
        -t work/tolerance_measured.json \
        --align-on raster_setA_10ms_count:100 --uncovered report
```

```
ALIGN raster_setA_10ms_count   candidate shifted by +250.0 ms at 100/s
PASS  B_stend                  n=19     mean=+0 mean|d|=0 max|d|=0@0.500s (limit 0)
PASS  lamsoni_w                n=195    mean=-1.744e-05 mean|d|=0.002714 max|d|=0.0072@1.250s (limit 0.0108)
PASS  nmot_w                   n=195    mean=-0.3212 mean|d|=3.717 max|d|=10.77@9.600s (limit 16.16)
PASS  raster_setA_10ms_count   n=195    mean=+0 mean|d|=0 max|d|=0@0.250s
PASS  rl_w                     n=195    mean=-0.009374 mean|d|=0.2761 max|d|=0.7563@9.150s (limit 1.134)
PASS  ti_1_w                   n=195    mean=-0.0007769 mean|d|=0.005554 max|d|=0.0153@0.650s (limit 0.02295)
PASS  tmot_w                   n=19     mean=+0.04667 mean|d|=0.1397 max|d|=0.334@1.500s (limit 0.501)
PASS  wkr_w                    n=195    mean=+0.002795 mean|d|=0.102 max|d|=0.2858@9.150s (limit 0.4287)
8 common variable(s): 8 pass, 0 fail, 0 skipped; 0 baseline-only, 0 candidate-only; 0 uncovered
RESULT: OK
```

`--uncovered report` says what to do with the variables the tolerance file does
not name: leave them out of the comparison and **list** them, instead of
judging them against the file's `default` limit (`fail`, still the default) or
dropping them silently (`ignore`). Here it prints `0 uncovered`, because a
derived file names every variable of the pair — that is the point of it. On a
hand-written file it is the mode to use: a session file reads plain RAM, so a
*stock* log carries the patch's `ff_*` variables reading 0, and they must not
fall to a default limit of 1.0. `logging/bench_rehearsal.py` runs its E0
comparison exactly this way.

```bash
# 3. what the alignment is worth: the same comparison without it
./.venv/bin/python3 tools/logcmp.py \
        logging/samples/stock_run1.csv logging/samples/stock_run2.csv \
        -t work/tolerance_measured.json --uncovered report -q
```

```
FAIL  lamsoni_w                n=200    mean=+1.9e-05 mean|d|=0.007951 max|d|=0.0351@4.150s (limit 0.0108)
      max|d| 0.0351 > 0.0108 at t=4.150s
      mean|d| 0.007951 > 0.004071
FAIL  nmot_w                   n=200    mean=-0.9022 mean|d|=88.71 max|d|=404.7@4.100s (limit 16.16)
      max|d| 404.669 > 16.16 at t=4.100s
      mean|d| 88.7066 > 5.575
FAIL  rl_w                     n=200    mean=-0.003474 mean|d|=2.457 max|d|=11.91@7.500s (limit 1.134)
      max|d| 11.9125 > 1.134 at t=7.500s
      mean|d| 2.45743 > 0.4141
FAIL  ti_1_w                   n=200    mean=-0.000707 mean|d|=0.0829 max|d|=0.4087@7.700s (limit 0.02295)
      max|d| 0.4087 > 0.02295 at t=7.700s
      mean|d| 0.0829 > 0.008332
FAIL  wkr_w                    n=200    mean=+0.001677 mean|d|=0.2242 max|d|=1.039@4.350s (limit 0.4287)
      max|d| 1.0395 > 0.4287 at t=4.350s
      mean|d| 0.224216 > 0.1529
8 common variable(s): 3 pass, 5 fail, 0 skipped; 0 baseline-only, 0 candidate-only; 0 uncovered
RESULT: FAILED
```

Same two runs, same software, same file: **five variables "fail" on 250 ms of
power-up offset.** That is what an unaligned comparison reports, and it is why
command 2 is not optional.

`stock_run1.csv` and `stock_run2.csv` are **synthetic** (`make_samples.py`, ten
seconds of idle with a load step, the second run's logger started 0.25 s of ECU
time later); they demonstrate the commands, they are not a measurement. On the
bench the same three commands read:

```bash
./.venv/bin/python3 tools/logcmp.py derive work/stock_run1.csv work/stock_run2.csv \
        --align-on raster_setA_10ms_count:100 \
        --exclude 'raster_*' ff_ticks 'ff_*' -o patches/ff_fuel/test/tolerance_measured.json
./.venv/bin/python3 tools/logcmp.py work/stock_run1.csv work/ff_fuel_e0.csv \
        -t patches/ff_fuel/test/tolerance.json --align-on raster_setA_10ms_count:100 \
        --uncovered report --json work/logcmp.json
```

The derived file is **new evidence, not a replacement**: keep the hand-written
`tolerance.json` — it carries the reasoning, the units and the null rows — and
retighten its numbers from the derived ones, row by row, as C1 asked.

### 5.6 The two standing procedures

**E0 equivalence** (docs/05 §6, docs/01 §3 principle 3): with the ethanol input
at E0 — or with every feature disabled — the logged `lambda`, `ti`, `fra` and
`zw` must be identical to the stock baseline within noise over the same
scenario. Static proof is `tools/bindiff`; emulator proof is the bit-identity
tests of §2.5; **log proof is this chapter.** Record the stock baseline first.

**Fault matrix** (`patches/ff_fuel/test/procedure.md` §5, six rows). The rule it
enforces is the #37 asymmetry, which now applies to every feature:

> On FAULT the **fuel** factor is **held and then decayed**; **ignition and rail
> terms go to zero** on the activation the mode leaves OK/HOLD. No hold and no
> ramp for anything that adds advance or pressure.

Each row of the matrix injects one fault (sensor unplugged, implausible value,
stale counter, not-ready status, bad frame, nothing ever received) and checks
`ff_mode`, the factor and the fault counters.
`logging/ethanol_frame_send.py --fault-after S`, `--stall`, `--implausible`,
`--not-ready` and `--e-ramp A:B:S` are the knobs, and
`logging/bench_rehearsal.py` runs all six against the simulator so the procedure
is debugged before the bench.

One more thing the procedure insists on: **settle before you trip.** Send good
frames until `ff_e_filt` is within 1 % of target and `ff_f_q10` has stopped
moving, *then* inject the fault — a fault injected during the ramp holds
whatever the filter happened to reach and the row proves nothing.

---

## 6. Roll back and recovery

### 6.1 The roll-back file

**`data/passat_azx_ori.bin`**, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`. It is the
KESSv2 read of the car's own ECU, 0x27C000 bytes, and writing it back is the
roll-back (docs/06 §6). Confirm the hash before writing it:

```bash
shasum -a 256 data/passat_azx_ori.bin
```

```
b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3  data/passat_azx_ori.bin
```

docs/04 §6 item 8: **roll back if anything is unexplained. Do not "fix forward"
on the car.**

### 6.2 What it does not cover

`data/passat_azx_ori.bin` is an **OBD** read, and it is missing two things:

* **0x400000-0x403FFF**, the first 16 KB of on-chip flash, which KESSv2 does not
  read. It carries the reset configuration word, the shadow row and the live
  exception vectors. Nothing in this project can restore it; only a BDM read can.
* **The EEPROM** (immobiliser data, adaptation, the programming record in block
  10). OBD writes do not touch it (docs/03 §7), so it survives an OBD flash —
  but it is not in the backup either.

Hence docs/04 §6 item 4 and E6's warning: **take the K-TAG/BDM read of external
flash, on-chip flash and EEPROM before the first write**, record the SHA-256s in
`data/backup_bdm/MANIFEST`, and keep it. That file does not exist yet.

### 6.3 What a failed flash looks like

From `re/findings/flash_programming.md` §5 (VERIFIED-STATIC from the dump, not
yet seen on hardware):

* **A half-finished write is not a brick.** If the calibration `5A5A` marker at
  file 0x1E2500 is wrong, `app_init` raises a "no valid dataset" flag, sets the
  loader magic in RAM and hangs into a watchdog reset — the ECU reboots into the
  RAM-resident bootstrap loader and stays there, re-flashable.
* **Nothing checks flash content at boot.** The 65 block sums are never
  recomputed at run time and the published CRC-32 is compared with nothing. A
  wrong checksum will therefore *not* stop the engine — which is an argument for
  being more careful, not less: `tools/checksum.py` and `tools/bindiff.py` are
  the only things standing between a typo and a car that runs on a corrupt map.
* The KWP programming route can never be made to write 0x000000-0x01FFFF,
  0x080000-0x09FFFF or 0x400000-0x403FFF — the firmware refuses all three. A
  **BDM tool does not refuse**, which is why BDM is both the recovery path and
  the only way to destroy the part.

### 6.4 What never to flash

| Never | Why |
|---|---|
| A file that does not verify `ALL OK (65 blocks)` | docs/04 §6 item 1 |
| A file whose `bindiff -p` shows any **unexpected** byte | docs/06 §5 item 2 |
| A file not derived from **this ECU's own read** | docs/04 §6 item 3; `patch_apply.py` checks `base_sha256` and cannot be overridden |
| Anything with a changed identification block 0x1CEE20-0x1CEE6F | docs/04 §6 item 3; no flag unlocks it |
| A patch whose `"ram_status"` is not `"verified"` | both current patches — issue #23's runtime snapshots are outstanding |
| Any image, to the **car**, that has not run on the bench spare | docs/01 §3 principle 2 |
| Anything that disables the ROM check, immobiliser pairing or component protection | docs/04 §6, closing line. Find the actual cause instead |

> **Ruling 2026-09-24 (Carlo, at wave-H planning).** The `ram_status` row is the
> **strict rule**: no write of any kind — **Flash 0 included** — before issue #23's
> runtime RAM snapshots are in (`docs/08` step 4 comes before step 5). Flash 0
> carries no patch RAM, but the rule is "snapshots first", not "patches only".

---

## 7. The order of the whole thing

```
0.3  checksum verify + unittest discover        every session
 |
1.   calibration change:  cp -> TunerPro -> checksum fix -> bindiff (read it)
 |        or
2.   code change:  make check -> dump -> gen -> apply -> emulator proofs -> bindiff -p
 |
4.1  rehearse against the simulator             --sim, --sim-patch, bench_rehearsal.py
 |
4.x  record the STOCK BASELINE log              before the flash; there is no second chance
 |
3.   flash (human, KESSv2)  ->  read back  ->  bindiff -p  ->  the three E6 checks
 |
4.x  log the same scenario again
 |
5.   logcmp --align-on the raster counter, against the baseline
     (and derive the tolerances from the two stock runs first)
 |
6.   if anything is unexplained: write data/passat_azx_ori.bin back
```
