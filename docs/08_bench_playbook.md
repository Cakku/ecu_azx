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
