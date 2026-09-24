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
| S3 | **`"ram_status"` is still `"static"`** in the patch you are about to write | **do not flash at all.** Finish step 4 first | `docs/07` §2.3, §6.4; `patch_apply.py`'s warning |
| S4 | **the target ECU has no verified BDM backup** (external flash, on-chip flash including 0x400000-0x403FFF, EEPROM) with SHA-256s in `data/backup_bdm/MANIFEST` | **do not flash that ECU.** For the car this is absolute (#28 depends on #2) | `docs/07` §3.1 row 4, §6.2; `docs/01` M1 |
| S5 | the file does not verify, has any `unexpected` byte in `bindiff -p`, was not built from this ECU's own read, or has a changed ident block 0x1CEE20 | do not flash it | `docs/07` §6.4 rows 1-4 |
| S6 | a tool offers to address 0x000000-0x01FFFF, 0x080000-0x09FFFF or 0x400000-0x403FFF | refuse. A BDM tool does not refuse by itself | `docs/07` §3.3, last paragraph |
| S7 | KESS reports that it *corrected* a checksum on a file that already verifies | stop and find out what it changed | `docs/07` §3.2 |
| S8 | **the on-chip read-back does not match**: some on-chip words written and others not, or bytes written but no stub runs | **do not trust the image.** Roll back (`docs/07` §6.1) | `docs/07` §3.3 table, row 3; `procedure.md` §4 row E |
| S9 | Flash 1 lands on row **B** or **E** of `patches/ff_counter/test/procedure.md` §4 | no further flash until it is explained | same, §4 |
| S10 | an image has not run on the software-matching bench spare | it does not go to the car | `docs/01` §3 principle 2; `docs/07` §6.4 row 6 |
| S11 | more than one `ff_*_enable` would change in one flash | one feature at a time | `docs/01` §3 principle 5; `docs/07` §3.4 item 3 |
| S12 | **`ff_persist_enable` = 1 in a bench `ff_fuel` image before brief G7 is merged.** It **ships as 1** (`patches/ff_fuel/README.md` FFCAL001 table, +1B). Today the E% store shares block 8 payload +2 with adaptation channel 1 | build the bench image with `ff_persist_enable=0` (`procedure_d2.md` §B4). Read block 8 before any flash (step 3f) | `docs/05` §3.8, note of 2026-09-23; `docs/agent_briefs/README.md` wave-G G2 notes |
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
the cursor walks 0x21004 → 0x9B69C, and the publish is still minutes away:

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
