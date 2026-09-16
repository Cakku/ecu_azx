# ff_fuel bench procedure, D2 half — measuring block 111 (#39) and the E% store (#38)

Brief **D2**, 2026-09-16. The companion of `procedure.md`, which covers the
MVP itself (#32/#37); do **not** start here. §1 of `procedure.md` (the on-chip
read-back) and §2 (the state block) must both have passed, because everything
below assumes the patch is running at all.

Written **before** any flash, as `docs/04_re_guidelines.md` §7 requires.
Nothing here has been run on an ECU. Every expectation is a prediction from
`tests/test_ff_diag_patch.py` and `emu/models/flexfuel.py`; the bench is what
turns it into a fact.

> Both blockers of `procedure.md` still apply: `"ram_status": "static"` and the
> undemonstrated KESSv2 write of the on-chip flash. **Do not flash yet.**

## 0. What is new in the image, compared with D1

| | |
|---|---|
| **Flash** | the blob at 0x152000 is now 3,860 B (was 2,304) |
| **New flash edits** | 16 B at 0x0A78A8 (`tbl_measuring_vars` ids 2196-2199) and four u16 at 0x5C55F6 / 0x5C57F4 / 0x5C59F2 / 0x5C5BF0 (`tbl_measuring_groups` group 111) |
| **New RAM** | `ff_persist_buf` 0x7FFB40 (1 B) and `ff_nvm_req` 0x7FFB44 (12 B), still inside the declared 0x7FFB00/0x100 block; `.bss` is 80 B of 256 |
| **New calibration values** | `ff_persist_enable` = **1**, block 8, offset 0, 5 %, 60 s. No new calibration *object*, so the XDF is unchanged |
| **New stock code called** | `nvm_block_request` (0x06131C) and `measuring_result_emit` (0x038EB4). Nothing else, and never a raw SPI primitive |
| **New EEPROM byte written** | EEP_CONF block 8, payload **+0**, one byte, 0..100 |

A rehearsal of the whole D2 half without an ECU, which should be run first:

```bash
cd patches/ff_fuel && make check gen apply          # -> work/ff_fuel.bin
cd ../.. && ./.venv/bin/python3 -m unittest tests.test_ff_diag_patch
./.venv/bin/python3 logging/med9log.py groups --sim --sim-dump work/ff_fuel.bin 111
```

---

# Part A — measuring block 111 (#39)

## A1. The first read, engine off, ignition on

VCDS (or VCDS-Lite / VAG-COM / OBDeleven): **Engine 01 → Measuring Blocks →
group 111**. On the wire that is `21 6F` in session `10 89`.

Four fields, in this order:

| Field | Shows | Formula | Range |
|---|---|---|---|
| 1 | filtered ethanol | 0x21, A = 100 → the value *is* B | 0…100 % |
| 2 | fuel factor F | 0x21, A = 100 → 100 % = 1.000 = stock | 100…200 % |
| 3 | fuel temperature | 0x05, A = 10 → `B − 100` °C | −40…+155 °C |
| 4 | `256 × persist_state + mode` | 0x36, a plain count | see A3 |

**Expected on a car that has never seen the Pico**, a few seconds after
ignition on:

```
field 1   0 %          (E0)
field 2   100 %        (F = 1.000, bit-identical to stock)
field 3   ---          "not available": no frame has arrived, so there is no
                       fuel temperature to report
field 4   3            persist_state 0 (idle) + mode 3 (FAULT)
```

**If all four fields read `---`**, exactly one of three things is true, in
order of likelihood:

1. the state block header is not valid — i.e. the periodic hook never ran.
   Go back to `procedure.md` §2; `ff_src_seen` is the first thing to look at.
2. the group-table edit did not take. Read back 8 bytes at 0x5C55F6 over KWP
   and expect `08 94`, and likewise 0x5C57F4/0x5C59F2/0x5C5BF0 → `08 95`,
   `08 96`, `08 97`. A blank there means the calibration block was not written.
3. the TKMWL edit did not take. Read 16 bytes at 0x0A78A8; `00 03 8E C4` four
   times means the flash write did not happen (this is the code region, not
   the on-chip one, so it would be surprising).

**If VCDS says "group not supported"**, the ECU refused `21 6F`, which the
firmware only does above group 0x7F — i.e. the tester asked for something
else. Check the group number, not the patch.

## A2. With the Pico running

Start the Pico (`procedure.md` §3) and watch group 111 for two minutes.

* field 1 rises towards the Pico's E% at **no more than 2 %/s** — from E0 to
  E85 takes about 43 s, and that slope is the acceptance criterion, not the
  end value;
* field 2 follows `ff_F_curve`: at E85 it reads **154 %** with the shipped
  curve. Cross-check it against field 1 with
  `./.venv/bin/python3 -m emu.models.flexfuel`, which prints the curve;
* field 3 becomes a number within one second of the first frame and should sit
  near ambient;
* field 4 becomes **1** (persist idle, mode OK).

Record the same run with the logger, because the measuring block is 1 %
granular and the log is not:

```bash
./.venv/bin/python3 logging/med9log.py log --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json --bus gs_usb:0 --seconds 180 \
    -o logs/2026-xx-xx_ff_group111.csv
```

`ff_diag_e_pct` in that log must equal field 1 of the measuring block sample
for sample, and `ff_diag_f_pct` field 2. If they disagree, the group table
points at the wrong ids.

## A3. Field 4, decoded

| Reading | mode | persist_state | Means |
|---|---|---|---|
| 0 | INIT | idle | only in the first activation; never seen by a tester |
| **1** | OK | idle | running on the sensor, no commit due |
| 2 | HOLD | idle | the Pico reports "contaminated": E and F are frozen |
| **3** | FAULT | idle | no usable frame; F is held then decays to `ff_e_key` |
| 4 | OFF | idle | `ff_mode` = 0 in the calibration, or FFCAL001 is not accepted |
| 5 | OVERRIDE | idle | `ff_mode` = 2, bench override |
| 257 / 513 | OK | staged / committing | an EEPROM write is in flight — lasts a few tens of ms |
| **769** | OK | done | the last commit succeeded. **This is the #38 pass indication** |
| 1025 | OK | error | the last commit failed; read `ff_persist_err` (§B4) |

## A4. Nothing else may change

Read three stock groups before and after flashing and compare them field by
field: **001** (rpm, coolant), **003** (rpm, mass air flow) and **115**, the
group whose *echo* is 238 and therefore shares nothing with ours. They must be
identical, which is what proves the group-table edit hit only group 111.

```bash
./.venv/bin/python3 logging/med9log.py groups --bus gs_usb:0 1 3 111 115
```

The response to `21 6F` is 26 bytes: `61 6F` plus eight triples. Triples 5-8
are group 238 and must all read `25 00 00`.

---

# Part B — E% across power loss (#38)

> **Read the EEPROM once, before anything else.** `re/findings/eeprom.md` §5
> caveat 2: *nothing in the dump proves the factory leaves block 8 payload
> +0..+13 at 0xFF.* The patch writes payload **+0**. If a real ECU has
> something there, this half must not be flashed until it is known what.
> The non-destructive check is B1; the destructive one is a bench EEPROM read
> with the ECU open.

## B1. What is in block 8 today (before flashing)

With the **stock** image, log the block manager's RAM mirror over DDLI — the
mirror is filled from the device at every start-up, so it is a faithful copy:

```bash
./.venv/bin/python3 logging/med9log.py log --session logging/sessions/ff_fuel.json \
    --bus gs_usb:0 --seconds 5 -o logs/2026-xx-xx_blk8_before.csv
```

and read the five `eep_blk8_*` variables.

* `eep_blk8_mirror_b0` — **must be 0xFF (255) or 0**. Anything else means a
  stock function we have not found uses payload +0; stop, and move the patch
  to `ff_persist_offset` = 1 (one calibration byte, no rebuild).
* `eep_blk8_mirror_b14` — whatever it is, note it. It is the one byte stock
  code writes (cpu 0x134380) and it must be unchanged by everything below.
* `eep_blk8_mirror_b29` — the manager's own byte; note it, never write it.
* `eep_blk8_mirror_csum` — `sum(payload +0..+29) + csum == 0xFFFF`. If that
  does not hold on the stock image, the mirror was never filled and B3 will
  fail for reasons that have nothing to do with the patch.

## B2. The first commit

Flash, start the Pico at a known E%, and log for **three minutes** (the rate
limit is 60 s and it is armed at power-up).

Expected, in order:

| t | What |
|---|---|
| 0 | `ff_persist_wait` = 60.00 s, counting down at 100/s; `ff_persist_state` = 0 |
| 0 | `ff_e_persist` = 65535 on a virgin store (or the stored percent, if B1 found one) |
| ~60 s | `ff_persist_state` goes 2 for a few samples, then **3**; `ff_persist_writes` = 1; `ff_persist_err` = 2 |
| ~60 s | `eep_blk8_mirror_b0` becomes `ff_diag_e_pct` |
| ~60 s | `ff_persist_wait` is back at 60.00 s |
| 60-180 s | nothing more happens unless E moves by ≥ 5 % |

Hard requirements for the whole log:

* `ff_persist_fails` stays **0**;
* `eep_blk8_mirror_b14` and `eep_blk8_mirror_b29` never change;
* `sum(payload) + csum == 0xFFFF` in **every** sample — the stage maintains the
  checksum itself, so there is no window in which the block is inconsistent;
* `nvm_queue_state` is 0x20 or 0x21 in almost every sample. If it sits at
  0x23-0x27 the EEPROM driver is stuck and the stock code's own blocks are
  affected too — power down and investigate before driving.

## B3. The disconnect test — the acceptance test of #38

This is the whole point of the issue.

1. Run the Pico at a **stable, distinctive** ethanol percentage, ideally
   E60-E80 so the value cannot be confused with 0 or 100. Wait until
   `ff_persist_state` = 3 and note `ff_diag_e_pct` — call it **E_before**.
2. **Ignition off.** Wait 10 s.
3. **Disconnect the battery**, or pull the ECU's main fuse if the car's
   comfort bus makes that easier. Wait **five minutes** — long enough that
   nothing capacitive survives. (`re/findings/ram.md` §3: the external SRAM is
   ordinary `.bss` and is zeroed at every cold start anyway, so this step is
   about the *EEPROM*, not about RAM retention.)
4. Reconnect. **Do not start the Pico.** Ignition on, and read group 111
   within the first few seconds, before any frame could arrive.

**Pass:**

```
field 1  = E_before   (±1 %, the rounding to whole percent)
field 2  = F(E_before), i.e. > 100 %
field 4  = 3          (mode FAULT: no frame yet, and persist idle again)
```

and in a log, `ff_e_key` = `ff_e_filt` = `E_before × 16`, `ff_e_persist` =
`E_before`, `ff_persist_err` = 2 (the return code of the *read*).

**Fail, and what it means:**

| Symptom | Cause |
|---|---|
| field 1 = 0, `ff_e_persist` = 65535 | the store read back 0xFF: the commit in B2 never reached the device. Look at `ff_persist_writes` from the previous run |
| field 1 = 0, `ff_e_persist` = 0 | the store read back 0, which is a *valid* 0 %. Either the commit wrote 0, or something else cleared block 8 |
| `ff_persist_err` = 0x81 | the manager refused the shape — the block number in FFCAL001 is wrong |
| `ff_persist_err` = 0x83 | the manager is blocked by the 0x7F9E3C flag; the EEPROM is busy with something else at start-up. Retry later in the drive cycle; if it persists, raise `ff_persist_rate_s` |
| field 1 is some *other* number | payload +0 is not ours. Go back to B1 |

5. Then start the Pico and confirm field 1 settles back to the live value at
   2 %/s, and that `ff_persist_state` returns to 3 within a minute if E moved
   by ≥ 5 %.

## B4. Turning it off

Persistence is one calibration byte. To make the image behave exactly like
D1's:

```bash
cd patches/ff_fuel
./../../.venv/bin/python3 ffcal001.py --set ff_persist_enable=0 \
    -o build/ffcal001.bin --no-rows
make gen && make apply
```

With `ff_persist_enable` = 0 the patch never calls `nvm_block_request` at all —
`tests/test_ff_diag_patch.py::TestPersistence::test_persistence_off_never_touches_the_block_manager`
asserts that the mirror is byte-identical after 400 activations. The measuring
block keeps working; only fields 4's `persist_state` stays 0 for ever.

The same lever moves the store somewhere safer without a rebuild:
`--set ff_persist_offset=1` (or any of +0..+13, +15..+28) and
`--set ff_persist_block=24` with `--set ff_persist_offset=3` for the 255-byte
single-copy block (eeprom.md §5), at the cost of an eight-page write.

## B5. Endurance, stated so it is not forgotten

Worst case with the shipped calibration is **one page write per minute**: the
hysteresis is 5 % and the ethanol estimate can only move at 2 %/s, so a
sustained 5 % swing every 60 s is the fastest the machine can be driven. The
M95160's endurance is 1e6 cycles per page, i.e. about **16,000 engine hours**
at that worst case, and realistically a few writes per refuelling. If a future
calibration lowers `ff_persist_rate_s`, redo this arithmetic first.

---

## C. What this procedure does *not* answer

* whether KESSv2 writes the on-chip flash (`procedure.md` §1) — unchanged;
* whether the factory uses block 8 payload +0 (B1 is the non-destructive
  check; only a bench EEPROM read is proof);
* what triggers the stock write-all-blocks routine at key-off
  (`re/findings/eeprom.md` §9) — the patch deliberately does not depend on it;
* the status the block manager reports when the *device* fails: the emulator
  cannot run the QSPI state machine (it walks into the OS halt spin at
  0x110F0), so codes 0x80 and 0x82 are only ever seen on real hardware. The
  patch's handling of them is tested by injecting them into the request
  record, which is exactly the single `stb` that `nvm_state_complete`
  (0x612B4) performs.
