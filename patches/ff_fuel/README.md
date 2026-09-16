# ff_fuel — the flex-fuel MVP

Issues **#32** (implementation) and the software half of **#37** (FAULT/HOLD
rules). The first patch of ours that changes what the engine does: it reads the
Pico ethanol frame off a spare CAN receive slot, filters it, and scales the
relative fuel mass `rk` by `F(E)`. At E0 it is **bit-identical to stock** —
`F = 1024` takes an early return and never writes `rk` at all.

> ## Do not flash yet — two blockers, both printed by `make apply`
>
> 1. **`"ram_status": "static"`.** `PATCH_RAM = 0x7FFB00` (0x100 B) is the block
>    `re/findings/ram.md` §8.1 recommends: VERIFIED-STATIC that no instruction
>    in the image references any byte of 0x7FF770-0x7FFFEB, above the task stack
>    0x7FF3C0-0x7FF76F, outside the KWP programming copy 0x804800-0x808687 and
>    outside the protected window 0x7F9E3C-0x7FA47F. The runtime snapshots of
>    **#23** are still pending.
> 2. **Two of the three hook words are in the on-chip flash** (0x42247C and
>    0x432940, region 0x404000-0x47FFFF). They are covered by the code
>    descriptor table at file 0x0A0000, so `checksum.py fix` handles them and
>    `verify` says ALL OK — but **whether KESSv2 protocol 179 writes that region
>    has never been demonstrated on this ECU**. `test/procedure.md` §1 is the
>    read-back test that answers it, and it is the first thing to do.
>
> `tools/patch_apply.py` refuses the on-chip range unless each change carries
> `"onchip_edit": true` and warns on every apply even when it is there
> (docs/06_patch_pipeline.md §1, added by this brief).

```bash
cd patches/ff_fuel
make check     # build + assert no r2/r13, no small data, sizes agree
make dump      # disassemble the raw blob (extracts below)
make gen       # regenerate patch.json's `changes` from the blob and FFCAL001
make apply     # -> work/ff_fuel.bin + .diff.json + .sha256
./../../.venv/bin/python3 -m unittest tests.test_ff_fuel_patch tests.test_flexfuel_model
```

`make` here is four lines rather than `ff_counter`'s three: FFCAL001 is data,
built by the patch's own generator (`ffcal001.py`), and it has to exist before
`make gen` reads it. Everything else comes from `patches/common/patch.mk`.

## What it does

| | |
|---|---|
| **Hooks** | three, one word each — see the table below |
| **Trampoline** | `HOOK_TAIL` for all three (`patches/common/hooks.S`): saves LR only, 16-byte frame |
| **RAM** | 64 bytes of the 0x100-byte block at `PATCH_RAM` = 0x7FFB00 |
| **Flash** | 2,304 bytes at 0x152000 (free area 0x150000-0x1AFFFF, all 0xFF) |
| **Calibration** | FFCAL001, 232 bytes at 0x5E2510 (checksum block 0x5E0000-0x5EFFFF) |
| **Stock RAM written** | `rk` 0x803038 (only when F != 1024) and `can_rx_shadow` slot 15 (0x803F98-0x803FA3, which nothing else uses) |
| **Stock code called** | `can_init_mb(15)` 0x135750 once, `can_rx_poll(15)` 0x4379C8 per activation |

`ff_fuel` is placed at 0x152000, not 0x150000, so it and `ff_counter` can sit
in one image if that ever becomes useful. They share the same RAM block and are
never flashed together.

### The three hook sites

| Site | Old word | New word | Task | Tail |
|---|---|---|---|---|
| **0x42247C** | `4B FF 9F 25` `bl 0x41C3A0` | `4B D2 FB 85` `bl 0x152000` | `task_segment_a` 0x4223B0, TCB 5, id 40, **engine-synchronous** | `ba 0x41C3A0` (`rksplit`) |
| **0x432940** | `4B C8 B0 A5` `bl 0x0BD9E4` | `4B D1 F6 E1` `bl 0x152020` | `task_100ms_int` 0x4328E4, id 19, **10 ms, task set A** | `ba 0x0BD9E4` (an empty leaf) |
| **0x12067C** | `4B FF E9 B1` `bl 0x11F02C` | `48 03 19 C5` `bl 0x152040` | `task_100ms` 0x1205A0, id 32, **10 ms, task set B** | `ba 0x11F02C` (`clr_ram_7FE889_800E18`) |

Plus two data edits: **0x2BD8C** `00 00 07 FF` -> `00 00 00 EC` (the id word of
`tbl_can_rx` slot 15) and **0x5E2510** 232 bytes of 0xFF -> FFCAL001.

### Why both 10 ms rasters are hooked

`re/findings/scheduler.md` §11.7 leaves one question open that the dump cannot
answer: **which ERCOSEK task set is live with the engine turning.** `os_init`
installs set A; 0x11DAF4 switches to set B when the byte 0x7FEB5E is non-zero.
A hook in the dead set never runs. C1's site 0x12067C is in **set B**.

Rather than wait for the bench read, ff_fuel hooks the 10 ms raster of **both**
sets. The set-A twin is **0x432940**, and it is a cleaner site than C1's
(`scheduler.md` §8.1, VERIFIED-STATIC):

```
0043293C  4B C8 B1 29  bl 0xbda64
00432940  4B C8 B0 A5  bl 0xbd9e4     <-- hooked
00432944  4B FF F8 99  bl 0x4321dc

000BD9E0  4E 80 00 20  blr            ; end of the previous function
000BD9E4  4E 80 00 20  blr            ; nop_leaf_bd9e4 - the whole function
000BD9E8  94 21 FF F8  stwu r1,-8(r1) ; the next function
```

* the target `0xBD9E4` is an **empty function**, one `blr`, and
  `tools/sda_xref.py data/passat_azx_ori.bin --code 0xBD9E4` finds **exactly one
  caller in the whole image — 0x432940 itself**. So the stock work the tail
  branch must preserve is literally nothing;
* 0x4328E4's body is the usual flat list of argument-less `bl`s, and the entry
  blocks of both neighbours' targets (0xBDA64, 0x4321DC) write r3-r12 before
  they read them, so nothing is live across the site and `HOOK_TAIL` suffices;
* the site is unconditional and at the top level, so it runs exactly once per
  10 ms activation of set A.

**Exactly one set is live, so exactly one hook fires per 10 ms.** If both ever
fired, the tick would still happen once: `ff_state.src_owner` latches the first
source, the other one is counted in `ff_src_foreign` and returns, and ownership
moves only after `FF_OWNER_SWITCH` = 3 consecutive foreign calls with none from
the owner in between — which is also what covers the real case of set A running
for a few activations before the switch to set B. `tests/test_ff_fuel_patch.py`
drives both hooks alternately for 40 activations and asserts `ff_ticks == 20`.

Side effect worth having: **`ff_src_seen` answers §11.7 from a single logger
sample** — 1 = set A, 2 = set B, 3 = both.

### Time constants: physical in the calibration, converted in the patch

Brief C4 (#44) showed every raster in `docs/05` is 10x faster than its name.
Rather than re-derive constants by hand, FFCAL001 stores **physical units** and
the patch converts with `ff_tick_ms`, itself a calibration value:

| Calibration | Value | What the patch computes, per activation |
|---|---|---|
| `ff_filter_tau_ms` | 3000 ms | `K = ff_tick_ms / ff_filter_tau_ms` = **1/300** (docs/05 §8 asks for ≈1/320) |
| `ff_slew_pct_s` | 2 %/s | `ff_slew_pct_s * 16 * 1024 * ff_tick_ms / 1000` = **327** units of 1/16384 % = 0.02 %/activation |
| `ff_timeout_ms` | 1000 ms | `age_ticks * ff_tick_ms > ff_timeout_ms` |
| `ff_hold_s` | 60 s | `ff_hold_s * 1000 / ff_tick_ms` = 6000 activations |
| `ff_tick_ms` | 10 ms | the real raster period (`scheduler.md` §11) |

**The conversion lives in the C, not in `ffcal001.py`.** Moving the hook to a
different raster is then one calibration byte, not a rebuild — and a
calibration written for a 100 ms raster still behaves as its author intended.

The 0.02 %/activation slew is why `e_filt` has a companion `e_frac`: 0.02 % is
0.32 counts of 1/16 %, which would truncate to zero and freeze the filter. The
pair is one 26-bit value in 1/16384 %; `e_filt` alone is what everything reads.

### The RAM block — `struct ff_state` at 0x7FFB00, 64 bytes

`src/ff_state.h` is the authority; `tests/test_ff_fuel_patch.py` and
`logging/sessions/ff_fuel.json` are checked against it.

| Off | Type | Name | Owner | Meaning |
|---|---|---|---|---|
| +00 | u32 | `ff_magic` | tick | `0x46463031` ("FF01") when the block is valid |
| +04 | u16 | `ff_length` | tick | `0x0040` |
| +06 | u16 | `ff_csum` | tick | `~sum16` of the **core** bytes (+08..+2B) |
| +08 | u16 | `ff_e_filt` | tick | filtered ethanol, **1/16 %**, 0..1600 |
| +0A | u16 | `ff_f_q10` | tick | fuel factor, **1/1024**, clamped to 1024..2048 |
| +0C | u8 | `ff_mode` | tick | 0 INIT 1 OK 2 HOLD 3 FAULT 4 OFF 5 OVERRIDE |
| +0D | u8 | `ff_status` | tick | byte 7 of the last frame; 0xFF = none yet |
| +0E | u8 | `ff_e_raw` | tick | byte 0 of the last frame |
| +0F | u8 | `ff_t_fuel` | tick | byte 1, °C + 40 |
| +10 | u8 | `ff_frame_ctr` | tick | byte 3, the rolling counter |
| +11 | u8 | `ff_fw_ver` | tick | byte 6 |
| +12 | u8 | `ff_cal_mode` | tick | `ff_mode` as read from FFCAL001 |
| +13 | u8 | `ff_cal_ok` | tick | 1 = FFCAL001 header and checksum accepted |
| +14 | u16 | `ff_age_ticks` | tick | activations since the last good frame |
| +16 | u16 | `ff_hold_ticks` | tick | activations left of the FAULT hold |
| +18 | u16 | `ff_frames` | tick | good frames since power-up (saturating) |
| +1A | u16 | `ff_faults` | tick | FAULT entries since power-up (saturating) |
| +1C | u8 | `ff_stall` | tick | frames with an unchanged counter |
| +1D | u8 | `ff_src_owner` | tick | 1 = set A hook, 2 = set B hook |
| +1E | u8 | `ff_src_seen` | tick | bit0 set A fired, bit1 set B fired |
| +1F | u8 | `ff_src_foreign` | tick | consecutive calls from the non-owner |
| +20 | u32 | `ff_ticks` | tick | periodic activations since power-up |
| +24 | u16 | `ff_e_key` | tick | the FAULT decay target (**D2** loads it from EEPROM) |
| +26 | u16 | `ff_e_frac` | tick | sub-count of `ff_e_filt`, 1/1024 of a count |
| +28 | u8 | `ff_frame_bad` | tick | 1 = the last frame was rejected (a **latch**) |
| +29 | u8 | — | tick | reserved, 0 |
| +2A | u16 | — | tick | reserved, 0 |
| **+2C** | u32 | `ff_rk_calls` | **rk hook** | segment-task invocations since power-up |
| +30 | u16 | `ff_e_persist` | **D2** | E% staged for / read back from EEPROM block 8 |
| +32 | u8 | `ff_persist_state` | **D2** | 0 idle 1 staged 2 committing 3 done 4 error |
| +33 | u8 | `ff_persist_err` | **D2** | last `nvm_block_request` return code |
| +34 | u16 | `ff_diag_e_pct` | **D2** | `e_filt` in 1 %, for the measuring block |
| +36 | u16 | `ff_diag_f_pct` | **D2** | F in %, `(f_q10 * 100) >> 10` |
| +38 | u16 | `ff_diag_t_degc` | **D2** | fuel temperature, °C + 40 |
| +3A | u16 | — | **D2** | reserved |
| +3C | u32 | — | **D2** | reserved |

Three things about this layout:

* **The header is mandatory, not decoration.** Brief C2 proved the cold start
  does not fill 0x7FF770-0x7FFFEB, so the contents are undefined at power-on.
  `ff_state_init()` runs whenever magic, length or checksum do not describe our
  block, zeroes all 64 bytes and re-seeds them.
* **The checksum covers the core (+08..+2B) only.** It is recomputed at the end
  of every periodic activation, so it may only cover fields that activation
  owns. `ff_rk_calls` is written by the engine-synchronous segment task, and
  D2's persistence fields come from a slower path; a corrupt annex therefore
  does not trigger a re-init, which is right because the annex carries no
  control value.
* **`ff_frame_bad` is a latch, not an event.** Frames arrive at 10 Hz and the
  raster runs at 100 Hz, so nine activations out of ten see nothing. Deciding
  FAULT only on the activation that carried the bad frame dropped straight back
  to OK on the next one — a real bug that the tick-by-tick comparison against
  `emu/models/flexfuel.py` caught.

`patches/ff_counter` uses +0x00..+0x07 of the same block. The two are never
flashed together, and its `ff_alive` = 0xFC01 at +0x04 cannot be mistaken for
our length 0x0040.

### FFCAL001 at 0x5E2510 — 232 bytes

Built by `ffcal001.py` from `ffcal001.json`; `src/ff_state.h` carries the same
offsets and `tests/test_flexfuel_model.py` asserts the two agree.

| Off | Type | Name | Shipped | Unit |
|---|---|---|---|---|
| +00 | 8 B | magic | `FFCAL001` | |
| +08 | u16 | version | 1 | |
| +0A | u16 | length | 232 | B |
| +0C | u16 | `ff_can_id` | 0x0EC | — (documentation; also compared against the id echo at 0x803F98) |
| +0E | u16 | `ff_timeout_ms` | 1000 | ms |
| +10 | u16 | `ff_hold_s` | 60 | s |
| +12 | u16 | `ff_filter_tau_ms` | 3000 | ms |
| +14 | u16 | `ff_slew_pct_s` | 2 | %/s |
| +16 | u16 | `ff_tick_ms` | 10 | ms |
| +18 | u8 | `ff_mode` | **1** | 0 off / 1 normal / 2 bench override |
| +19 | u8 | `ff_e_override` | 0 | % |
| +1A | u8 | `ff_stall_max` | 3 | frames |
| +1B | u8 | `ff_persist_enable` | 0 | **D2** |
| +1C | u8 | `ff_persist_hyst_pct` | 5 | **D2**, % |
| +1D | u8 | `ff_persist_block` | 8 | **D2**, EEP_CONF block |
| +1E | u8 | `ff_persist_offset` | 0 | **D2**, payload offset |
| +1F | u8 | `ff_persist_rate_s` | 60 | **D2**, s |
| +20 | 17×u16 | `ff_F_curve` | formula | 1/1024 over E 0..100 step 6.25 % |
| +42 | 17×u8 | `ff_fzw_curve` | 0 | **reserved**, docs/05 §3.4 |
| +54 | 8×8 s8 | `ff_dzw_map` | 0 | **reserved**, 0.75 °CA |
| +94 | 6×6 u16 | `ff_fst_map` | 1024 | **reserved**, docs/05 §3.5 |
| +DC | 8×u8 | `ff_prail_add` | 0 | **reserved**, 0.1 MPa |
| +E6 | u16 | crc | | `~sum16` of bytes [0, length-2) |

The four reserved tables carry neutral values so a later patch **extends** the
block instead of moving it. `ff_cal_ok` = 0 (a bad magic, version, length or
checksum) forces mode 0, i.e. `F = 1024` and no CAN — the safe direction.

**Descriptor rows go to [`ffcal001_rows.csv`](ffcal001_rows.csv)**, not to
`re/calibration_draft.csv`: brief **D3** owns that file and runs in parallel.
The rows use its exact header, `ffcal001.py` regenerates them, and the
integrator appends them at merge time (then re-runs `tools/draft_to_xdf.py`).
`tests/test_flexfuel_model.py` checks that the checked-in file is current and
that the rows survive the XDF generator.

### `ff_mode` — how to build a mode-0 file

```bash
./../../.venv/bin/python3 ffcal001.py --set ff_mode=0 -o build/ffcal001.bin --no-rows
make gen && make apply
```

Mode 0 forces `ff_f_q10` to 1024 for ever, never polls the CAN slot and never
arms the message buffer, while the state block, `ff_ticks`, `ff_src_seen` and
`ff_rk_calls` all still fill in — the right first flash on a car. Mode 2 takes
E from `ff_e_override` with no sensor and no CAN, for a bench ECU. Rebuild with
plain `ffcal001.py` to get mode 1 back, and re-run `make gen`.

## Blob disassembly

`make dump` — the raw bytes at the address the CPU will fetch them from, not
the ELF. Recorded 2026-09-16, LLVM 23.1.1, `PATCH_FLASH=0x152000`,
`PATCH_RAM=0x7FFB00`. The three trampolines first (identical but for the call
and the tail):

```
                                  ; --- ff_fuel_rk_hook: HOOK_TAIL
00152000  94 21 FF F0  stwu     r1, -0x10(r1)
00152004  7C 08 02 A6  mflr     r0
00152008  90 01 00 0C  stw      r0, 0xc(r1)
0015200C  48 00 07 49  bl       0x152754        ; ff_rk_scale
00152010  80 01 00 0C  lwz      r0, 0xc(r1)
00152014  7C 08 03 A6  mtlr     r0
00152018  38 21 00 10  addi     r1, r1, 0x10
0015201C  48 41 C3 A2  ba       0x41c3a0        ; rksplit
                                  ; --- ff_fuel_hook_a: same, bl 0x152060, ba 0xbd9e4
                                  ; --- ff_fuel_hook_b: same, bl 0x152730, ba 0x11f02c
```

and the whole of the segment-synchronous half, which is the only code that runs
per injection:

```
                                  ; --- ff_rk_scale
00152754  3C 80 00 80  lis      r4, 0x80
00152758  38 64 FB 00  addi     r3, r4, -0x500  ; 0x7FFB00 = PATCH_RAM
0015275C  80 A3 00 2C  lwz      r5, 0x2c(r3)    ; ff_rk_calls
00152760  38 A5 00 01  addi     r5, r5, 1
00152764  90 A3 00 2C  stw      r5, 0x2c(r3)
00152768  80 84 FB 00  lwz      r4, -0x500(r4)  ; ff_magic
0015276C  6C 84 46 46  xoris    r4, r4, 0x4646
00152770  28 04 30 31  cmplwi   r4, 0x3031
00152774  4C 82 00 20  bnelr                    ; no valid state -> stock
00152778  A0 63 00 0A  lhz      r3, 0xa(r3)     ; ff_f_q10
0015277C  28 03 04 01  cmplwi   r3, 0x401
00152780  4D 80 00 20  bltlr                    ; F <= 1024 -> stock, rk untouched
00152784  28 03 08 00  cmplwi   r3, 0x800
00152788  41 80 00 08  blt      0x152790
0015278C  38 60 08 00  li       r3, 0x800       ; clamp to 2048 in code
00152790  3C 80 00 80  lis      r4, 0x80
00152794  A0 A4 30 38  lhz      r5, 0x3038(r4)  ; rk
00152798  7C 65 19 D6  mullw    r3, r5, r3
0015279C  54 63 B2 BE  srwi     r3, r3, 0xa     ; (rk * F) >> 10
001527A0  28 03 FF FF  cmplwi   r3, 0xffff
001527A4  41 80 00 0C  blt      0x1527b0
001527A8  3C 60 00 00  lis      r3, 0
001527AC  60 63 FF FF  ori      r3, r3, 0xffff  ; saturate
001527B0  B0 64 30 38  sth      r3, 0x3038(r4)
001527B4  4E 80 00 20  blr
```

Absolute addressing throughout (`lis 0x80` / `-0x500`, `lis 0x5E` for the
calibration), **no r2 or r13 anywhere** (`--check-sda` OK), no `.rodata`, no
floating point, no 64-bit arithmetic, and every loop is fixed-length.

## Cost

Measured in the Unicorn harness on the applied image (`--trace` instruction
counts, `tests/test_ff_fuel_patch.py::test_the_cost_of_one_activation` guards
them):

| Path | Instructions | Runs |
|---|---|---|
| **segment stub, F = 1024** (E0) | **20** | every injection segment |
| segment stub, F != 1024 | 30 | every injection segment |
| segment stub, no valid state block | 17 | the first milliseconds after power-up |
| periodic, warm, no frame | 485 | 9 of every 10 activations |
| periodic, warm, fresh frame | 618 | 1 of every 10 activations |
| periodic, the non-owner hook | 306 | never, unless both task sets run |
| periodic, mode 0 | 361 | — |
| periodic, cold start (init + tick) | 1,527 | once per power-up |

The periodic figures include the real `can_rx_poll` (which has its own critical
section) and the 36-byte state checksum. At 100 activations per second and
56 MHz that is **about 0.1 % of the CPU**; the worst case is the cold-start
activation, which is a single 1,527-instruction event. Deepest stack use is
**119 bytes below the task's r1**, measured on the cold-start activation — the
ERCOSEK task stack is 0x3B0 bytes (`ram.md` §4.1, §5).

## Applying it

```
$ make apply
ff_fuel: 48 patch range(s) (2494 B), 11 descriptor range(s) (34 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: ac375be0ee3cb1e5f97b3e8fe3f880d964ad270bacf59d9d811e556951f5c8e7
WARNING: ff_fuel: "ram_status": "static" - ... Do not flash this image.
WARNING: change at 0x42247c+0x4 writes the MPC561 on-chip flash ...
WARNING: change at 0x432940+0x4 writes the MPC561 on-chip flash ...
```

48 patch ranges because many of the blob's and FFCAL001's own bytes are 0xFF,
so they do not change and the ranges around them split. The 11 descriptor
ranges are the sum/~sum words of the **six** affected Bosch blocks, five of
them described by the code table at file 0x0A0000 and one by the calibration
table at 0x1C3300:

| Descriptor | Table entry | Block it covers | Touched by |
|---|---|---|---|
| 0x0A0010 | code #1 | 0x020000-0x02FFFF | the CAN id word at 0x2BD8C |
| 0x0A0200 | code #32 | the block holding 0x12067C | the set-B hook word |
| 0x0A0250 | code #37 | the block holding 0x152000 | the blob |
| 0x0A0300 | code #48 | the on-chip block holding 0x42247C | the fuel hook word |
| 0x0A0310 | code #49 | the on-chip block holding 0x432940 | the set-A hook word |
| 0x1C3340 | cal #4 | 0x5E0000-0x5EFFFF | FFCAL001 |

That the on-chip hook sites land in real, maintained descriptors is the static
half of the on-chip question: the ECU checksums them, so the file is
self-consistent. Whether KESS *writes* them is §1 of `test/procedure.md`.

The sha256 above is of this build; it changes whenever the code, the
calibration or the toolchain changes.

## The state machine

`docs/05_flexfuel_design.md` §3.2, implemented in `ff_tick()` and specified in
`emu/models/flexfuel.py`:

```
power-up          -> FAULT, E0, F = 1024          (and FAULT until the first good frame)
good frame        -> OK,    E_filt filtered towards E_raw, slew-limited to 2 %/s
status 2          -> HOLD,  E_filt and F frozen
status 1 or 3     -> FAULT
E_raw > 100       -> FAULT
counter unchanged for ff_stall_max frames  -> FAULT
no good frame for ff_timeout_ms            -> FAULT
wrong id echo at 0x803F98                  -> FAULT
FAULT             -> hold F for ff_hold_s, then ramp E_filt to ff_e_key at ff_slew
```

The asymmetry is deliberate: on a fault the **fuel** factor is held, never
dropped, so a transient dropout cannot lean the engine out. (The ignition and
rail blends, which must drop to the gasoline map *immediately*, are docs/05
§3.4 / §3.6 and do not exist yet.)

## Tests

* `tests/test_flexfuel_model.py` (58) — the formula, the curve, the fixed-point
  lookup and its clamp, the whole #37 fault matrix, modes 0 and 2, the hook
  arbitration, the FFCAL001 layout against `src/ff_state.h`, and the descriptor
  rows through `tools/draft_to_xdf.py`.
* `tests/test_ff_fuel_patch.py` (41) — the stock facts, the apply, the segment
  stub bit for bit, and **the periodic hook compared with the model tick by
  tick** over hundreds of activations, with the real `can_init_mb` and
  `can_rx_poll` executing and only TouCAN C message buffer 6 modelled (IFLAG
  0x7078A4 bit 6, control word 0x707960, payload 0x707966 — `can.md` §4).
  Nothing is stubbed.
* The E0 proof: `test_f_1024_leaves_rk_untouched` for `rk` in
  {0, 1, 0x7FFF, 0xFFFF}, and `test_one_activation_moves_only_our_own_ram`,
  which diffs the whole 64 KB of SRAM between the stock and the patched image
  after the same hooked site has run.

## Bench

`test/procedure.md` — the on-chip read-back test, the DDLI recipe, the task-set
decision from `ff_src_seen`, the E0 equivalence run, and the **#37
fault-injection matrix** with the expected mode and F per step.
`test/tolerance.json` — the limits for `tools/logcmp.py` on the E0 run.
`logging/sessions/ff_fuel.json` — the variable list, with `patch_offset` on
every `ff_*` entry so `med9log.py log --patch patches/ff_fuel/patch.json`
resolves the addresses from `patch.json` and cannot go stale.
