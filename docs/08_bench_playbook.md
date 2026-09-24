# Bench-day playbook

Issues **#3, #20, #23, #26, #27, #44** (desk half), with pointers into #28 and
#32-#39. Written 2026-09-24 by brief **G6** against `integration/wave-G`
(fc0cb06). Where this file lives: `docs/08`, not `re/findings/`, because it is
something you *follow* (like `docs/07`), not a finding. It records no new fact
about the ECU.

This is the **order** in which the human runs the procedures that already
exist, from "ECU on the desk, nothing connected" to "Flash 1 verified". It does
not restate any of them. Each step says which file and section it drives, what
passes, what each kind of failure means, which issue it moves, and why it comes
where it does. When this file and a procedure disagree, the procedure wins.
Report the disagreement as a drift note.

**Nothing below has been run on an ECU.** Commands come in three kinds:

| Mark | Meaning |
|---|---|
| **[Mac]** | Runs on this Mac with no hardware attached. Every one was run on 2026-09-24 on `integration/wave-G`, and one line of its real output is quoted. Its output is simulated (`# simulated: true`) and is **never** bench evidence (`docs/07` §4.3). |
| **[bench-only]** | Needs the ECU on the bench harness and the CAN adapter. Copied from the procedure it belongs to, not run. |
| **[car-only]** | Needs a running engine. A bench ECU has no crank or cam signal and stays in "engine not running" (`re/findings/hardware_prep.md` §3.5). The logging is read-only, over the OBD socket (`logging/README.md` §7, "In the car", which is HYPOTHESIS until tried) or on the powertrain pair directly (§9 item 3). **No flashing.** |

**Flash 0 means the unmodified dump** re-saved through our tools. That is the
definition in `docs/01` §4 Phase 3, `docs/07` §3.4 item 2 and issue #26.
**Flash 1** is `patches/ff_counter` (#27). Two older texts use "Flash 0" for the
first `patches/ff_fuel` write: the intro of `docs/07` §3, and item 6 of
`logging/sessions/flash_crc.json`'s comment. Read those as "ff_fuel's first
flash".

## 0. Which ECU, and the stop list

### 0.1 Three units, three jobs

From `docs/01` §3 principle 2, §5 M1/M1b/M1c and
`re/findings/hardware_prep.md` §1.4b:

| Unit | May do | Must not do |
|---|---|---|
| **Bench mule**: any VR6 `03H906032`, whatever its software | steps 1-2 (harness, power, CAN, TP2.0, Pico frames) | produce evidence for steps 3-4. Every session file and RAM range is for SW `1037382557` (the `"ecu"` and `"dump_sha256"` fields of each `logging/sessions/*.json`) |
| **Software-matching spare**: `0261S02226` / `1037382557` (M1c) | everything in steps 1-6. Flash 0 and Flash 1 go here first | be skipped. Nothing goes to the car that has not run here (principle 2) |
| **The car's ECU** | read-only logging of the [car-only] reads in steps 3-4; later #28 | be flashed before M1 (BDM backup plus a write-back route) and before the same file has run on the spare |

Step 2d has a check that tells you which software a unit really runs: the stock
flash CRC. Its value must be **0x5562139F**. A different value on a unit that
should be stock means its flash is not this dump (`flash_crc.json`, item 5).

### 0.2 Stop here if

These restate the existing gates of `docs/01` §3 (principles 2 and 5),
`docs/07` §6.4 and `docs/04` §6, in the order you meet them on the day. None of
them has an override.

| # | Stop if … | Then | Source |
|---|---|---|---|
| S1 | `checksum.py verify -q data/passat_azx_ori.bin` is not `ALL OK (65 blocks)`, or its SHA-256 is not `b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3` | nothing downstream means anything | `docs/07` §0.3, §6.1 |
| S2 | the unit's stock flash CRC is not 0x5562139F | that unit cannot supply evidence for steps 3-4, or be the flash-rehearsal spare | `flash_crc.json` item 5 |
| S3 | **`"ram_status"` is still `"static"`** in the patch you are about to write | **do not flash at all.** Finish step 4 first. This playbook applies it to Flash 0 too — **confirmed as the strict rule by ruling (Carlo, 2026-09-24)**; `docs/07` §6.4 carries the matching dated note | `docs/07` §2.3, §6.4; `patch_apply.py`'s warning; the G6 brief |
| S4 | **the target ECU has no verified BDM backup** (external flash, on-chip flash including 0x400000-0x403FFF, EEPROM) with SHA-256s in `data/backup_bdm/MANIFEST` | **do not flash that ECU.** For the car this is absolute (#28 depends on #2) | `docs/07` §3.1 row 4, §6.2; `docs/01` M1 |
| S5 | the file does not verify, has any `unexpected` byte in `bindiff -p`, was not built from this ECU's own read, or has a changed ident block 0x1CEE20 | do not flash it | `docs/07` §6.4 rows 1-4 |
| S6 | a tool offers to address 0x000000-0x01FFFF, 0x080000-0x09FFFF or 0x400000-0x403FFF | refuse. A BDM tool does not refuse by itself | `docs/07` §3.3, last paragraph |
| S7 | KESS reports that it *corrected* a checksum on a file that already verifies | stop and find out what it changed | `docs/07` §3.2 |
| S8 | **the on-chip read-back does not match**: some on-chip words written and others not, or bytes written but no stub runs | **do not trust the image.** Roll back (`docs/07` §6.1) | `docs/07` §3.3 table, row 3; `procedure.md` §4 row E |
| S9 | Flash 1 lands on row **B** or **E** of `patches/ff_counter/test/procedure.md` §4 | no further flash until it is explained | same, §4 |
| S10 | an image has not run on the software-matching bench spare | it does not go to the car | `docs/01` §3 principle 2; `docs/07` §6.4 row 6 |
| S11 | more than one `ff_*_enable` would change in one flash | one feature at a time | `docs/01` §3 principle 5; `docs/07` §3.4 item 3 |
| S12 | **block 8 payload +19..+28 has not been read on this ECU** (step 3f) before the first `ff_fuel` flash. `ff_persist_enable` **ships as 1 by ruling** (Carlo, 2026-09-24; `patches/ff_fuel/README.md` FFCAL001 row +1B) and the E% store sits at **+19** since brief G7 (merged 2026-09-24) | read block 8 first and record +19..+28; `eeprom.md` §5 predicts 0x00 there. If the bytes are anything else, do **not** flash `ff_fuel` with the store enabled — build with `ff_persist_enable=0` (`procedure_d2.md` §B4) and report | `docs/05` §3.8 (notes of 2026-09-23/24 and the ruling of 2026-09-24); `eeprom.md` §5 (G7's exclusion set) |
| S13 | anything is unexplained after a write | roll back with `data/passat_azx_ori.bin`. Never "fix forward" on the car | `docs/07` §6.1; `docs/04` §6 item 8 |
| S14 | a fix seems to need the ROM check, immobiliser pairing or component protection turned off | find the actual cause instead | `docs/04` §6, closing line |

Two things look like a brick and are not. Recognise them; do not stop the
project over them:

* an ECU that reboots into the loader and stays there: the calibration marker
  `5A 5A` at file 0x1E2500 is wrong (`docs/07` §3.3 check 2, §6.3);
* frozen set-B raster counters: set A is the live set (step 3c).

---

## Step 1 — Bench bring-up (#3)

**Drives:** `re/findings/hardware_prep.md` §3.9 items 1-5 (the order), §3.2
(the T94 pinout, COMMUNITY: ring it out before power), §3.3 (caveats), §3.6
(current-limited supply), and `logging/README.md` §7 "On the bench" (CAN pair
on T94 67/68, adapter termination **off**, KL15 present).

**Unit:** the mule is enough (§0.1).

**Run [bench-only]:** photograph and label the unit (item 1). Ohm out the pins
with no power (item 2). Power up in stages, ground, then KL30 at 1 A, then KL15,
noting the current each time (items 3-4). Sniff at 500 kbit/s (item 5; SavvyCAN,
or `python3 -m can.viewer -i gs_usb -c 0 -b 500000` from `logging/README.md` §8
step 1).

**Pass:** pins 1, 2, 4 and 61 read about 0 Ω to the case. 3, 5, 6, 87 and 92 are
not shorted to ground. **CAN-H 68 to CAN-L 67 reads about 66 Ω**, which confirms
both the pin numbering and the central termination (§3.4). With KL15 on, the ECU
sends cyclic frames every 10-25 ms (`re/findings/can.md` §4).

**Fail means:**

| Symptom | Meaning |
|---|---|
| no ≈66 Ω between 67 and 68 | wrong pins, or a unit that is not the central terminator. Re-ring before any power (§3.9 item 2) |
| a supply pin shorted to ground, or current far above the §3.6 expectation | stop. Power off and find it before going on |
| no CAN traffic with KL15 on | wiring, bitrate or KL15 (`logging/README.md` §8 step 1) |

**Record:** the results go into `re/findings/bench.md`, the file §3.9's heading
names. It does not exist yet. The measured current replaces the §3.6 HYPOTHESIS
paragraph.

**Why first:** nothing that follows is interpretable on a harness that has not
been rung out. `docs/01` §4 Phase 0 has the harness before any flash work.

## Step 2 — First contact over the logger (#3 exit, #20)

**Drives:** `logging/README.md` §8 steps 0-2, and its table "Things that will
go wrong, and what they mean", which is the troubleshooting reference for
steps 2-6. `logging/README.md` §6 covers the adapter. `hardware_prep.md` §3.9
item 6 is the same test.

### 2a. On the Mac, with nothing connected [Mac]

```bash
./.venv/bin/python3 logging/ecu_sim.py --self-test
./.venv/bin/python3 logging/med9log.py probe --sim
```

```
RESULT: PASS
channel setup      0.19 ms   we transmit on 0x740, module on 0x300
```

If either fails, the software is broken, not the bench. Fix it before
connecting anything.

### 2b. The real answer [bench-only]

```bash
python3 logging/med9log.py probe --bus gs_usb:0
```

**Pass:** `we transmit on 0x740, module on 0x300`, with round trips of a few
milliseconds. **This is the exit criterion of issue #3** (TesterPresent over
TP2.0). **Fail:** use the §8 table. No answer on 0x201 means wiring, KL15,
bitrate or swapped H/L. `0xD6`-`0xD8` means another tester holds the channel.
`7F 3E 12` means the tool sent `3E 01`, and this ECU wants a bare `3E`.

### 2c. The Pico node on the same wire (#3, second half) [bench-only]

`#3`'s exit criterion also asks for Pico frames visible in SavvyCAN.
`hardware_prep.md` §3.9 item 7 puts this after TesterPresent. Rehearse it
first [Mac], then swap the bus (`docs/07` §4.6):

```bash
./.venv/bin/python3 logging/ethanol_frame_send.py --bus virtual:doc --e-pct 85 --seconds 1 -v
```

```
  E 85%  T  25 C  f 134 Hz  cnt   0  v1  OK
```

On the bench the same command runs with `--bus gs_usb:0` (`logging/README.md`
§9). **Pass:** id 0x0EC at 10 Hz in SavvyCAN. With 2b, that **closes #3**.

### 2d. Which software is this unit, and what is T_bg — the flash-CRC read (#20)

**Drives:** `logging/sessions/flash_crc.json` (its comment, items 1-5), and
`re/findings/boot.md` §6.8(c) and (e).

Rehearse [Mac]. `--sim-flash-crc` runs the firmware's own CRC task. In 45 s
the cursor walks from 0x21004 to between 0x9B69C and 0xC2F6C (two runs; it
depends on host load). The publish is still minutes away:

```bash
./.venv/bin/python3 logging/med9log.py log --sim --sim-flash-crc --time-scale 8 --seconds 45 --session logging/sessions/flash_crc.json -o work/sim_flash_crc.csv
```

```
session flash_crc: 13 variables, 6 chunks on 0xf0, 32 bytes per sample
```

On the unit [bench-only], **power-cycle first**, because the task runs once per
power cycle (item 1). Then, KL15 on and the engine off:

```bash
python3 logging/med9log.py log --session logging/sessions/flash_crc.json \
    --bus gs_usb:0 --seconds 300 -o logs/2026-xx-xx_flash_crc.csv
```

This read answers two questions:

1. **The software identity.** `flash_crc_pub_hi`/`_lo` must become
   `0x5562`/`0x139F` (item 5). If they do not, gate **S2** applies. The unit is
   a mule, and steps 3-6 need another one. Publish can take up to 24.7 min after
   `os_init` (boot.md §6.8(c), table), so a 300 s log that has not published
   yet is not a failure. Extend it.
2. **T_bg**, the background-loop period that G3 bounded to **0.51-300.75 ms**
   (boot.md §6.8(c)). T_bg = 500 bytes ÷ the slope of `flash_crc_cursor`
   (0x7FB700) in bytes/s (§6.8(e) item 1). The #20 integration note of
   2026-09-24 asks for the number twice, **at key-on engine off** (here) and
   **at idle** ([car-only], step 3). The result goes into `flash_crc.json` and
   #20.

For the idle read: the cursor only moves until the publish, and on a warm
ECU nothing moves (item 1). §6.8(e) item 2's loop counter **0x7FD70C** keeps
counting after that, but no session file on this head logs it (open question 2).

**Why here:** S2 has to be known before any step-3 read can count as evidence
about SW `1037382557`. The same 0x5562139F is then the expected result of the
Flash 0 read-back in step 5, so this read is also its "before".

---

## Step 3 — The wave-B confirmations, the stock baselines, block 8 (#44)

**Drives:** `logging/README.md` §8 steps 3-4, `logging/sessions/wave_b_confirm.json`
(its comment has the scenario and the expected raster slopes), and the #44
checklist. **Unit:** the software-matching spare or the car (S2). Most of #44
needs a running engine, so most of this step is [car-only] read-only logging.

### 3a. Rehearse [Mac]

```bash
./.venv/bin/python3 logging/med9log.py groups --sim 1 2 3 20
./.venv/bin/python3 logging/med9log.py log --sim --seconds 2 --session logging/sessions/wave_b_confirm.json -o work/sim.csv
```

```
  field 2: fmt 0x05 A=0x0a B=0x77 -> 19.000 degC [crosschecked]
session wave_b_confirm: 26 variables, 17 chunks on 0xf0, 56 bytes per sample
```

### 3b. The two reads

```bash
python3 logging/med9log.py groups --bus gs_usb:0 1          # then 2, 3, 20-24, 231 (read as 104, kwp.md 12.3)
python3 logging/med9log.py log --bus gs_usb:0 --seconds 70 \
        --session logging/sessions/wave_b_confirm.json -o logs/2026-xx-xx_wave_b.csv
```

Run VCDS on the same groups at the same time; one tester at a time per channel
(§8 table, `0xD6`-`0xD8`). Scenario: 0-30 s idle, 30-40 s one load step,
40-70 s idle, engine warm (`wave_b_confirm.json`).

### 3c. Which #44 checkbox each read closes

| #44 row | Read | Where | Pass | Closes |
|---|---|---|---|---|
| group 001.2 vs `tmot` 0x8021EF / `tmot_w` 0x802228 | `groups 1` + `tmot`, `tmot_w` in the log | bench (KL15) or car | the same °C as VCDS, within a count (§8 step 3) | already ticked (emulator, `measuring_vars.md` §7.1); a sanity check of the logger |
| group 002 vs `ti` 0x8030C4, 1 µs/LSB | `groups 2` + `ti_sum` | **car** | agree at 1 µs/LSB (`injection.md` §8) | **ticks the row** |
| rail, group 231 vs `prist` 0x8031DA / `prsoll` 0x8031F4 | `groups 231` + `prist_w`, `prsoll_w` | **car** | 0.005 bar/LSB | already ticked (emulator, §7.3); sanity check |
| group 003.4 vs 0x7FEF87, s8 at 0.75 °CA/LSB | `groups 3` + `zwist_display_b1` | **car** | agree at 0.75 °CA/LSB (`ignition.md`) | **ticks the row** |
| groups 020-024 vs `dwkrz` 0x7FCE57-0x7FCE5C | `groups 20`…`24` + `dwkrz_1`…`_6` | **car** | the cylinder order 1,5,3,6,2,4 (COMMUNITY) holds | **ticks the row** |
| `dwi` 0x803088 at WOT | `dwi_inj_angle` in a WOT pull (not the 70 s scenario) | **car, road** | a number for the +73 % window margin (`rail.md` §7) | **ticks the row** |
| period of 0x45CAC4 | — | — | — | already ticked (C4); nothing to read |
| **which task set is live** | the five raster counters in the log | bench (KL15) first, then **car** at idle | set A 0x7FD754 +100/s, 0x7FD75C +1000/s; the three set-B cells frozen; all ~0.25 % low (`wave_b_confirm.json`) | the row says "with the engine running", so the bench read is only the first look. The **car** read ticks it, and so does Flash 1 row A (step 6), whichever comes first |
| `nmot_w` 0x7FEE74 / `rl` 0x7FED38 scaling | `groups 1` + `nmot_w`, `rl_for_fuel` | **car** | 0.25 rpm/LSB, 100 %/4096 | **ticks the row** |

Frozen set-B counters are the prediction (`scheduler.md` §11.8), not a fault.
Set-B counters moving is procedure.md §4 row B's finding, made early. Write it
into #44 and #34 loudly, and read §11.8 again before any flash (S9 applies in
spirit).

**Also read in the same session (brief G4, #41 integration note of
2026-09-23, `re/findings/calibration_names.md` §11). These are not #44 rows.**
Record the results as dated notes in `calibration_names.md` §11:

| Id | Cell | How | Expect | Source |
|---|---|---|---|---|
| 43 | 0x80304A `lamsbg_w` | in **no** stock group (`re/findings/measuring_groups.txt`) and in no session file, so it needs a DDLI entry that does not exist yet (open question 3) | 1.000 (G4's expectation; 4096 = 1.0) | §11.8; `re/symbols.csv` row 0x80304A |
| 85 | 0x8021CC, intake air `tans` | `groups 4` (field 4), against VCDS 004.4 | same °C | §11.1 |
| 130 | 0x80223B `gangi` | `groups 51` (field 3), or `gangi` in `logging/sessions/tuning_checklist.json`, **while shifting** | 0 … 6, 7 = reverse | §11.7 |
| 171 | 0x80315C `rkte_w` | `groups 73` (field 4) **during canister purge** | log it during purge; the subtraction assumes gasoline vapour, so it is a tuning-checklist item on E85 | §11.5 |

(`./.venv/bin/python3 logging/med9log.py groups --sim 4 51 73` [Mac] prints
all three groups. Their simulated values mean nothing.)

**T_bg at idle** (step 2d, second half) belongs in this car session.

### 3d. Two stock baselines, before any flash

`docs/07` §3.2 and §7: *there is no second chance to record a baseline on a
flashed ECU.* Record the scenario **twice** on the stock image of the unit that
will be flashed:

* `logging/sessions/flash1_counter.json`, for Flash 1
  (`patches/ff_counter/test/procedure.md` §3 and §5; the two runs feed
  `logcmp.py derive`, `docs/07` §5.5);
* `patches/ff_fuel/test/procedure.md` §4's scenario, for `ff_fuel`'s E0
  equivalence later (#32).

The scenario has an idle and a load step. On a bench spare that cannot run an
engine, see open question 4 before you rely on these baselines.

### 3e. EEPROM block 10, before (#26)

`docs/07` §3.3 check 3: read EEP_CONF block 10 before any write and again
after. A changed block 10 is independent evidence that the ECU's own
programming route ran.

### 3f. EEPROM block 8, before (#38; S12)

`patches/ff_fuel/test/procedure_d2.md` §B1: on the **stock** image, log the
`eep_blk8_*` mirror with `logging/sessions/ff_fuel.json`. Rehearse [Mac]:

```bash
./.venv/bin/python3 logging/med9log.py log --sim --eeprom work/eeprom.bin --session logging/sessions/ff_fuel.json --seconds 3 -o work/sim_blk8.csv
```

```
session ff_fuel: 95 variables, 37 chunks on 0xf0, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 157 bytes per sample
```

The full EEPROM image also belongs in the BDM backup before the first write
(`docs/07` §6.2, S4). This non-destructive read shows what a used car's block 8
holds. Per `docs/05` §3.8 (note of 2026-09-23), **+2 … +18 are the 17
adaptation-channel slots**. Record them, and **+19..+28** as well: brief G7 (merged 2026-09-24) moved the store to +19 and `eeprom.md` §5 predicts 0x00 at +19..+28 — this read is what stop line S12 asks for.

**Why step 3 comes before any flash:** every row above is a read of the
**stock** image. After a flash it would no longer be a stock read. Flash 1's
decision table (step 6) takes 3c's raster-counter read as its first input.

---

## Step 4 — The RAM snapshots (#23, dynamic half)

**Drives:** `re/findings/ram.md` §9 (the six sessions and the pass criteria),
`logging/sessions/ram_snapshot.json` (the ranges), `logging/README.md` §3b and
§8 ("A RAM snapshot takes about a minute"), and `tools/ram_snapshot_diff.py`.
**Unit:** SW `1037382557` only (S2). The ranges and the patch block 0x7FFB00
belong to this software.

### 4a. Rehearse [Mac]

```bash
./.venv/bin/python3 logging/med9log.py dump --sim --ranges logging/sessions/ram_snapshot.json -o work/sim_snap.bin --session-name key-on
./.venv/bin/python3 tools/ram_snapshot_diff.py --self-test
```

```
63932 bytes in 60.836 s -> work/sim_snap.bin
self-test OK
```

A simulated snapshot carries `"simulated"` and never goes into the comparison
set (`logging/README.md` §4).

### 4b. The six snapshots [bench-only / car-only]

One `dump` per state, with `--session-name` naming it (`logging/README.md` §8):

| # | State (`ram.md` §9) | Where |
|---|---|---|
| 1 | **key-on**, engine not started. Read 0x7F8012 first (0x41 = 32 KB, 0x44 = 64 KB, `ram.md` §7) | bench or car |
| 2 | **idle**, warm, ≥ 2 min after start | **car** |
| 3 | **after a drive** that exercised knock control, the fuel adaptations and the rail controller, engine off, ignition on | **car, road** |
| 4-6 | **key cycle 1, 2, 3**: key off, wait for the main relay to drop (~10 s), key on, snapshot immediately | bench or car |

Then:

```bash
python3 tools/ram_snapshot_diff.py logging/sessions/snap_*.json --free 64 --csv work/ramdiff.csv
```

### 4c. Pass, and what a failure means

Quoting the criteria of `ram.md` §9, not restating them:

* **Pass:** every byte of **0x7FF770-0x7FFFEB** is `blank` in all six. The
  stack range 0x7FF000-0x7FF76F has a `changed` floor below 0x7FF770.
  0x804990-0x807FFF does not change between the six.
* **A `changed` byte in 0x7FF770-0x7FFFEB:** the block is live. §9 names the
  fall-back (candidate 2, 0x7FE0C0-0x7FE587). The patches' `build.ram` has to
  move, and that is a desk brief, not a bench fix.
* **Stack floor above 0x7FF770:** the recommendation is wrong and is withdrawn
  immediately (§9).

**On a pass:** mark `ram.md` §10's rows and #23's "Dynamic" checkbox. Then
change `"ram_status"` to `"verified"` in `patches/ff_counter/patch.json` and
`patches/ff_fuel/patch.json`, as a reviewed desk commit that cites the six
files. That is what lifts gate **S3** (`docs/07` §2.3). The #23 exit criterion,
"unchanged across runtime dumps at idle, driving and key-off/on", is then met.

**Why before Flash 0:** Flash 0 is not a patch and has no `ram_status`, so
`docs/07` §6.4's row does not literally cover it. This playbook still puts the
snapshots first, for two reasons. The G6 brief states S3 as "do not flash at
all". And it keeps every step-3 and step-4 read on a never-written ECU, so a
Flash 0 that goes wrong cannot cost you the stock reads.

---

## Step 5 — Flash 0: the unmodified file (#26)

**Drives:** `docs/07` §3.1 (pre-flight, all eight rows), §3.2 (write), §3.3
(read back, then the three E6 checks), §3.4 items 1-2, and
`re/findings/flash_programming.md` §7.2 (the read-back checklist; also
`docs/06` §6's note). **Unit:** the software-matching spare. Gates S1-S7 and S4
in particular: the BDM backup of *this* unit comes first.

### 5a. The file [Mac]

"Re-saved through our tools" is `checksum.py fix` on a copy (`docs/07` §1.3:
*run `fix` on an untouched dump and it changes nothing*):

```bash
cp data/passat_azx_ori.bin work/flash0_src.bin
./.venv/bin/python3 tools/checksum.py fix work/flash0_src.bin -o work/flash0.bin
./.venv/bin/python3 tools/checksum.py verify -q work/flash0.bin
./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/flash0.bin
shasum -a 256 work/flash0.bin
```

```
0 descriptor(s) updated
ALL OK (65 blocks)
0 changed range(s): 0 patch (0 B), 0 descriptor (0 B), 0 unexpected (0 B)
b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3  work/flash0.bin
```

The file you send is byte-identical to the car's read, and its hash says so.

### 5b. Write, then read back [bench-only]

Write with KESSv2 protocol 179 (`docs/07` §3.2). **Its checksum correction must
be a no-op** (S7). Read the whole ECU back into `work/readback.bin`, then:

1. `bindiff data/passat_azx_ori.bin work/readback.bin` must print **0 changed
   ranges** (`docs/07` §3.3; for Flash 0 there is no `patch.json`, so run it
   without `-p`). Anything else means a check we do not know about. Stop (S8,
   S13).
2. **0x404000-0x47FFFF read back** (§3.3 check 1). Record it. **What it cannot
   tell you on Flash 0:** the written file *is* the stock image, so "KESS wrote
   the array" and "KESS skipped it" give the same bytes. The on-chip answer
   that `patches/ff_counter/test/procedure.md` §3b wants therefore comes from
   **Flash 1's own read-back of 0x432940**. `docs/07` §3.4's note,
   consequence 1, already makes that check part of Flash 1 (step 6b). What
   Flash 0's read-back *does* prove is that nothing on-chip was damaged.
3. **`5A 5A 5A 5A` at file 0x1E2500** (§3.3 check 2).
4. **EEP_CONF block 10 after**, compared with step 3e (§3.3 check 3).
5. **Power-cycle, then the flash CRC again** with step 2d's command. It must
   still publish **0x5562139F**: same content, same CRC (`flash_crc.json`
   items 5-6). This is a read-back that does not depend on KESS's own read
   routine.

### 5c. Then

Clear DTCs (`docs/07` §3.4 item 1). Run `probe --bus gs_usb:0` again (step
2b). Read the DTCs with VCDS. Log the step-3d scenario once more and compare it
with the baselines (`docs/07` §7, chapter 5).

> **2026-09-24 (H2, #47/#26):** run `logging/sessions/adaptation_channels.json`
> before *and* after this flash and after the DTC clear — the block-8 channels
> (incl. fuel trims 4/8/10 and the E% at +19), 0x7FA02B and 0x7FEB59 — so a
> reset from the routine-0xC5 path (`re/findings/eeprom.md` §11) is visible;
> Flash 0 is a bare download and should not trip it.

**Pass = the #26 exit criterion:** the ECU runs the re-saved file, the
read-back equals the written file (items 1 and 5), TesterPresent works, and
there is no DTC beyond the expected bench faults (`hardware_prep.md` §3.5:
missing partners set DTCs, which is expected). Record the hashes of the written
and read-back files in #26 (its deliverable).

**Fail means:**

| Symptom | Meaning | Next |
|---|---|---|
| KESS "corrected" something | its correction is not a no-op on a file that verifies | S7. Stop, find out what it changed |
| bindiff shows changes outside the on-chip region | an unknown check, or a bad read | stop, S13. Roll back per `docs/07` §6 |
| an on-chip difference | a damaged or partial write of the array | S8. Roll back; do not go to Flash 1 |
| the ECU stays in the loader | the `5A5A` marker (§3.3 check 2, §6.3) | recoverable, not a brick (`docs/07` §6.3). Stop, and choose the recovery route. The loader speaks SCI1 serial, not CAN (`re/findings/ram_loader.md`; F5's note on #26). The shop/BDM write-back of M1 is the planned route (`docs/01` §6) |
| a CRC other than 0x5562139F | the flash is not the file you wrote | S13 |

**Why before Flash 1:** Flash 0 changes nothing, so it proves the route, the
no-op checksum correction and the read-back without any of our code in the
ECU (`docs/07` §3.4 item 2). A failure here is a tool or harness problem, and
nothing else. `ff_counter` procedure §0 also lists Flash 0 as a prerequisite.

---

## Step 6 — Flash 1: the both-sets counter (#27)

**Drives:** `patches/ff_counter/test/procedure.md`: §0 (prerequisites), §1-§2
(what is in the ECU, the DDLI), §3a-§3c (the three measurements, in that
order), §4 (the decision table, rows A-I), §4.5 (`HOOKS=external`), §5
(regression), §6-§7 (if wrong, roll back). Also `docs/07` §3.4 item 3 and its
note of 2026-09-22, and `logging/sessions/flash1_counter.json`. **Unit:** the
software-matching spare that ran Flash 0. **Gates:** S3 (step 4 passed and
`ram_status` is `verified`), S4, S5.

### 6a. Build and rehearse [Mac]

```bash
./.venv/bin/python3 tools/patch_apply.py data/passat_azx_ori.bin patches/ff_counter -o work/ff_counter.bin
./.venv/bin/python3 tools/bindiff.py data/passat_azx_ori.bin work/ff_counter.bin -p patches/ff_counter/patch.json
```

```
sha256: 3cd20443c068ed009b7d48b32210790eb320cb159489fc36cc1ef1ea67696498
11 changed range(s): 5 patch (229 B), 6 descriptor (16 B), 0 unexpected (0 B)
```

Today `patch_apply.py` also prints the `ram_status` do-not-flash warning
(`docs/07` §3.4 note). After step 4 it must not. The on-chip warning for
0x432940 stays, by design.

Rehearse rows A and B of the decision table in the simulator:

```bash
./.venv/bin/python3 logging/med9log.py log --sim --sim-patch patches/ff_counter --session logging/sessions/flash1_counter.json --patch patches/ff_counter/patch.json --seconds 3 -o work/sim_flash1.csv
./.venv/bin/python3 logging/med9log.py log --sim --sim-patch patches/ff_counter --sim-task-set B --session logging/sessions/flash1_counter.json --patch patches/ff_counter/patch.json --seconds 3 -o work/sim_flash1_setB.csv
```

```
session flash1_counter: 17 variables, 11 chunks on 0xf0, 44 bytes per sample
```

In the first run `ff_alive` = 64513, `ff_src_seen` = 1 and `ff_ticks` tracks
`raster_setA_10ms_count` (row A's pattern). In the second, `ff_src_seen` = 2
and set B counts (row B's pattern). On the stock image without `--sim-patch`,
the block stays at 0, which is row D's pattern.

**The expected flash CRC of this image**, per `flash_crc.json` item 6
("recompute the expected value from the patched image the same way"):

```bash
./.venv/bin/python3 logging/med9log.py log --sim --sim-dump work/ff_counter.bin --sim-flash-crc --time-scale 8 --seconds 330 --session logging/sessions/flash_crc.json -o work/sim_flash_crc_ff_counter.csv
```

It publishes `flash_crc_pub_hi` = 1728, `_lo` = 35540, i.e. **0x06C08AD4**
(state 7, about 200 s of wall time on the M2). A zlib CRC-32 over the same
three ranges of `work/ff_counter.bin` gives the same value. **This holds for the
image with the SHA-256 above only.** Recompute it if the image changes.

### 6b. Write, read back, then the three measurements [bench-only]

Write it like Flash 0 (5b, `docs/07` §3.2). Read back, then
`bindiff work/readback.bin` against `work/ff_counter.bin -p
patches/ff_counter/patch.json` (procedure §1). Then `blobdis` both words out of
the **read-back** (`docs/07` §3.4 note, consequence 1):

```bash
./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x22E940 --addr 0x432940 --len 4
./.venv/bin/python3 tools/blobdis.py work/readback.bin --file-off 0x12067C --addr 0x12067C --len 4
```

[Mac] on the built image, which is what the read-back must show:

```
00432940  4B D1 D6 C1  bl       0x150000
```

The stock word there is `4B C8 B0 A5  bl 0xbd9e4`. **That is the on-chip input
of the decision table** (procedure §3b, "written" or "stock"; see 5b item 2 for
why Flash 0 could not supply it).

Then, in procedure §3's order:

* **3a**, the five stock raster counters. You already have them from step 3c
  on the stock image. Read them again on the flashed ECU.
* **3b**, the on-chip read-back above.
* **3c**, the counter log [bench-only]:

  ```bash
  python3 logging/med9log.py log --session logging/sessions/flash1_counter.json \
      --patch patches/ff_counter/patch.json --bus gs_usb:0 --seconds 70 \
      -o logs/2026-xx-xx_flash1.csv
  ```

  Fit the slope over ≥ 60 s; the tolerance is ±2 % (procedure §4, "Method").
* **The flash CRC**, after a power-cycle (step 2d's command). It must publish
  **0x06C08AD4**, not 0x5562139F (`flash_crc.json` item 6).

### 6c. Which row proves what (procedure §4, quoted in outline)

| Row | Inputs | What it proves | Where it goes |
|---|---|---|---|
| **A** | set A moving, on-chip **written**, 100 ±2 /s, `ff_src_seen` = 1 | **The expected outcome.** `scheduler.md` §11.8 goes from VERIFIED-STATIC to **VERIFIED-DYNAMIC**; the 10 ms raster is confirmed end to end; the live-set row of #44 is ticked; KESSv2 **does** write the on-chip array over OBD, which **settles the on-chip half of #32** for `ff_fuel` as well | dated note in `scheduler.md`; #44, #32, #27 |
| **B** | set B moving, 100 /s, `ff_src_seen` = 2 | §11.8 is **wrong**. E1/E2/E5 assume set A | **S9: stop.** Reopen #34 and #44 with the log |
| **C** | 200 ±4 /s, `ff_src_seen` = 3 | both sets run; every "once per 10 ms" claim needs a re-check | reopen #44 |
| **D** | set A moving, on-chip **stock**, slope 0, `ff_alive` never set | **KESS skipped the on-chip array.** No counter patch can run, and neither can six of `ff_fuel`'s eight hooks (`flash_programming.md` §7.3). A tool-capability result, not a patch bug | §7.3's ladder (BDM/K-TAG, or a tool that drives the firmware's own route). **Do not** rebuild and **do not** move the hook. `make HOOKS=external` helps **only** if set B is live (row B) *and* the array cannot be written (§4.5). In row D set A is live, so it is useless |
| **E** | on-chip **written**, slope 0 | read-back and execution disagree | **S8/S9: stop.** Re-read the whole image and disassemble both sites out of the read-back |
| **F** | set A moving, written, 100 /s, `ff_src_seen` = 2 | contradictory; suspect the logger first | re-read after a power cycle |
| **G** | neither set moving | the logger reads the wrong place, or the application is not running | check `5A5A` (`docs/07` §3.3 check 2) |
| **H** | 10 ±0.2 /s | C4's tick chain is wrong | reopen #44 |
| **I** | any other slope | not a fixed-period raster | record the pattern (`scheduler.md` §11.3) |

The full wording, including what to write where, is procedure §4. This table
is only its index.

### 6d. Regression, then the #27 exit

Procedure §5: `logcmp.py` of the Flash-1 log against the step-3d baseline, with
`--align-on raster_setA_10ms_count:100` (or the set-B counter, if 3a said set B)
and `--uncovered report`. Exit 0 plus row A is the **#27 exit criterion**: the
counter increments at the task rate, and the log comparison shows no other
change. If anything is off, roll back (procedure §7). Row D's roll-back needs
no on-chip write (§7, note).

> **2026-09-24 (H2, #47/#27):** run `logging/sessions/adaptation_channels.json`
> around this flash too. Flash 1 is still a bare download of ff_counter (no
> `31 C5`), so the adaptation channels should be unchanged; a reset here would
> mean the tool ran component-protection adaptation (`re/findings/eeprom.md`
> §11) — record it against #28.

**Why here:** Flash 1 is the first code of ours in the ECU. It needs S3
(`ram_status` from step 4), Flash 0's proof of the route (step 5), and step
3c's stock counters. Without the counters, a frozen `ff_ticks` has two
explanations instead of one (procedure §0).

---

## Step 7 — Where it goes next (pointers only)

Each item is its own procedure. Only the order and the gates are given here.

1. **`ff_fuel`, its first flash and E0 equivalence (#32).** Flash the shipped
   image — `ff_persist_enable` = 1 by ruling (Carlo, 2026-09-24), the E% store at
   block 8 +19 since G7 — **after** step 3f has recorded +19..+28 (S12). Before
   that, settle `procedure.md` §0's last HYPOTHESIS with
   `logging/sessions/can_bc_check.json`: TouCAN C must share the wire with B, or
   the Pico is on the wrong pair. Then `patches/ff_fuel/test/procedure.md` §1
   (read back **all seven** on-chip words; the list is in §1 and the
   README's hook table), §2-§3, and §4 (E0 equivalence against step 3d's
   baseline). If Flash 1 came out row D, this item cannot run at all
   (`flash_programming.md` §7.3).
2. **The fault matrix (#37):** `procedure.md` §5. Settle before you trip
   (`docs/07` §5.6).
3. **Measuring block 111 (#39):** `procedure_d2.md` Part A. **Part B, the E%
   store (#38):** G7 is merged and the store is at +19; run it once step 3f's
   block 8 read is on record (S12).
4. **OBD PID 0x52 (#39, optional half; brief G1, merged).** The seven
   stock-instruction edits are in every `ff_fuel` image and are run-time gated:
   with `ff_pid52_enable` = 0 mode 01 is observably stock
   (`patches/ff_fuel/README.md` "Stock-instruction edits (PID 0x52)" and "What a
   tester sees"; `re/findings/obd.md` §9). Bench check: set `ff_pid52_enable` =
   1. That is a calibration change to FFCAL001, and so a flash of its own
   (`docs/07` chapter 1, §2.4, S11). Then **disconnect and reconnect the generic
   scan tool** once the ECU is up. After that, `01 40` shows bit 0x40 of its
   third byte and `01 52` answers `41 52 A`, E% = A × 100 / 255. The reconnect
   is needed because the support bitmap is rebuilt once per new diagnostic
   connection (`obd.md` §10.3, G3). The 0x7DF/0x7E8 transport itself has never
   been exercised, so it is a bench item (README, same section).
5. **One feature at a time (S11):** the ignition blend `procedure_e1.md`
   (#34), then start enrichment `procedure_e2.md` (#35), then the rail adder
   `procedure_e5.md` (#36). Each one is an `ff_*_enable` byte flipped in
   FFCAL001, not a rebuild (`docs/07` §3.4 item 3). Log, then compare
   (chapters 4-5).
6. **#28, the car's ECU:** Flash 0 and Flash 1 again, with the BDM backup of the
   *car's* unit in `data/backup_bdm/MANIFEST` (S4) and only files that ran on
   the spare (S10). #28's exit criterion: no DTCs, adaptation values unchanged,
   counter visible over OBD.

Before each of 1-5, the simulator rehearsal of `docs/07` §4.5
(`logging/bench_rehearsal.py --fresh-eeprom`) runs all eleven numbered steps
of `ff_fuel.json` against the patch [Mac]:

```
69/69 checks passed
```

(2026-09-24, integration head, about 3.5 min on the M2.) It rewrites
`logging/samples/ff_fuel_sim_*.csv`. Run it on a scratch checkout, or
`git checkout -- logging/samples/` afterwards, unless you mean to update them.
Brief G5 is changing this tool and `ecu_sim.py` in parallel, so re-run it on
the day.

---

## Which issue closes when

| Step | Issue | Exit criterion (from the issue) | Closed by |
|---|---|---|---|
| 1 + 2b + 2c | **#3** bench harness | ECU answers TesterPresent over TP2.0 on the bench; Pico frames visible in SavvyCAN | `probe --bus` answers (2b) and 0x0EC at 10 Hz (2c) |
| 2d, 3b | **#20** logger (VCDS row) | logs nmot/rl/ti/lambda on the bench ECU and in the car via OBD at ~40 samples/s; a RequestUpload snapshot works | 3b in the car (`groups` vs VCDS) plus 4b (`dump`). T_bg (2d, 3c) is #20's G3 bench number |
| 3c | **#44** wave-B confirmations | every row has a logged value that matches, or a recorded correction | the table in 3c, row by row; the live-set row also closes by Flash 1 row A |
| 4 | **#23** RAM survey | block unreferenced statically **and** unchanged across dumps at idle, driving and key-off/on | the six snapshots pass `ram.md` §9 → `ram_status: verified` |
| 5 | **#26** Flash 0 | runs the re-saved file; read-back = written; TesterPresent works; no DTC beyond bench faults | 5b items 1-5 and 5c |
| 6 | **#27** Flash 1 | the counter increments at the task rate; the log comparison shows no other change | row A plus procedure §5 exit 0 |
| 6 (row A) | **#32** on-chip half | KESS writes 0x404000-0x47FFFF | Flash 1 row A; row D sends it to §7.3's ladder |
| 7.1 | **#32** acceptance | E0 equivalence of `ff_fuel` | `procedure.md` §4 |
| 7.2 | #37 | fault matrix | `procedure.md` §5 |
| 7.3-7.4 | #39, #38 | block 111 / PID 0x52 / E% across power loss (store at +19) | `procedure_d2.md`; README "What a tester sees" |
| 7.5 | #34 / #35 / #36 | per feature | `procedure_e1/e2/e5.md` |
| 7.6 | **#28** | car runs normally; no DTCs; adaptation unchanged; counter visible via OBD | Flash 0 + Flash 1 on the car, BDM backup in hand |

Nothing on this list is ticked by desk work. The issue is closed by the human,
from the log.

## The day in one picture

```
[Mac]  docs/07 §0.3 check; every [Mac] rehearsal above
  |
1  bring-up (mule is fine)  ->  2  probe --bus, Pico frames      => #3
  |
2d flash CRC = 0x5562139F?  --no-->  S2: a mule; get the spare
  | yes (SW 1037382557)
3  #44 reads (mostly car, read-only) + two stock baselines + EEPROM blocks 10, 8
4  six RAM snapshots -> ram_snapshot_diff --free 64 -> ram_status: verified   => #23
  |                                                    (S3 lifted)
   BDM backup of this unit in MANIFEST?  --no-->  S4: stop
  | yes
5  Flash 0 -> read back -> bindiff 0 ranges, 5A5A, block 10, CRC 0x5562139F   => #26
6  Flash 1 -> read back 0x432940 -> 3a/3b/3c -> row A..I -> CRC 0x06C08AD4    => #27 (+#44, #32 on row A)
  |
7  ff_fuel (store at +19, persist on) -> E0 (#32) -> faults (#37) -> 111/0x52 (#39) -> E1, E2, E5 -> #28 on the car
```

## Open questions this ordering could not settle

1. **The Flash 0 on-chip read-back cannot distinguish "written" from "stock".**
   The file is the stock image. `patches/ff_counter/test/procedure.md` §3b and
   row D/E speak of "the Flash 0 on-chip read-back". This playbook takes that
   input from Flash 1's read-back of 0x432940 instead (5b item 2, 6b), which
   `docs/07` §3.4's note already requires. The owner of `procedure.md` may want
   §3b reworded.
2. **No session file logs the background-loop counter 0x7FD70C** (`boot.md`
   §6.8(e) item 2, `re/symbols.csv`). `flash_crc.json` logs the cursor, which
   stops at the publish, so an idle T_bg read works only while the CRC is still
   running. G5 owns `flash_crc.json`.
3. **Measuring id 43 (`lamsbg_w` 0x80304A)** is in no stock group and no session
   file, so G4's "should read 1.000" has no ready command.
4. **The engine-running scenarios on a bench spare.** `ff_counter` procedure §3
   (idle plus load step) and `ff_fuel` procedure §4 assume a warm, running
   engine. A bench ECU without crank or cam stays "engine not running"
   (`hardware_prep.md` §3.5). Neither procedure says whether the Flash 1 slope
   and the regression of §5 are to be taken engine-off on the bench (procedure
   §2 check 4 says the counter runs with KL15 only) with the engine-running
   comparison deferred to #28. Decide before the baselines of step 3d are
   recorded.
5. `logging/README.md` §8 says a snapshot is 1,032 TransferData blocks, and
   `ram.md` §9 says 1,031. The tool prints `5 ranges, 63932 bytes, 1032
   TransferData blocks`, because each of the five ranges rounds up. It does not
   matter for the procedure.
   *Settled 2026-09-24 (H4): both were wrong — per-range rounding gives
   1,034; the tool printed ceil(total / 62) = 1,032 and now prints 1,034
   (`ram.md` §9).*
