# ff_fuel — the flex-fuel MVP

Issues **#32** (implementation), the software half of **#37** (FAULT/HOLD
rules), **#39** (VCDS-visible values) and **#38** (E% across power loss). The
first patch of ours that changes what the engine does: it reads the Pico
ethanol frame off a spare CAN receive slot, filters it, and scales the relative
fuel mass `rk` by `F(E)`. At E0 it is **bit-identical to stock** — `F = 1024`
takes an early return and never writes `rk` at all.

Brief **D2** added the second half (2026-09-16): four values in **VCDS
measuring block 111**, and the ethanol estimate kept in **EEP_CONF block 8** so
a battery disconnect does not cost it. Both go through stock code only —
`measuring_result_emit` and `nvm_block_request` — and neither can change what
the engine does.

Brief **E1** added the **ignition blend** (2026-09-17, issue #34 and the
ignition half of #37): `zw += f_zw(E) * dzw_E(nmot, rl)`, produced in the
10 ms tick and consumed by a ten-instruction stub inside `zwgru_build`. It is
the first feature to ship **disabled** — `ff_zw_enable` = 0 in FFCAL001, and
`ff_dzw_map` all zero on top of that — so the flashable file still behaves
exactly like the fuel-only MVP until the human turns one byte on. Its four
values are **VCDS measuring block 108**.

Brief **E2** added the **start enrichment** (2026-09-17, issue #35 and both
#37 rules at once): the cranking quantity is scaled by `f_st(E, tmst)` at the
two words that publish `ksta_adapted`, and the start ignition angle `zwstt`
gets a small ethanol advance. Both ship **disabled** (`ff_st_enable` and
`ff_zwst_enable` = 0) with a neutral `ff_fst_map` and an all-zero
`ff_fzwst_curve`. Its four values are **VCDS measuring block 69**. Two
properties of this feature are worth reading before touching it: the start
runs with the **knock retard bypassed**, so nothing downstream takes an
ignition advance back, and the low-pressure `%ESSTT` twin **shares its store
with its own early-out**, so the S1 stub has to test `B_stend` itself
(`re/findings/start.md` §9.3).

Brief **E5** added the **rail-pressure adder and the injection-window
diagnostics** (2026-09-17, issue #36 trimmed, and the rail flavour of #37):
`prsoll_raw += prail_add(E)` at the one instruction that publishes the
unlimited setpoint, *inside* the stock 110.0 bar ceiling, plus the three
statistics that say whether the pump follows. It ships **disabled**
(`ff_prail_enable` = 0) with an all-zero `ff_prail_curve`. Its four values are
**VCDS measuring block 109** — and two of them, the injection-window margin and
the worst `prist` of the last second, run **whether or not the adder does**,
because `dwi` and `wbho1s` have no stock measuring id at all
(`re/findings/rail.md` §11). The torque limiter of docs/05 §3.6 and any
`KLPRMAX` raise are deliberately **out of scope**; the design note for the
limiter is `test/procedure_e5.md` §6.

> ## Do not flash yet — two blockers, both printed by `make apply`
>
> 1. **`"ram_status": "static"`.** `PATCH_RAM = 0x7FFB00` (0x100 B) is the block
>    `re/findings/ram.md` §8.1 recommends: VERIFIED-STATIC that no instruction
>    in the image references any byte of 0x7FF770-0x7FFFEB, above the task stack
>    0x7FF3C0-0x7FF76F, outside the KWP programming copy 0x804800-0x808687 and
>    outside the protected window 0x7F9E3C-0x7FA47F. The runtime snapshots of
>    **#23** are still pending.
> 2. **Seven of the eight hook words are in the on-chip flash** (0x42247C,
>    0x432940, 0x41D40C, 0x41A680, 0x41A808, 0x431384 and 0x45845C, region
>    0x404000-0x47FFFF). They are covered by the
>    code descriptor table at file 0x0A0000, so `checksum.py fix` handles them
>    and `verify` says ALL OK — but **whether KESSv2 protocol 179 writes that
>    region has never been demonstrated on this ECU**. `test/procedure.md` §1 is
>    the read-back test that answers it, and it is the first thing to do.
>
> `tools/patch_apply.py` refuses the on-chip range unless each change carries
> `"onchip_edit": true` and warns on every apply even when it is there
> (docs/06_patch_pipeline.md §1, added by brief D1).

```bash
cd patches/ff_fuel
make check     # build + assert no r2/r13, no small data, sizes agree
make dump      # disassemble the raw blob (extracts below)
make gen       # regenerate patch.json's `changes` from the blob and FFCAL001
make apply     # -> work/ff_fuel.bin + .diff.json + .sha256
./../../.venv/bin/python3 -m unittest tests.test_ff_fuel_patch \
    tests.test_ff_diag_patch tests.test_ff_ign_patch \
    tests.test_ff_start_patch tests.test_ff_rail_patch \
    tests.test_flexfuel_model
```

`make` here is four lines rather than `ff_counter`'s three: FFCAL001 is data,
built by the patch's own generator (`ffcal001.py`), and it has to exist before
`make gen` reads it. Everything else comes from `patches/common/patch.mk`.

## What it does

| | |
|---|---|
| **Hooks** | eight, one word each — see the table below |
| **Trampoline** | `HOOK_TAIL` for D1's three (`patches/common/hooks.S`): saves LR only, 16-byte frame. E1's, E2's and E5's five are hand-written in `src/hooks.S` and have no frame at all |
| **RAM** | 92 bytes of the 0x100-byte block at `PATCH_RAM` = 0x7FFB00: the **76-byte** state block, `ff_persist_buf` (0x7FFB4C) and `ff_nvm_req` (0x7FFB50). E1 added no RAM at all; E2 added four bytes and **E5 eight more**, all appended past the annex so nothing moved |
| **Flash** | 7,692 bytes at 0x152000 (free area 0x150000-0x1AFFFF, all 0xFF) |
| **Calibration** | FFCAL001 **v4**, 332 bytes at 0x5E2510 (checksum block 0x5E0000-0x5EFFFF) |
| **Stock tables edited** | `tbl_measuring_vars` ids 2196-2199 (0x0A78A8), 2192-2195 (0x0A7898), 2188-2191 (0x0A7888) and 2184-2187 (0x0A7878), 16 B each; `tbl_measuring_groups` groups 111, 108, 69 and 109 (four u16 each) |
| **Stock RAM written** | `rk` 0x803038 (only when F != 1024), `can_rx_shadow` slot 15 (0x803F98-0x803FA3, which nothing else uses) and — through the block manager, never directly — EEP_CONF block 8's mirror byte 0x7F9F80. **The ignition blend writes no stock RAM at all**: it adds its offset to a register inside `zwgru_build`, and the stock `stb` at 0x41D430 is what stores `zwgru`. **E2's three stubs write `ksta_adapted` 0x80302C and `zwstt` 0x802096** — but only because they *are* the stores the stock code would have executed; they replace a `sth`/`stb`, they perform it absolutely, and with both features off the byte pattern written is identical. **E5's stub writes `prsoll_raw` 0x8031F0** on the same terms: it *is* the store the stock code would have executed, it performs it absolutely, and with `prail_add` = 0 the halfword is identical |
| **Stock RAM read** | `nmot_w` 0x7FEE74 and `rl_w` 0x7FEFB2 (the blend's two axis inputs), `dwkrz` 0x7FCE57-0x7FCE5C and the low-octane latch 0x7FD31B (block 108 fields 3 and 4), `tmst` 0x8021F6 (the start map's column axis, and block 69 field 3), `B_stend` 0x7FE921 (the S1 gate), `ksta_adapted` 0x80302C (block 69 field 4) and — **E5** — `wbho1s` 0x80307E, `dwi` 0x803088, the required margin 0x7FD290, `prist` 0x8031DA and the MSV volume 0x80316E, plus the stock calibration word `VMSVMX` 0x5D4BC6 |
| **Stock code called** | `can_init_mb(15)` 0x135750 once, `can_rx_poll(15)` 0x4379C8 per activation, `measuring_result_emit` 0x38EB4 per measuring field, `nvm_block_request` 0x6131C at cold start and at most once a minute |

`ff_fuel` is placed at 0x152000, not 0x150000, so it and `ff_counter` can sit
in one image if that ever becomes useful. They share the same RAM block and are
never flashed together.

### The eight hook sites

| Site | On-chip | Old word | New word | Insns | Task | Tail |
|---|---|---|---|---|---|---|
| **0x42247C** | yes | `4B FF 9F 25` `bl 0x41C3A0` | `4B D2 FB 85` `bl 0x152000` | 20 / 30 | `task_segment_a` 0x4223B0, TCB 5, id 40, **engine-synchronous** | `ba 0x41C3A0` (`rksplit`) |
| **0x432940** | yes | `4B C8 B0 A5` `bl 0x0BD9E4` | `4B D1 F6 E1` `bl 0x152020` | see below | `task_100ms_int` 0x4328E4, id 19, **10 ms, task set A** | `ba 0x0BD9E4` (an empty leaf) |
| **0x12067C** | no | `4B FF E9 B1` `bl 0x11F02C` | `48 03 19 C5` `bl 0x152040` | see below | `task_100ms` 0x1205A0, id 32, **10 ms, task set B** | `ba 0x11F02C` (`clr_ram_7FE889_800E18`) |
| **0x41D40C** (**E1**) | yes | `7C 63 52 14` **`add r3,r3,r10`** | `4B D3 4C 55` `bl 0x152060` | **10** / 6 | `zwgru_build` 0x41D38C, from task 41 (TCB 6, 0x4224BC), **once per ignition event** | none — `blr` to 0x41D410 |
| **0x41A680** (**E2**) | yes | `B3 ED 30 3C` **`sth r31,0x303C(r13)`** | `4B D3 7A 09` `bl 0x152088` | **9** / 14 / 17 / 23 | `esstt_ksta` 0x41A264, from `task_segment_a` 0x4223B0 (TCB 5, id 40), **engine-synchronous** | none — `blr` to 0x41A684 |
| **0x41A808** (**E2**) | yes | `B0 6D 30 3C` **`sth r3,0x303C(r13)`** | `4B D3 78 89` `bl 0x152090` | **8** / 13 / 16 / 22 | `esstt_ksta_hdr` 0x41A68C, from `task_segment_b` 0x4224BC (TCB 6, id 41) | none — `blr` to 0x41A80C |
| **0x431384** (**E2**) | yes | `9B ED 20 A6` **`stb r31,0x20A6(r13)`** | `4B D2 0D 6D` `bl 0x1520F0` | **9** / 16 | `zwstt_build` 0x431294, from **task set A's 10 ms** raster 0x4328E4 at 0x432B04 | none — `blr` to 0x431388 |
| **0x45845C** (**E5**) | yes | `B3 CD 32 00` **`sth r30,0x3200(r13)`** | `bl 0x152134` | **8** / 12 / 13 | `hdrpsol_main` 0x45822C, from set A's **20 ms** task 0x45CAC4 (id 23) at 0x45CC08 | none — `blr` to 0x458460 |

**Seven of the eight are in the on-chip flash** 0x404000-0x47FFFF — every one
except D1's task-set-B raster hook 0x12067C. Each of the seven carries
`"onchip_edit": true` and each makes `make apply` print a warning, so a clean
`make apply` prints **seven** of them. **Their external-flash alternative:**
0x12067C is the set-B twin of 0x432940, so the raster hook has one. The fuel
hook 0x42247C, the ignition hook 0x41D40C, E2's three and E5's have **none** —
`rksplit`, the whole base ignition chain (`re/findings/ignition.md` §1), both
`%ESSTT` twins, `zwstt_build` and `hdrpsol_main` all live in the on-chip
flash, and there is no external-flash site that sees `rk` after its last
writer, `zwgru` before the knock retard, `ksta_adapted` before `gk_rk`
consumes it, `zwstt` before `zwbas_per_bank` substitutes it, or `prsoll_raw`
before the `KLPRMAX` clamp. (`prsoll_raw`'s only other store in the whole
image, 0x132300, is a one-shot initialiser in no task's process list —
`re/findings/scheduler.md` §11.8.) If E6 finds that the OBD route cannot write
0x404000-0x47FFFF, those six features need a BDM flash, not a different site.

**Five of the eight are not call redirects.** The word each replaces is an
ordinary instruction — `add r3,r3,r10` for E1, a `sth`/`stb` for each of E2's
three, and a `sth` for E5's — so there is nothing to tail-branch to: the stub
re-does the instruction itself and `blr`s back to the following word. That is
hook technique **3** of docs/06 §4, and `tools/patch_gen.py` accepts a
non-branch `old` word since 2026-09-17 (the site is still pinned, because
`old` is compared with the stock image before anything is written).

**The one thing about E2's sites that is not obvious from the source.**
`0x41A680` is *not* unreachable once the start is over. `re/findings/start.md`
§3 renders the top of `esstt_ksta` as
`if (B_stend) { 0x80302C = 0x400; return; }`, but the compiler shared the
epilogue: `0x41A2DC li r31,0x400` / `0x41A2E0 b 0x41A680`. The word therefore
runs on every activation of the segment task for as long as the engine runs,
carrying the neutral 1.0 — so **both S1 stubs read `B_stend` 0x7FE921
themselves** and take the untouched path when it is set. That check is also
why their cheapest path (9 and 8 instructions) is the one that runs all day.
The `Insns` column above lists, for E2's two S1 rows: *start over / no valid
state block / `fst_q10` = 1024 / scaling*; and for the Z1 row: *no valid block
(or a `zwst_add` outside 0..8) / adding*. `zwstt_build` **does** branch past
0x431384 on 0x7FECCA, so Z1 needs no gate of its own. Details and the
`find_branch_refs` output: `start.md` §9.3.

Plus **twenty-one** data edits:

| Address | Old | New | What |
|---|---|---|---|
| **0x02BD8C** | `00 00 07 FF` | `00 00 00 EC` | the id word of `tbl_can_rx` slot 15 |
| **0x5E2510** | 332 B of 0xFF | FFCAL001 v4 | the new calibration block |
| **0x0A78A8** | `00 03 8E C4` ×4 | the four handler addresses | `tbl_measuring_vars` ids 2196-2199 (D2) |
| **0x5C55F6** | `00 00` | `08 94` | `tbl_measuring_groups` group 111 field 1 (D2) |
| **0x5C57F4** | `00 00` | `08 95` | field 2 |
| **0x5C59F2** | `00 00` | `08 96` | field 3 |
| **0x5C5BF0** | `00 00` | `08 97` | field 4 |
| **0x0A7898** | `00 03 8E C4` ×4 | the four handler addresses | `tbl_measuring_vars` ids 2192-2195 (**E1**) |
| **0x5C55F0** | `00 00` | `08 90` | `tbl_measuring_groups` group 108 field 1 (**E1**) |
| **0x5C57EE** | `00 00` | `08 91` | field 2 |
| **0x5C59EC** | `00 00` | `08 92` | field 3 |
| **0x5C5BEA** | `00 00` | `08 93` | field 4 |
| **0x0A7888** | `00 03 8E C4` ×4 | the four handler addresses | `tbl_measuring_vars` ids 2188-2191 (**E2**) |
| **0x5C55A2** | `00 00` | `08 8C` | `tbl_measuring_groups` group 69 field 1 (**E2**) |
| **0x5C57A0** | `00 00` | `08 8D` | field 2 |
| **0x5C599E** | `00 00` | `08 8E` | field 3 |
| **0x5C5B9C** | `00 00` | `08 8F` | field 4 |
| **0x0A7878** | `00 03 8E C4` ×4 | the four handler addresses | `tbl_measuring_vars` ids 2184-2187 (**E5**) |
| **0x5C55F2** | `00 00` | `08 88` | `tbl_measuring_groups` group 109 field 1 (**E5**) |
| **0x5C57F0** | `00 00` | `08 89` | field 2 |
| **0x5C59EE** | `00 00` | `08 8A` | field 3 |
| **0x5C5BEC** | `00 00` | `08 8B` | field 4 |

The TKMWL words are generated from the linker symbols (`"u32_syms"` in
`patch.json`, docs/06 §1), so they follow the code instead of going stale; the
four group words are inside the guarded stock calibration and carry
`"calibration_edit": true`.

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

### The RAM block — `struct ff_state` at 0x7FFB00, 76 bytes

`src/ff_state.h` is the authority; `tests/test_ff_fuel_patch.py` and
`logging/sessions/ff_fuel.json` are checked against it.

| Off | Type | Name | Owner | Meaning |
|---|---|---|---|---|
| +00 | u32 | `ff_magic` | tick | `0x46463031` ("FF01") when the block is valid |
| +04 | u16 | `ff_length` | tick | `0x004C` (0x0044 after **E2**, 0x0040 before it) |
| +06 | u16 | `ff_csum` | tick | `~sum16` of the **two core ranges**, +08..+2B then +40..+4B |
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
| **+29** | s8 | `ff_dzw_e` | tick | **E1**: the ignition offset the 0x41D40C stub adds, **0.75 °CA**, positive = advance |
| **+2A** | u16 | `ff_fzw_q8` | tick | **E1**: `f_zw(e_filt)`, **1/256**, 0 when the blend is not producing |
| **+2C** | u32 | `ff_rk_calls` | **rk hook** | segment-task invocations since power-up |
| +30 | u16 | `ff_e_persist` | **D2** | E% staged for / read back from EEPROM block 8 |
| +32 | u8 | `ff_persist_state` | **D2** | 0 idle 1 staged 2 committing 3 done 4 error |
| +33 | u8 | `ff_persist_err` | **D2** | last `nvm_block_request` return code |
| +34 | u16 | `ff_diag_e_pct` | **D2** | `e_filt` in 1 %, for the measuring block |
| +36 | u16 | `ff_diag_f_pct` | **D2** | F in %, `(f_q10 * 100) >> 10` |
| +38 | u16 | `ff_diag_t_degc` | **D2** | fuel temperature, °C + 40 |
| +3A | u16 | `ff_persist_wait` | **D2** | activations left of the commit rate limit |
| +3C | u16 | `ff_persist_writes` | **D2** | commits that finished OK (saturating) |
| +3E | u16 | `ff_persist_fails` | **D2** | commits that failed (saturating) |
| **+40** | u16 | `ff_fst_q10` | tick | **E2**, start fuel factor `f_st`, **1/1024**, 1024..2560 — *core 2, checksummed* |
| **+42** | s8 | `ff_zwst_add` | tick | **E2**, start ignition advance, 0.75 °CA per count, 0..8 — *core 2* |
| **+43** | u8 | `ff_msv_sat_ticks` | tick | **E5**, activations of the current window with `0x80316E` at `VMSVMX` — *the byte E2 reserved for this brief* |
| **+44** | u16 | `ff_prail_add` | tick | **E5**, the rail setpoint adder the 0x45845C stub adds, **0.005 bar** — *core 2* |
| **+46** | s16 | `ff_win_margin_min` | tick | **E5**, the worst `wbho1s − dwi − 0x7FD290×32` of the window, **3/128 °CA** |
| **+48** | u16 | `ff_prist_min` | tick | **E5**, the worst `prist` of the window, **0.005 bar** |
| **+4A** | u16 | `ff_diag_ticks` | tick | **E5**, activations left of the current window |

Above the 76-byte block, still inside the declared 0x100, sit two objects the
state block deliberately does not contain: **`ff_persist_buf` at 0x7FFB4C**,
the one byte a stage copies from, and **`ff_nvm_req` at 0x7FFB50**, the block
manager's 9-byte request record. The manager keeps a *pointer* to that record
in its own queue and dereferences it milliseconds after the call returns
(`re/findings/eeprom.md` §8.2), so it cannot be a stack temporary — and its
layout is the manager's, not ours, which is why it is not part of
`struct ff_state`. Both addresses are in `patch.json`'s `build.symbols`
(`build.ram_symbols`), so a test or a logger reads them rather than assuming
them.

**E1 cost the block nothing.** `ff_dzw_e` and `ff_fzw_q8` are the two fields
D1 reserved at +29 and +2A, so the length is still 0x40, the magic is still
`FF01`, and `ff_persist_buf` and `ff_nvm_req` did not move. Putting them in
the **core** rather than in the annex was deliberate: `ff_dzw_e` is a control
value, it is written only by the periodic tick, and being inside the checksum
means a corrupted offset makes the next activation re-initialise the block
instead of leaving a stale advance in the ignition path for as long as the
engine runs.

Three things about this layout:

* **The header is mandatory, not decoration.** Brief C2 proved the cold start
  does not fill 0x7FF770-0x7FFFEB, so the contents are undefined at power-on.
  `ff_state_init()` runs whenever magic, length or checksum do not describe our
  block, zeroes all 76 bytes and re-seeds them. It clears the block a **word**
  at a time, which is why `FF_LENGTH` has to stay a multiple of four — 0x4C is,
  and a `_Static_assert` in `src/ff_fuel.c` says so.
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
our length 0x004C.

**E2 grew the block, and that is the first time any brief did.** The core was
full at +0x2B and `ff_fst_q10` / `ff_zwst_add` are CONTROL values that three
segment-asynchronous stubs consume, so by E1's argument they belong inside the
checksum. Appending them **past the annex** rather than inserting them keeps
every D1, D2 and E1 offset exactly where it was, at the price of making the
checksummed core two ranges: `ff_core_csum()` sums +08..+2B and then +40..+43,
and `emu/models/flexfuel.py` concatenates the same two slices in the same
order. The length went 0x40 → 0x44, so a block written by the D2/E1 blob is
rejected by the length check and re-initialised on the first activation — the
safe direction, and the same rule FFCAL001's strict version check follows.
`ff_nvm_req` and `ff_persist_buf` moved up by four bytes with the linker;
nothing quotes their numbers, because `build.ram_symbols` pins them by name.

**E5 grew core 2 rather than adding a third range.** Its five fields are all
written by the periodic tick and by nothing else, so extending `FF_CORE2_LEN`
from 0x04 to 0x0C covers every one of them and `ff_core_csum()` still sums
exactly two ranges. `ff_prail_add` belongs in the checksum by E1's argument one
step stronger: the stub that reads it runs in a **different ERCOSEK task** from
the one that writes it (`hdrpsol_main` from set A's 20 ms task id 23, the
producer from set A's 10 ms task id 19 — `scheduler.md` §11.8), so nothing
about the ordering can be relied on and a corrupted byte has to re-initialise
the block rather than sit in the rail-pressure path. The length went
0x44 → 0x4C, so a block written by the E2 blob is rejected and re-initialised —
the safe direction again. **+0x43, the byte E2 reserved for this brief, is
spent on `ff_msv_sat_ticks`**, which is what it was reserved for.

### FFCAL001 v4 at 0x5E2510 — 332 bytes

Built by `ffcal001.py` from `ffcal001.json`; `src/ff_state.h` carries the same
offsets and `tests/test_flexfuel_model.py` asserts the two agree.

**Every version appends and moves nothing.** Everything up to +0xE5 is exactly
where v1 put it; E1's four parameters start at +0xE6, which is where v1's
checksum used to be; E2's eight start at +0x108, which is where v2's checksum
was; E5's five start at +0x122, which is where v3's checksum was; and the
checksum follows the length each time. `ff_cal_ok()` accepts
**the current version only**: a v1, v2 or v3 block flashed under a v4 blob
reads as corrupt, which means mode 0 — `F = 1024`, no CAN, `dzw_e = 0`,
`fst_q10 = 1024`, `zwst_add = 0` and `prail_add = 0` — the safe direction, and
`tests/test_ff_ign_patch.py`, `tests/test_ff_start_patch.py` and
`tests/test_ff_rail_patch.py` all prove it on the applied image.

| Off | Type | Name | Shipped | Unit |
|---|---|---|---|---|
| +00 | 8 B | magic | `FFCAL001` | |
| +08 | u16 | version | **3** | |
| +0A | u16 | length | 290 | B |
| +0C | u16 | `ff_can_id` | 0x0EC | — (documentation; also compared against the id echo at 0x803F98) |
| +0E | u16 | `ff_timeout_ms` | 1000 | ms |
| +10 | u16 | `ff_hold_s` | 60 | s |
| +12 | u16 | `ff_filter_tau_ms` | 3000 | ms |
| +14 | u16 | `ff_slew_pct_s` | 2 | %/s |
| +16 | u16 | `ff_tick_ms` | 10 | ms |
| +18 | u8 | `ff_mode` | **1** | 0 off / 1 normal / 2 bench override |
| +19 | u8 | `ff_e_override` | 0 | % |
| +1A | u8 | `ff_stall_max` | 3 | frames |
| +1B | u8 | `ff_persist_enable` | **1** | **D2**, 0 makes the patch behave exactly like D1's |
| +1C | u8 | `ff_persist_hyst_pct` | 5 | **D2**, % |
| +1D | u8 | `ff_persist_block` | 8 | **D2**, EEP_CONF block |
| +1E | u8 | `ff_persist_offset` | 0 | **D2**, payload offset |
| +1F | u8 | `ff_persist_rate_s` | 60 | **D2**, s |
| +20 | 17×u16 | `ff_F_curve` | formula | 1/1024 over E 0..100 step 6.25 % |
| +42 | 17×u8 | `ff_fzw_curve` | **ramp** | **E1**, 1/256: 0 at E0 rising to 255 at E50, flat above |
| +54 | 8×8 s8 | `ff_dzw_map` | **0** | **E1**, 0.75 °CA; 8 nmot rows × 8 rl columns |
| +94 | 6×6 u16 | `ff_fst_map` | **1024** | **E2**, 1/1024; 6 ethanol rows × 6 `tmst` columns. Row 0 is the E0 row and must stay exactly 1024 |
| +DC | 8×u8 | `ff_prail_add` | 0 | **reserved**, 0.1 MPa |
| **+E6** | u8 | `ff_zw_enable` | **0** | **E1**: 1 applies the blend, 0 keeps `dzw_e` at 0 for ever |
| **+E7** | u8 | `ff_dzw_max` | 8 | **E1**: \|`dzw_e`\| ceiling in counts = 6.00 °CA; code clamps to 16 |
| **+E8** | 8×u16 | `ff_dzw_nmot_axis` | KFZW rows | **E1**, `nmot_w` units: 520, 1000, 2000, 2920, 3720, 4520, 5520, 6520 rpm |
| **+F8** | 8×u16 | `ff_dzw_rl_axis` | KFZW cols | **E1**, `rl_w` units: 10.2, 21.1, 31.3, 41.4, 52.3, 72.7, 93.8, 103.9 % |
| **+108** | u8 | `ff_st_enable` | **0** | **E2**: 1 applies `f_st`, 0 keeps `fst_q10` at 1024 for ever |
| **+109** | u8 | `ff_zwst_enable` | **0** | **E2**: 1 applies the start advance, 0 keeps `zwst_add` at 0 |
| **+10A** | u16 | `ff_fst_max` | 2048 | **E2**: `fst_q10` ceiling = 2.00×; code clamps to 2560 (2.50×) |
| **+10C** | u8 | `ff_zwst_max` | 4 | **E2**: `zwst_add` ceiling in counts = 3.00 °CA; code clamps to 8 |
| **+10D** | u8 | `ff_zwst_tmax` | 117 | **E2**: `tmst` COUNT (39.75 °C) at and above which the advance is 0 |
| **+10E** | 6×u8 | `ff_fst_e_axis` | 0, 20, 40, 60, 85, 100 | **E2**, % — the map's ROWS, shared with `ff_fzwst_curve` |
| **+114** | 6×u8 | `ff_fst_tmst_axis` | 24, 44, 64, 91, 117, 184 | **E2**, `tmst` counts = -30, -15, 0, +20.25, +39.75, +90 °C — the map's COLUMNS |
| **+11A** | 6×s8 | `ff_fzwst_curve` | **0** | **E2**, 0.75 °CA, on the E axis above |
| **+122** | u8 | `ff_prail_enable` | **0** | **E5**: 1 applies the adder, 0 keeps `prail_add` at 0 for ever |
| **+123** | u8 | `ff_prail_rsv` | 0 | **E5**: reserved; it exists only so the two words below stay 2-byte aligned |
| **+124** | u16 | `ff_prail_max` | 3000 | **E5**: `prail_add` ceiling = **15.0 bar**, exactly the headroom `KLPRMAX` 22000 leaves above `KFPRSOLHOM`'s 19000; code clamps to 6000 (30.0 bar) |
| **+126** | u16 | `ff_diag_window_ms` | 1000 | **E5**: the window `win_margin_min`, `prist_min` and `msv_sat_ticks` are accumulated over |
| **+128** | 17×u16 | `ff_prail_curve` | **0** | **E5**, 0.005 bar over E 0..100 step 6.25 % — the same grid as `ff_F_curve` |
| +14A | u16 | crc | | `~sum16` of bytes [0, length-2) |

The v1 reservation `ff_prail_add` at +0xDC is **superseded by
`ff_prail_curve`** and left in place, neutral and unread, so that nothing in
the block moves. It was the wrong shape for the job: eight u8 in 0.1 MPa with
no axis at all, against seventeen u16 in the ECU's own 0.005 bar on the same
ethanol grid the rest of the block already uses. `ff_cal_ok` = 0 (a bad magic,
version, length or checksum) forces mode 0, i.e. `F = 1024`, no CAN,
`dzw_e = 0`, `fst_q10 = 1024` and `prail_add = 0` — the safe direction.

`ffcal001.py` refuses to build a block that would be unsafe rather than
leaving it to the ECU: `ff_F_curve[0] != 1024`, `ff_fzw_curve[0] != 0`, either
curve non-monotonic, an axis that is not strictly increasing (the breakpoint
search assumes it), an `ff_dzw_max` above the 16-count ceiling, an
`ff_fst_map` whose **row 0 is not exactly 1024** (E0 would stop being
bit-identical), any `ff_fst_map` cell below 1024 (`f_st` may only enrich) or
above 2560, an `ff_fst_max` outside 1024..2560, an `ff_zwst_max` or an
`ff_fzwst_curve` above 8 counts, an `ff_fzwst_curve[0]` that is not 0, an `ff_prail_curve[0]` that is
not 0, a non-monotonic `ff_prail_curve`, a curve point or an `ff_prail_max`
above 6000, an `ff_diag_window_ms` of 0, or a non-zero `ff_prail_rsv`.

**Why the E5 code ceiling is the number it is.** `FF_PRAIL_HARD_MAX` = 6000 =
30.0 bar is twice the shipped `ff_prail_max` and twice the whole headroom the
stock calibration has — `KFPRSOLHOM` tops out at 19000 = 95 bar against a
`KLPRMAX` of 22000 = 110 bar — so anything above it is a calibration that lies:
the stock clamp four instructions after the hooked store eats it.

**Why the two E2 code ceilings are the numbers they are.** `FF_FST_HARD_MAX`
is 2560 because that is the largest factor measuring block 69 field 1 can
report without saturating: `(2560 × 100) >> 10 = 250`, which still fits the
`B` byte of formula 0x21 with A = 100. So there is no value the patch can
produce that the tester cannot see. `FF_ZWST_HARD_MAX` is 8 counts = 6.00 °CA,
twice the shipped ceiling and twice the useful range `start.md` §5 gives —
and it matters more than any other clamp in this patch, because during the
start **the knock retard is bypassed** and nothing downstream takes an advance
back.

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

## The ignition blend (E1, #34) — `zw += f_zw(E) * dzw_E(nmot, rl)`

Producer and consumer, the shape `re/findings/ignition.md` §13.2 found the
stock software already using for exactly this job (its low-octane detector
computes an s8 delta in a slow task and `zwbas_per_bank` consumes it
segment-synchronously):

```
10 ms tick            ff_zw_update()  in src/ff_ign.c, from ff_finish()
                        fzw_q8 = f_zw(e_filt)                      1/256
                        d      = ff_dzw_map(nmot_w, rl_w)          s8 counts
                        dzw_e  = clamp(round(fzw_q8 * d / 256),
                                       +-min(ff_dzw_max, 16))

per ignition event    ff_zw_hook  in src/hooks.S, 10 instructions
                        zwgru accumulator += dzw_e
```

**Where it lands, and what still acts on top.** The offset is added at
0x41D40C, i.e. after `KFZW` and every base delta and before

* the stock **s8 clamp** at 0x41D410-0x41D428, which is why an over-large
  offset saturates instead of wrapping;
* the bank offsets and the **knock retard** (`zwbas_per_bank` 0x41D10C);
* `zwmin` and the **-54 … +58.5 °CA output clamp** (`zwout_select` 0x41D464).

`KFZWOP` — the torque model's optimum — is **not** shifted, as docs/05 §3.4
requires. It is instead the budget: `KFZWOP - KFZW` is 1.5 °CA at 1800 rpm /
35 % and about 9 °CA at 4500 rpm / 90 % (`ignition.md` §12), and `dzw_E` has
to stay inside it. `ff_dzw_max` ships at 8 counts = 6.00 °CA and the code
clamps to 16 counts = 12.00 °CA whatever the calibration says.

**Two things are 0 by construction.** `f_zw(E0)` is 0 in every block
`ffcal001.py` will build (it refuses a curve that does not start at 0), and
`ff_dzw_map` is all zero as shipped. Either one on its own makes the offset 0,
and `ff_zw_enable` = 0 makes it 0 a third time.

**The #37 asymmetry, and where it lives.** `ff_zw_update()` is called from
`ff_finish()`, which runs at the end of **every** activation on **every** path
out of `ff_tick()` — the foreign-hook return, mode 0, the bench override and
the normal path. So the activation on which the mode leaves OK/HOLD/OVERRIDE
is already the activation on which `dzw_e` is 0. There is no hold and no ramp,
and there is no branch anyone has to remember to write: putting the producer
in the branch that computes `F` is what would have made it possible to get
wrong. The fuel factor keeps its 60 s hold in the same activation, which is
the asymmetry #37 asks for — a stale-rich mixture is safe, a stale advance is
not.

`dzw_e` is also 0 when `ff_zw_enable` is 0, when `cal_ok` is 0, and when
`e_filt` is 0. `HOLD` keeps computing from the frozen `e_filt`, so the offset
freezes with it rather than dropping.

**Why the consumer trusts only the magic.** The stub is ten instructions and
checks the state block's header word, exactly as `ff_rk_scale` does. What
bounds the value it loads is the producing side (`ff_dzw_max`, then
`FF_DZW_HARD_MAX` in code), the stock s8 clamp immediately after it, and the
output clamp further down; and because `dzw_e` lives inside the checksummed
core, a corrupted byte re-initialises the whole block on the next 10 ms
activation. The alternative — clamping in the stub — would have cost four more
instructions on the per-ignition-event path to re-check a bound that three
other mechanisms already hold.

**The axes are the stock ones.** `ff_dzw_nmot_axis` is every other breakpoint
of `KFZW`'s nmot axis 0x5C7736 and `ff_dzw_rl_axis` is eight of the twelve
breakpoints of its rl axis 0x5C7758, so a cell of `ff_dzw_map` lines up with a
`KFZW` row and column and the `KFZWOP - KFZW` budget table can be read against
it directly. The search is `axis_search_u16_hint`'s arithmetic (0x40C9CC)
rewritten as a fixed-trip-count loop — patch code may not contain a loop whose
trip count is data — and the interpolation is `interp_2d_s8`'s (0x40C3B4),
`val[iy*8 + ix]`. `emu/models/flexfuel.py` carries the same two functions and
`tests/test_ff_ign_patch.py` checks them against `emu/zw_model.py`, which
`tests/test_zw_model.py` checks against the real code in the dump.

**One trap, for whoever writes the next emulated test.** The harness's default
stack top (`emu/core.py` `STACK_TOP` = 0x7FEFFC) is only 0x4A bytes above
**`rl_w` at 0x7FEFB2**, and a cold-start activation uses about 168 bytes of
stack — so a test that seeds `rl_w` and then calls the hook on the default
stack watches the frame overwrite one of the blend's two inputs while it is
being read. The ECU never does this: its one task stack is 0x7FF3C0-0x7FF76F
with r1 = 0x7FF768 (`ram.md` §4.1). Every emulated activation in
`tests/test_ff_ign_patch.py` therefore runs on the real task stack.

## The start enrichment (E2, #35) — `ksta *= f_st(E, tmst)` and `zwstt += dzw_st`

Same producer/consumer split as E1, this time with **three** consumers and two
different stock cells:

```
10 ms tick            ff_start_update()  in src/ff_start.c, from ff_finish()
                        fst_q10  = clamp(interp6(ff_fst_map, e_filt, tmst),
                                         1024, min(ff_fst_max, 2560))
                        zwst_add = clamp(ff_fzwst_curve(e_filt),
                                         0, min(ff_zwst_max, 8))
                                   and 0 at and above ff_zwst_tmax

per segment           ff_st_hook_a  0x41A680   ksta_adapted = min((v * fst_q10) >> 10, 0xFFFF)
per segment           ff_st_hook_b  0x41A808   the same, out of r3 instead of r31
per 10 ms             ff_zwst_hook  0x431384   zwstt = min(stock + zwst_add, 127)
```

**Where each lands.** `ksta_adapted` 0x80302C is what `gk_rk` multiplies into
`rk` at 0x41AA88 while the start path is selected (`start.md` §3.3), so `f_st`
scales the *cranking quantity* and nothing else — and it does so **upstream of
every stock limit that follows**, including the injection window. `zwstt`
0x802096 **replaces the whole per-bank angle** in `zwbas_per_bank` while
`B_stend` is clear (`start.md` §5), so the advance is applied to the one value
that matters during cranking; `%ZWMIN` reads the same cell in this dataset
(`cand_CWZWMN` 0x5C7972 bit 3 is clear), so the minimum-angle floor moves with
it instead of clipping it (`start.md` §9.3).

> **The start runs with the knock retard bypassed.** `zwbas_per_bank`
> substitutes `zwstt` for `zwgru` *and* for `dwkrz`, so a start advance has
> nothing downstream to take it back. That is why `ff_zwst_max` ships at
> 4 counts = 3.00 °CA, why the code ceiling is 8 counts = 6.00 °CA, and why
> `ff_zwst_tmax` switches the whole thing off at and above 39.75 °C: a warm
> start does not need it and a hot one must not have it.

**Three things are 1024 / 0 by construction.** `ff_fst_map`'s row 0 is the E0
row and `ffcal001.py` refuses to build a block whose row 0 is not exactly 1024;
`ff_fzwst_curve[0]` must be 0 for the same reason; and both `ff_st_enable` and
`ff_zwst_enable` ship 0, which pins the two values whatever the tables say.
Any one of them alone makes the feature inert.

**The #37 asymmetry is sharper here than anywhere else**, and it is in one
function:

* `fst_q10` is computed wherever `ff_tick()` computes `f_q10` from `e_filt`
  (OK, HOLD, **FAULT** and OVERRIDE) and is 1024 exactly where `f_q10` is
  forced to 1024 (INIT, OFF). It therefore inherits the 60 s hold and the
  decay towards `e_key` with no rule of its own — a stale-rich crank is safe;
* `zwst_add` is **0 on the activation the mode leaves OK/HOLD/OVERRIDE**. No
  hold, no ramp. `tests/test_ff_start_patch.py::TestProducer::`
  `test_fault_holds_the_fuel_factor_and_drops_the_advance` drives one sequence
  and asserts both halves of that sentence at once.

**The axes.** `ff_fst_tmst_axis` is six of the twelve `KFWKSTT` breakpoints
(0x5C6C62), so every cell of the new map lines up with a stock row: -30, -15,
0, +20.25, +39.75, +90 °C. The dense end is where the stock cranking factor
moves fastest (22.8× at -30 °C against 2.1× at +90 °C, `start.md` §6), and
+39.75 °C is also the shipped `ff_zwst_tmax`, so the temperature at which the
advance switches off is a breakpoint of the fuel map rather than a number
between two of them. `ff_fst_e_axis` is 0, 20, 40, 60, **85**, 100 %.

**Deviation from the brief, with its reason.** There is no `ff_zwst_gain`. The
brief's formula spells `fzwst(e_filt) * ff_zwst_gain`, but its own FFCAL001 v3
append list does not budget an offset for a gain, and a gain multiplying a
free-form six-point curve adds no expressive power — only a second place to
get a start advance wrong, in the one feature where nothing downstream
corrects a mistake. `ff_fzwst_curve` is therefore directly in s8 counts of
0.75 °CA and `ff_zwst_max` is the only ceiling above it.

### The trap in the low-pressure `%ESSTT`, and the gate it forced

`re/findings/start.md` §3 renders the top of `esstt_ksta` as
`if (B_stend) { 0x80302C = 0x400; return; }` and §7 concluded that the hooked
store is unreachable outside the start. The compiler shared the epilogue
instead:

```
0041A2D0  lbz   r12,-0x16cf(r13)   ; B_stend 0x7FE921
0041A2D4  cmpwi r12,0
0041A2D8  beq   0x41a2e4           ; still starting -> the real computation
0041A2DC  li    r31,0x400          ; finished -> the neutral 1.0 ...
0041A2E0  b     0x41a680           ; ... published THROUGH the hooked store
```

So 0x41A680 executes on **every activation of the segment task for as long as
the engine runs**, carrying 0x400. An ungated stub would have multiplied the
ECU's explicit "no start enrichment" by `f_st` at every operating point — the
one thing this feature must never do. Both S1 stubs therefore read `B_stend`
0x7FE921 themselves and take the untouched path when it is set: the same
condition the stock code tests, in the same cell, applied to the *store*
rather than to one path to it. It is also the cheapest path, 9 instructions,
and the one that runs all day.

The high-pressure twin is built differently (`bne 0x41A824` at 0x41A6A4 jumps
past its store) and `zwstt_build` likewise branches past 0x431384 on 0x7FECCA,
so Z1 needs no gate. `tools/find_branch_refs.py data/passat_azx_ori.bin
0x41A680 0x41A808 0x431384 --no-ptr` lists exactly one branch into each word
and is the check `tests/test_ff_start_patch.py` re-runs.


## The rail adder (E5, #36) — `prsoll_raw += prail_add(E)`

The third producer/consumer pair, and the one whose *insertion point* does
more of the safety work than the code does:

```
10 ms tick            ff_rail_update()  in src/ff_rail.c, from ff_finish()
                        prail_add = clamp(interp17(ff_prail_curve, e_filt),
                                          0, min(ff_prail_max, 6000))
                      and, unconditionally, three window statistics

per 20 ms activation  ff_prail_hook  in src/hooks.S, 12 instructions
                        prsoll_raw = min(map_output + prail_add, 0xFFFF)
```

**Where it lands, and what still acts on top.** The hooked word is the `sth`
at 0x45845C, the **single store of `prsoll_raw` 0x8031F0** in the running
engine. The offset therefore goes in after the six-map bank of
`re/findings/rail.md` §3.2 and before

* the **`PRSOLMN` floor** 7000 = 35.0 bar and the **`KLPRMAX` ceiling**
  22000 = **110.0 bar** (§3.3);
* the **pump-volume rate limiter** (§3.4), which is what decides whether a
  raised setpoint is ever actually reached;
* `%HDR`, `%VSTMSV` and `%AMSV`.

and all of them still bind, because the clamp **re-reads the cell from RAM**
four instructions later (`lhz r30,0x3200(r13)` at 0x458480) rather than
re-using the register the store came from. That single instruction is the
whole safety argument: **there is no calibration of this patch that can put
more pressure in the rail than the stock ECU already allows itself.**
`tests/test_ff_rail_patch.py::test_the_ceiling_holds_even_at_the_largest_representable_adder`
drives `prail_add` = 0xFFFF through the real code and asserts it.

**What it is for, and what it is not.** `KFPRSOLHOM` tops out at 19000 =
95 bar, so the headroom is **+15 bar** = +15.8 % pressure = **7.6 % more flow**
at the same `ti`. It is a mixture-preparation and injector-duty measure; E85's
+40 % fuel **mass** comes from `rk` and the F curve (`rail.md` §12.1). That is
also why `ff_prail_max` ships at exactly 3000.

**No gate, and why that is a fact rather than a shortcut.** Three `b` reach
the word (0x458260, 0x4583B4, 0x45843C) and four paths fall through from
0x458458; all seven mean "here is a new setpoint". The one path that must not
be modified — `0x7FD04D & 1`, "hold the previous setpoint" — branches **past**
the store (`bne 0x458460` at 0x45826C), so the stub never runs on it. That is
the exact opposite of E2's 0x41A680, whose early-out branched *into* its store
and forced a `B_stend` test inside the stub, and it is the reason this brief
read the whole control flow rather than the neighbours.

**No mask, either.** `r30` is a clean 0..0xFFFF halfword on every path in:
`interp_2d_u16` (0x40C444) ends with `clrlwi r3,r8,0x10`, the two
`KFPRSOLOFF` paths clamp explicitly to 0 and 0xFFFF, and the `CWPRSOL & 1`
path is an `lhz`. Three independent constructions, all VERIFIED-STATIC
(`rail.md` §12.1).

**The #37 rule, rail flavour.** `prail_add` is 0 on the very activation the
mode leaves OK/HOLD/OVERRIDE — no hold, no ramp — for the same reason
`dzw_e` and `zwst_add` are: the *fuel* factor may be held because a stale-rich
mixture is safe, but a stale pressure demand is not. It is 0 as well when
`ff_prail_enable` is 0, when `cal_ok` is 0 and when `e_filt` is 0, and the
shipped `ff_prail_curve` is all zero on top of that.

### The diagnostics, and why they are half the feature

The dangerous case is the **pump**, not the map (`rail.md` §12.2, §14.4): a
setpoint the pump cannot follow lets `prist` fall, and below `PRWBHMX`
(0x5D3CDC = 2600 = **13.0 bar**) the injection driver's cut-off angle, the
injection-angle clamp and the fault charge limit arm **simultaneously**. So
`ff_rail_update()` also accumulates, on **every** activation and regardless of
whether the adder is enabled:

| Field | What | Unit |
|---|---|---|
| `win_margin_min` | the worst `wbho1s − dwi − 0x7FD290 × 32` of the window | 3/128 °CA |
| `prist_min` | the worst `prist` of the window | 0.005 bar |
| `msv_sat_ticks` | activations with `0x80316E` at `VMSVMX` | count |

**`dwi` and `wbho1s` have no stock measuring id at all** (`rail.md` §11), so
field 3 of group 109 is the first time the injection window can be watched on
a car without a DDLI logger and hand-resolved addresses.

The window is **tumbling with continuous publication**, not sliding: the three
fields show the worst value since the current `ff_diag_window_ms` started and
reset when it expires. A sliding minimum needs a ring buffer of up to 6553
samples and patch code does not get to allocate that; `ff_diag_ticks` is in
the state block so a logger can see where in the window a sample sits, and
`test/procedure_e5.md` says to watch the field for several seconds rather than
to trust one poll.

The required margin is read from **0x7FD290**, the RAM cell `awea_angles`
writes it into, not from the literal 2144 — 2144 is 67 × 32 and 67 is what
`KLWBHO1SMX` holds in *this* dataset. Likewise the saturation threshold is
`VMSVMX` read out of the calibration (0x5D4BC6), not a literal 5000; that is
what `tools/gen_stock_header.py`'s new "stock calibration constants" group is
for.

## Blob disassembly

`make dump` — the raw bytes at the address the CPU will fetch them from, not
the ELF. Re-recorded 2026-09-17 after E2, LLVM 23.1.1,
`PATCH_FLASH=0x152000`, `PATCH_RAM=0x7FFB00`. The three `HOOK_TAIL`
trampolines first (identical but for the call and the tail):

```
                                  ; --- ff_fuel_rk_hook: HOOK_TAIL
00152000  94 21 FF F0  stwu     r1, -0x10(r1)
00152004  7C 08 02 A6  mflr     r0
00152008  90 01 00 0C  stw      r0, 0xc(r1)
0015200C  48 00 0C DD  bl       0x152ce8        ; ff_rk_scale
00152010  80 01 00 0C  lwz      r0, 0xc(r1)
00152014  7C 08 03 A6  mtlr     r0
00152018  38 21 00 10  addi     r1, r1, 0x10
0015201C  48 41 C3 A2  ba       0x41c3a0        ; rksplit
                                  ; --- ff_fuel_hook_a: same, bl 0x152664, ba 0xbd9e4
                                  ; --- ff_fuel_hook_b: same, bl 0x152cc4, ba 0x11f02c
```

and **the ignition stub, which is the whole of the fourth hook** — no frame,
no call, no stock tail:

```
                                  ; --- ff_zw_hook (E1), in place of 0x41D40C
00152060  7C 63 52 14  add      r3, r3, r10     ; the displaced instruction
00152064  3D 60 00 80  lis      r11, 0x80
00152068  81 8B FB 00  lwz      r12, -0x500(r11); 0x7FFB00 = ff_state.magic
0015206C  6D 8C 46 46  xoris    r12, r12, 0x4646
00152070  28 0C 30 31  cmplwi   r12, 0x3031     ; "FF01"?
00152074  4C 82 00 20  bnelr                    ; no valid state -> stock
00152078  89 8B FB 29  lbz      r12, -0x4d7(r11); 0x7FFB29 = ff_state.dzw_e
0015207C  7D 8C 07 74  extsb    r12, r12
00152080  7C 63 62 14  add      r3, r3, r12
00152084  4E 80 00 20  blr                      ; back to 0x41D410
```

Ten instructions, six of them on the path an uninitialised block takes. It
writes r11 and r12 and nothing else: r10 and r1 are untouched, CR0 is written
by the stock `cmpwi r3,0x7f` at 0x41D410 before anything reads it, and the LR
the `bl` clobbered was already dead (`zwgru_build` saved it at 0x41D394 and
reloads it at 0x41D42C). The `@ha`/`@l` pair is the linker's, so `PATCH_RAM`
can move without touching `src/hooks.S`, and the two offsets are `ff_state.h`'s
own — `src/ff_ign.c` `_Static_assert`s them against `struct ff_state`.

The **eight** measuring handlers are ordinary functions at **0x152088**
(`ff_diag_e_pct`), **0x15210C** (`ff_diag_f_pct`), **0x152190**
(`ff_diag_t_degc`), **0x152224** (`ff_diag_mode`) and — E1's — **0x153198**
(`ff_diag_fzw_pct`), **0x153228** (`ff_diag_dzw`), **0x1532A8**
(`ff_diag_dwkrz`) and **0x153310** (`ff_diag_zwlatch`); no trampoline. The
dispatcher enters a handler with `blrl`, so LR already holds the return
address, and a non-leaf C function saves it, calls `measuring_result_emit` and
`blr`s, which is exactly the stock handlers' shape.

and the whole of the segment-synchronous half, which is the only code that runs
per injection:

```
                                  ; --- ff_rk_scale
00152CE8  3C 80 00 80  lis      r4, 0x80
00152CEC  38 64 FB 00  addi     r3, r4, -0x500  ; 0x7FFB00 = PATCH_RAM
00152CF0  80 A3 00 2C  lwz      r5, 0x2c(r3)    ; ff_rk_calls
00152CF4  38 A5 00 01  addi     r5, r5, 1
00152CF8  90 A3 00 2C  stw      r5, 0x2c(r3)
00152CFC  80 84 FB 00  lwz      r4, -0x500(r4)  ; ff_magic
00152D00  6C 84 46 46  xoris    r4, r4, 0x4646
00152D04  28 04 30 31  cmplwi   r4, 0x3031
00152D08  4C 82 00 20  bnelr                    ; no valid state -> stock
00152D0C  A0 63 00 0A  lhz      r3, 0xa(r3)     ; ff_f_q10
00152D10  28 03 04 01  cmplwi   r3, 0x401
00152D14  4D 80 00 20  bltlr                    ; F <= 1024 -> stock, rk untouched
00152D18  28 03 08 00  cmplwi   r3, 0x800
00152D1C  41 80 00 08  blt      0x152d24
00152D20  38 60 08 00  li       r3, 0x800       ; clamp to 2048 in code
00152D24  3C 80 00 80  lis      r4, 0x80
00152D28  A0 A4 30 38  lhz      r5, 0x3038(r4)  ; rk
00152D2C  7C 65 19 D6  mullw    r3, r5, r3
00152D30  54 63 B2 BE  srwi     r3, r3, 0xa     ; (rk * F) >> 10
00152D34  28 03 FF FF  cmplwi   r3, 0xffff
00152D38  41 80 00 0C  blt      0x152d44
00152D3C  3C 60 00 00  lis      r3, 0
00152D40  60 63 FF FF  ori      r3, r3, 0xffff  ; saturate
00152D44  B0 64 30 38  sth      r3, 0x3038(r4)
00152D48  4E 80 00 20  blr
```

Those 25 instructions are **byte for byte what D1 shipped**; D2, E1 and E2
only moved them within the blob. Nothing on the per-injection path changed.

E2's two S1 stubs, which share everything after their first instruction:

```
                                  ; --- ff_st_hook_a (0x41A680, v in r31)
00152088  7F E0 FB 78  mr       r0, r31
0015208C  48 00 00 08  b        0x152094
                                  ; --- ff_st_hook_b (0x41A808, v in r3)
00152090  7C 60 1B 78  mr       r0, r3
                                  ; --- the shared tail
00152094  3D 60 00 80  lis      r11, 0x80
00152098  89 8B E9 21  lbz      r12, -0x16df(r11)   ; B_stend 0x7FE921
0015209C  2C 0C 00 00  cmpwi    r12, 0
001520A0  40 82 00 44  bne      0x1520e4            ; start over -> stock
001520A4  3D 60 00 80  lis      r11, 0x80
001520A8  81 8B FB 00  lwz      r12, -0x500(r11)    ; ff_state.magic 0x7FFB00
001520AC  6D 8C 46 46  xoris    r12, r12, 0x4646
001520B0  28 0C 30 31  cmplwi   r12, 0x3031         ; "FF01"?
001520B4  40 82 00 30  bne      0x1520e4
001520B8  A1 8B FB 40  lhz      r12, -0x4c0(r11)    ; ff_state.fst_q10 +0x40
001520BC  28 0C 04 00  cmplwi   r12, 0x400
001520C0  40 81 00 24  ble      0x1520e4            ; neutral or bad -> stock
001520C4  54 00 04 3E  clrlwi   r0, r0, 0x10        ; the halfword sth stores
001520C8  7D 8C 01 D6  mullw    r12, r12, r0
001520CC  55 8C B2 BE  srwi     r12, r12, 0xa
001520D0  28 0C FF FF  cmplwi   r12, 0xffff
001520D4  40 81 00 0C  ble      0x1520e0
001520D8  39 80 FF FF  li       r12, -1
001520DC  55 8C 04 3E  clrlwi   r12, r12, 0x10      ; saturate at 0xFFFF
001520E0  7D 80 63 78  mr       r0, r12
001520E4  3D 60 00 80  lis      r11, 0x80
001520E8  B0 0B 30 2C  sth      r0, 0x302c(r11)     ; ksta_adapted 0x80302C
001520EC  4E 80 00 20  blr
```

and the Z1 stub, whose unsigned `cmplwi 12, 8` rejects every out-of-range byte
including the negative ones, which is why it needs only the upper s8 clamp:

```
                                  ; --- ff_zwst_hook (0x431384, v in r31)
001520F0  7F E0 FB 78  mr       r0, r31
001520F4  3D 60 00 80  lis      r11, 0x80
             ... magic check, as above ...
             lbz  r12, ff_state.zwst_add (+0x42)
             cmplwi r12, 8 ; bgt -> stock
             extsb r0, r0 ; add r0, r0, r12
             cmpwi r0, 127 ; ble -> store ; li r0, 127
             lis  r11, 0x80 ; stb r0, 0x2096(r11)   ; zwstt 0x802096
             blr
```

and E5's rail stub, which is the only one of the five with **two** stores —
one per path, so that the stock path stores the operand register itself and
cannot disagree with the ECU about what "the value" is:

```
                                  ; --- ff_prail_hook (E5), in place of 0x45845C
00152134  3D 60 00 80  lis      r11, 0x80
00152138  81 8B FB 00  lwz      r12, -0x500(r11)   ; 0x7FFB00 = ff_state.magic
0015213C  6D 8C 46 46  xoris    r12, r12, 0x4646
00152140  28 0C 30 31  cmplwi   r12, 0x3031        ; "FF01"?
00152144  40 82 00 24  bne      0x152168           ; no valid state -> stock
00152148  A1 8B FB 44  lhz      r12, -0x4bc(r11)   ; 0x7FFB44 = prail_add
0015214C  7D 9E 62 14  add      r12, r30, r12      ; both operands <= 0xFFFF
00152150  28 0C FF FF  cmplwi   r12, 0xffff
00152154  40 81 00 08  ble      0x15215c
00152158  39 80 FF FF  li       r12, -1            ; sth keeps the low 16 bits
0015215C  3D 60 00 80  lis      r11, 0x80
00152160  B1 8B 31 F0  sth      r12, 0x31f0(r11)   ; prsoll_raw 0x8031F0
00152164  4E 80 00 20  blr                         ; back to 0x458460
00152168  3D 60 00 80  lis      r11, 0x80
0015216C  B3 CB 31 F0  sth      r30, 0x31f0(r11)   ; exactly the stock store
00152170  4E 80 00 20  blr
```

Sixteen words in the blob; **8 / 12 / 13** instructions executed on the
invalid-block, adding and saturating paths. It writes r11 and r12 and nothing
else — **r30 is never touched**, although 0x458480 reloads it from the cell
anyway — and it uses no stack. It deliberately does **not** re-clamp
`prail_add`: the producing side clamps it twice, it sits inside the
checksummed core so a corrupted byte re-initialises the block on the next
10 ms activation, and `KLPRMAX` bounds the result four instructions later
whatever this stub computes. A fourth check on a path that runs 50 times a
second would buy nothing.

**Neither S1 stub ever writes r31 or r3**, and the Z1 stub does not write r31
either: all three copy the value into r0, which `start.md` §9.2 proves dead at
all three words along with r11, r12, LR and CR0. None of them touches r1, so
none of them uses a byte of stack.

Absolute addressing throughout (`lis 0x80` / `-0x500`, `lis 0x5E` for the
calibration), **no r2 or r13 anywhere** (`--check-sda` OK), no `.rodata`, no
floating point, no 64-bit arithmetic, and every loop is fixed-length.

### The two halves share one word, and only one

The segment task (id 40, priority 0x0A) and the raster task (id 32/19, priority
0x09) are different ERCOSEK tasks, so one can preempt the other. Everything the
segment half reads is a single aligned load:

* `ff_magic` — written only by `ff_state_init()`, and only to the same value;
* `ff_f_q10` — a u16 written with one `sth`, so a `lhz` can never see half of
  an old value and half of a new one. The worst case is one injection segment
  using the previous activation's factor, i.e. a 10 ms delay on a signal whose
  slew limit is 0.02 % per 10 ms.

`ff_rk_calls` is a read-modify-write and can lose an increment to preemption.
It is a diagnostic counter and nothing reads it back, which is why it lives in
the annex and outside the checksum.

## Cost

Measured in the Unicorn harness on the applied image (`--trace` instruction
counts, `tests/test_ff_fuel_patch.py::test_the_cost_of_one_activation` guards
them):

Re-measured 2026-09-17 after **E2**, on the applied image.

| Path | Instructions | Runs |
|---|---|---|
| **segment stub, F = 1024** (E0) | **20** | every injection segment |
| segment stub, F != 1024 | 30 | every injection segment |
| segment stub, no valid state block | 17 | the first milliseconds after power-up |
| **ignition stub, valid state block** | **10** | every ignition event |
| ignition stub, no valid state block | 6 | the first milliseconds after power-up |
| `zwgru_build` end to end, stock → patched | 276 → **286** | every ignition event |
| **S1 stub A, `B_stend` set** | **9** | every segment, all day |
| S1 stub A, no valid state block | 14 | the first milliseconds after power-up |
| S1 stub A, `fst_q10` = 1024 | 17 | every segment of a start, feature off |
| S1 stub A, scaling (saturating) | 23 (25) | every segment of a start, feature on |
| S1 stub **B**, the same four paths | 8 / 13 / 16 / 22 | it falls through instead of branching |
| `esstt_ksta` end to end, stock → patched | 456 → **473** | every segment of a start |
| **Z1 stub, no valid block or a byte outside 0..8** | **9** | — |
| Z1 stub, adding (clamping at +127) | 16 (17) | every 10 ms of a start |
| `zwstt_build` end to end, stock → patched | 196 → **212** | every 10 ms of a start |
| periodic, warm, no frame, blend and start off | 646 | 9 of every 10 activations |
| periodic, warm, fresh frame, blend and start off | 686 | 1 of every 10 activations |
| periodic, warm, no frame, **start on** | 664 | 9 of every 10 activations |
| periodic, warm, fresh frame, **start on** | 704 | 1 of every 10 activations |
| periodic, warm, no frame, **blend on** | 826 | 9 of every 10 activations |
| periodic, warm, fresh frame, **blend on** | 920 | 1 of every 10 activations |
| `ff_zw_update()` alone, blend off | 24 | every activation |
| `ff_zw_update()` alone, blend producing | 237 | every activation |
| periodic, the activation that commits | 1,069 | at most once per `ff_persist_rate_s` |
| periodic, the non-owner hook | 354 | never, unless both task sets run |
| periodic, mode 0 | 411 | — |
| periodic, cold start (init + tick), start off / on | 2,134 / **2,152** | once per power-up |
| one measuring handler, through the dispatcher | 38-79 | only when a tester asks |

The segment figures are D1's and are unchanged, because `ff_rk_scale` is.
E1 added **10 instructions per ignition event** and about 210 per periodic
activation when the blend is producing.

**E2 added 18 instructions per periodic activation** with the start features
on — and the figure does not move with the operating point, because every loop
in `ff_start_update()` has a fixed trip count: the same 664 at
`tmst` 24 with `e_filt` in the first interval and at `tmst` 150 with `e_filt`
at a breakpoint. On the consumer side it added 9 instructions to the segment
task for as long as the engine runs (the `B_stend` gate, and nothing more) and
17 to `esstt_ksta`, 16 to `zwstt_build`, **only during a start**. The
cold-start figure grew by 18 for the same reason plus four more checksum bytes.

They include the real `can_rx_poll` (which has its own critical section), the
real `nvm_block_request` (which has another) and the 48-byte state checksum. At
100 activations per second and 56 MHz that is still **about 0.19 % of the CPU**
for the raster half (0.16 % before E5); the ignition half is 10 instructions at
up to 300 events per second at 6000 rpm, i.e. **0.005 %**, the S1 gate is 9
instructions per segment, i.e. **0.005 %** at the same rate, and the R1 stub is
12 instructions at 50 activations per second, i.e. **0.001 %**. The worst case
is still the cold-start activation, a single 2,441-instruction event. Deepest
stack use is **168 bytes below the task's r1**, on both the cold-start and the
committing activation — the ERCOSEK task stack is 0x3B0 bytes (`ram.md` §4.1,
§5). The ignition stub, **all three E2 stubs and the E5 stub use no stack at
all**. A measuring handler uses 32 bytes, and it runs in the KWP task, not in
the raster.

Reproduce: `tests/test_ff_rail_patch.py::TestRailStub::test_the_cost_of_each_path`
and `::TestProducer::test_the_cost_of_one_activation`,
`tests/test_ff_start_patch.py::TestStubs::test_the_cost_of_each_stub`
and `::TestProducer::test_the_cost_of_one_activation_with_both_features_on`,
`tests/test_ff_ign_patch.py::TestTrampoline::test_the_cost_per_ignition_event`
and `::TestProducer::test_the_cost_of_one_activation`, plus D1's
`tests/test_ff_fuel_patch.py::TestColdStartAndModes::test_the_cost_of_one_activation`.

## Applying it

```
$ make apply
ff_fuel: 159 patch range(s) (7977 B), 18 descriptor range(s) (54 B), 0 unexpected
checksums: ALL OK (65 blocks); identification block unchanged
sha256: c7e82058629c94feb2716d0f518fbc93bfccb8ba0841f71630d645da82035160
WARNING: ff_fuel: "ram_status": "static" - ... Do not flash this image.
WARNING: change at 0x42247c+0x4 writes the MPC561 on-chip flash ...
WARNING: change at 0x432940+0x4 writes the MPC561 on-chip flash ...
WARNING: change at 0x41d40c+0x4 writes the MPC561 on-chip flash ...
```

159 patch ranges because many of the blob's and FFCAL001's own bytes are 0xFF,
so they do not change and the ranges around them split. The 18 descriptor
ranges are the sum/~sum words of the **ten** affected Bosch blocks, eight of
them described by the code table at file 0x0A0000 and two by the calibration
table at 0x1C3300:

| Descriptor | Table entry | Block it covers | Touched by |
|---|---|---|---|
| 0x0A0010 | code #1 | 0x020000-0x02FFFF | the CAN id word at 0x2BD8C |
| 0x0A0100 | code #16 | the block holding 0x0A7888 / 0x0A7898 / 0x0A78A8 | the twelve TKMWL pointers (**D2**, **E1**, **E2**) |
| 0x0A0200 | code #32 | the block holding 0x12067C | the set-B hook word |
| 0x0A0250 | code #37 | the block holding 0x152000 | the blob |
| 0x0A02F0 | code #47 | the on-chip block holding 0x41A680 / 0x41A808 / 0x41D40C | the **start** hook words (**E2**) and the **ignition** hook word (**E1**) |
| 0x0A0300 | code #48 | the on-chip block holding 0x42247C | the fuel hook word |
| 0x0A0310 | code #49 | the on-chip block holding 0x431384 / 0x432940 | the **start-ignition** hook word (**E2**) and the set-A hook word |
| 0x0A0330 | code #51 | the on-chip block holding 0x45845C | the **rail** hook word (**E5**) — the tenth block, and the reason `make apply` now reports 18 descriptor ranges instead of 17 |
| 0x1C3320 | cal #2 | the block holding 0x5C55A2 / 0x5C55F0 / 0x5C55F6 | the twelve group words (**D2**, **E1**, **E2**) |
| 0x1C3340 | cal #4 | 0x5E0000-0x5EFFFF | FFCAL001 |

That the on-chip hook sites land in real, maintained descriptors is the static
half of the on-chip question: the ECU checksums them, so the file is
self-consistent. Whether KESS *writes* them is §1 of `test/procedure.md`.

The sha256 above is of this build; it changes whenever the code, the
calibration or the toolchain changes.

## What a tester sees — measuring block 111 (#39)

`21 6F` over KWP, i.e. VCDS **Engine 01 → Measuring Blocks → group 111**:

| Field | Value | id | Formula | Reads |
|---|---|---|---|---|
| 1 | filtered ethanol, whole % | 2196 | 0x21, A = 100 → the value *is* B | `ff_diag_e_pct` |
| 2 | the fuel factor F, % (100 = 1.000 = stock) | 2197 | 0x21, A = 100 | `ff_diag_f_pct` |
| 3 | fuel temperature, °C | 2198 | 0x05, A = 10 → `B − 100`, CROSS-CHECKED | `ff_diag_t_degc` |
| 4 | `256 × persist_state + mode` | 2199 | 0x36, a plain count | `ff_state.mode` |

Why those slots: 1,507 of the 2,200 TKMWL ids both point at the "not available"
stub 0x38EC4 *and* are named by no group, and ids 2196-2199 are the **last
four**, which makes the edit one contiguous 16-byte range. Group 111 and its
`+0x7F` echo 238 are both entirely empty, so the whole 25-byte answer belongs
to the patch. 108, 69 and 109 were equally free and went to the ignition,
start and rail features. Evidence and the three negative searches:
`re/findings/measuring_vars.md` §8, reproducible with

```bash
./../../.venv/bin/python3 ../../tools/measuring_vars.py \
    ../../data/passat_azx_ori.bin --free
```

A handler checks the block **header only** — not the checksum, which is stale
for the length of an activation — and answers `(0x25, 0, 0)` "not available"
when it does not hold, which is also what field 3 shows before the first frame.
Better than displaying −40 °C.

Rehearse the whole thing without an ECU, against the patched image, through the
firmware's own SID 0x21 route:

```bash
./../../.venv/bin/python3 ../../logging/med9log.py groups --sim \
    --sim-dump ../../work/ff_fuel.bin 111
  field 1: fmt 0x21 A=0x64 B=0x55 -> 85.000 %      [community]
  field 2: fmt 0x21 A=0x64 B=0x9a -> 154.000 %     [community]
  field 3: fmt 0x05 A=0x0a B=0x7d -> 25.000 degC   [crosschecked]
  field 4: fmt 0x36 A=0x00 B=0x01 -> 1.000 count   [community]
```

## What a tester sees — measuring block 108 (E1, #34)

`21 6C`, i.e. VCDS **Engine 01 → Measuring Blocks → group 108**. D2 left 108
free for exactly this (`measuring_vars.md` §8.2), and its `+0x7F` echo 235 is
empty too, so the whole 25-byte answer is the patch's.

| Field | Value | id | Formula | Reads |
|---|---|---|---|---|
| 1 | the blend factor `f_zw`, 0-100 % | 2192 | 0x21, A = 100 | `ff_state.fzw_q8` |
| 2 | the applied offset `dzw_e`, °CA | 2193 | 0x22, A = 0x4B | `ff_state.dzw_e` |
| 3 | the **worst** of the six `dwkrz` bytes, °CA | 2194 | 0x22, A = 0x4B | stock 0x7FCE57-0x7FCE5C |
| 4 | the low-octane latch, `0x7FD31B & 3` | 2195 | 0x36, a plain count | stock 0x7FD31B |

**Formula 0x22 with A = 0x4B is not a choice, it is a copy.** The six stock
per-cylinder knock-retard handlers at 0x039CD0-0x039D48 are literally
`li r3,0x22 ; lbz r5,dwkrz ; li r4,0x4B ; addi r5,r5,0x80`, so fields 2 and 3
are scaled and biased exactly the way VCDS groups 020-024 already are:
`0.01 × 75 × (B − 128)` = **0.75 °CA per count**, which is the ignition
resolution of `ignition.md` §6. (`logging/med9kwp/vag_formulas.py` carries the
generic community label "kW" for 0x22; the arithmetic is right and the unit
string is the published table's, not ours. The brief's fallback of formula
0x36 as a raw signed count was not used because it would make a tester convert
by hand and would not line up with groups 020-024.)

Fields **1 and 2 check the state-block header** and answer "not available"
when it does not hold, exactly as the group-111 handlers do. Fields **3 and 4
do not**: they read stock cells, they are valid whether or not our block is,
and somebody chasing knock has to be able to see them.

Read fields 3 and 4 together with 1 and 2 and the calibration question answers
itself: **advance is only allowed in cells where field 3 stays 0 and field 4
never leaves 0**. `test/procedure_e1.md` is the recipe.

Rehearse it without an ECU:

```bash
./../../.venv/bin/python3 ../../logging/med9log.py groups --sim \
    --sim-dump ../../work/ff_fuel.bin 108
```

With the shipped calibration fields 1 and 2 read 0, because `ff_zw_enable` is
0 — which is exactly what a file that must behave like stock has to show.

## What a tester sees — measuring block 69 (E2, #35)

`21 45`, i.e. VCDS **Engine 01 → Measuring Blocks → group 69**. The wave-E
budget in `docs/agent_briefs/README.md` reserves 69 for E2; its `+0x7F` echo
196 is empty too, so the whole 25-byte answer is the patch's.

| Field | Value | id | Formula | Reads |
|---|---|---|---|---|
| 1 | the start factor `f_st`, 100-250 % | 2188 | 0x21, A = 100 | `ff_state.fst_q10` |
| 2 | the applied start advance, °CA | 2189 | 0x22, A = 0x4B | `ff_state.zwst_add` |
| 3 | `tmst`, whole °C | 2190 | 0x05, A = 10 | stock 0x8021F6 |
| 4 | `ksta_adapted`, **a raw count**, 1024 = 1.00× | 2191 | 0x36 | stock 0x80302C |

**Field 4 is a count and not a percent, on purpose.** The brief asks for a
percent and says to fall back to a count formula if it does not fit — and it
does not: the *stock* cranking factor alone reaches 22.8× = 2280 % at -30 °C
(`start.md` §6) and formula 0x21's `B` is one byte. Formula 0x36 emits
`(A << 8) | B`, so the whole 16-bit cell is reported exactly as the ECU holds
it, with no saturation anywhere in the range, and the number a tester reads is
the number `emu/start_model.py` and the tick-by-tick comparison work in.
Divide by 1024 for the factor.

**Field 1 never saturates either**, and that is what fixes `FF_FST_HARD_MAX`
at 2560: `(2560 × 100) >> 10 = 250`, the largest value the `B` byte of formula
0x21 with A = 100 can carry. There is no `f_st` the patch can produce that
this field cannot show.

**Field 3 converts in the handler.** `tmst` is a u8 count of 0.75 °C with a
-48 °C offset, so the handler emits `(count × 3 + 2) / 4 − 48 + 100` — integer,
rounded to nearest, half **up** (not Python's half-to-even, which is why
`tests/test_ff_start_patch.py` spells the expression out).

Fields **1 and 2 check the state-block header** and answer "not available"
when it does not hold, exactly as the group-111 and group-108 handlers do.
Fields **3 and 4 do not**: they are stock cells, and somebody watching a cold
start has to see them whether or not our block is valid.

Read all four together and the calibration loop closes: field 3 says which
`ff_fst_map` column you are in, field 1 says what the patch asked for, field 4
says what the ECU is actually going to inject with, and field 2 says how much
advance went in — with `test/procedure_e2.md` adding `dwi` (the injection
window) from group 111's neighbourhood and lambda from the stock groups.

Rehearse it without an ECU:

```bash
./../../.venv/bin/python3 ../../logging/med9log.py groups --sim \
    --sim-dump ../../work/ff_fuel.bin 69
```

With the shipped calibration field 1 reads 100 % and field 2 reads 0.00 °CA,
because both enables are 0 — which is exactly what a file that must behave
like stock has to show.


## What a tester sees — measuring block 109 (E5, #36)

`21 6D`, i.e. VCDS **Engine 01 → Measuring Blocks → group 109**. Its `+0x7F`
echo, group 236, is empty too, so the whole 25-byte answer is the patch's.

| Field | Value | id | Formula | Reads |
|---|---|---|---|---|
| 1 | the applied rail adder, **bar** | 2184 | 0x53, the raw word `>> 1` | `ff_state.prail_add` |
| 2 | the **worst** `prist` of the window, bar | 2185 | 0x53, the same | `ff_state.prist_min` |
| 3 | the **worst** injection-window margin, °CA | 2186 | 0x22, A = 225 | `ff_state.win_margin_min` |
| 4 | MSV-saturated activations of the window | 2187 | 0x36, a plain count | `ff_state.msv_sat_ticks` |

**Fields 1 and 2 are a copy, not a choice**, exactly as E1's 0x22/0x4B was.
The stock `prist` and `prsoll` handlers at 0x3DB94 and 0x3DBAC halve the raw
0.005 bar word and emit `(0x53, high byte, low byte)`, so a tester reads
`((A<<8)|B) × 0.01` bar — the same unit, scale and code path VCDS ids 500 and
501 already use, cross-checked against the controller code in
`re/findings/measuring_vars.md` §7.3.

**Field 3 is the one formula/A pair in this patch with no stock precedent**,
and the reason is range. Groups 108 and 69 use A = 0x4B = 0.75 °CA per count,
which spans only −96.00 … +95.25 °CA, while the window margin reaches about
+280 °CA at a light-load 2000 rpm point — the field would sit pinned at its
maximum. **A = 225** gives 2.25 °CA per count and −288.00 … +285.75 °CA, which
covers everything `KFWBHO1SW` (210…330 °CA) can produce; and 2.25 °CA is
exactly **96 angle LSB** of 3/128 °CA, so the handler converts with one exact
integer division. The division **floors**, so the margin a tester reads never
overstates the room there is.

**All four fields check the state-block header**, unlike groups 108 and 69
whose fields 3 and 4 report stock cells directly. Here even the two fields
derived from stock RAM report a *minimum*, which is ours and would be a lie if
it were stale. A tester who sees "not available" reads the stock rail groups
instead — 106 field 1 is `prist`, 231 fields 2 and 3 are `prsoll`/`prist`.

Read all four together and the acceptance test of `rail.md` §14.4 is on one
screen: **field 2 above 13.0 bar always**, **field 4 at 0** on a steady pull,
**field 3 positive**. `test/procedure_e5.md` is the recipe.

Rehearse it without an ECU:

```bash
./../../.venv/bin/python3 ../../logging/med9log.py groups --sim \
    --sim-dump ../../work/ff_fuel.bin 109
```

With the shipped calibration field 1 reads 0.00 bar, because
`ff_prail_enable` is 0 — which is exactly what a file that must behave like
stock has to show.


## E% across power loss (#38)

The estimate is kept in **EEP_CONF block 8, payload +0, one byte**, through
`nvm_block_request` (0x6131C) and nothing else. The raw SPI primitives are
never touched: they would race the manager's mirror and the patch would have to
maintain the block checksum itself.

```c
ff_persist_init()          /* cold start: nvm(blk, off, 1, 1, &buf, 0)      */
                           /*   mode 1 = READ.  With mode 0 the same call   */
                           /*   is the STAGE shape and clobbers the mirror. */
ff_persist_tick(tick_ms)   /* nvm(blk, off, 1, 0, &buf, 0)   stage  -> 2    */
                           /* nvm(blk,   0, 0, 0,    0, &req) commit -> 1   */
                           /*   then poll req.status: 1 busy, 2 done        */
```

Four things keep the EEPROM safe, and all four are calibration:

* **only OK and HOLD write.** A FAULT estimate is a decayed guess and must not
  overwrite what the sensor last reported; mode 2 (bench override) never
  reaches the store at all;
* **`ff_persist_hyst_pct`** = 5 %, clamped to at least 1 in code;
* **`ff_persist_rate_s`** = 60 s, armed at power-up as well as after every
  attempt, so a car that is started and stopped cannot write more than once a
  minute. Worst case is one page write a minute, i.e. ~16,000 engine hours of
  the M95160's 1e6-cycle endurance;
* **one request at a time**: while `persist_state` is BUSY the record belongs
  to the manager and nothing here touches it.

The restore seeds **both `e_filt` and `e_key`**, not just the decay target.
After a key cycle the state block is gone and the machine starts in FAULT with
`hold_ticks` 0, so the first activation decays `e_filt` towards `e_key`:
starting from 0 that would mean running the E0 fuel factor on an E85 tank for
the ~50 s the 2 %/s slew limit needs — lean, the dangerous direction. Starting
from the stored value errs *rich* if the tank was refilled while the car was
off, and the sensor corrects it at the same 2 %/s. A store that reads back 0xFF
or above 100 is ignored and the patch starts at E0.

**There is no key-off commit to piggyback on.** Block 8 has exactly one stock
client and it only ever *stages*; the write-all-blocks routine has no
resolvable trigger; the synchronous-shutdown mode's two setters have no callers
(`re/findings/eeprom.md` §9). Writing while the engine runs is therefore not a
compromise — it is the only route, and it is the better one for the case #38
cares about, because it does not depend on an orderly shutdown at all.

`ff_persist_enable = 0` in the calibration removes every one of these calls;
`tests/test_ff_diag_patch.py` asserts the mirror is byte-identical after 400
activations with it off.

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
dropped, so a transient dropout cannot lean the engine out. The **ignition**
blend does the opposite — `dzw_e` is 0 on the very activation the mode leaves
OK/HOLD/OVERRIDE, with no hold and no ramp, because advance is the dangerous
direction (docs/05 §3.4, the rule of #37). **E2's two halves sit on opposite
sides of the same line**: `fst_q10` follows `e_filt` and therefore inherits
the hold and the decay, while `zwst_add` goes to 0 with the ignition rule —
and the start is the one place where nothing downstream would take a stale
advance back, because the knock retard is bypassed there. **E5's rail adder
follows the ignition rule** as §3.6 said it would: `prail_add` is 0 on the
activation the mode leaves OK/HOLD/OVERRIDE, with no hold and no ramp,
because a stale pressure demand is not safe either — the pump is what runs
out, and below 13.0 bar of rail pressure three interventions arm at once
(`rail.md` §14.3). Three of the four features are now on the "straight to
zero" side of the line and only the fuel factor is held.

## Tests

* `tests/test_flexfuel_model.py` (69) — the formula, the curve, the fixed-point
  lookup and its clamp, the whole #37 fault matrix, modes 0 and 2, the hook
  arbitration, the FFCAL001 **v3** layout against `src/ff_state.h` (including
  that v2 and v3 each appended and moved nothing, that a v1 **or v2** block is
  refused, and that both the `f_zw` curve and the two axes are the ones the
  stock KFZW axes dictate), and the descriptor rows through
  `tools/draft_to_xdf.py`.
* `tests/test_ff_fuel_patch.py` (41) — the stock facts, the apply, the segment
  stub bit for bit, and **the periodic hook compared with the model tick by
  tick** over hundreds of activations, with the real `can_init_mb` and
  `can_rx_poll` executing and only TouCAN C message buffer 6 modelled (IFLAG
  0x7078A4 bit 6, control word 0x707960, payload 0x707966 — `can.md` §4).
  Nothing is stubbed.
* `tests/test_ff_diag_patch.py` (37) — D2's half over five layers: the stock
  facts behind the slot choice, the two table edits on the applied image,
  `measuring_var_dispatch` per id against the model, **`21 6F` end to end
  through `logging/ecu_sim.py` on the patched image**, and the E% store through
  the real `nvm_block_request` with the QSPI left as the emulator's zero stub.
* `tests/test_ff_ign_patch.py` (51) — E1's half over six layers: the stock
  facts (the `add` at 0x41D40C is not a branch, the clamp that follows it, the
  free TKMWL and group slots with the three negative searches, the latch bits,
  and the stock knock handlers whose formula fields 2 and 3 copy); the apply;
  **`zwgru_build` on the patched image against the untouched dump** over the
  grid `test_zw_model.py` uses; the trampoline on its own (where it returns,
  what it leaves in r10/r1, what an invalid block makes it do); the producer
  against the model tick by tick across OK → FAULT → OK; and group 108 through
  the dispatcher and through `ecu_sim`'s `21 6C`.
* `tests/test_ff_start_patch.py` (61) — E2's half over seven layers: the stock
  facts (each of the three words is a store and not a branch, the two `%ESSTT`
  twins publish from **different registers**, each site's task and single
  caller, **the one branch that reaches each hooked word** — including the
  early-out that forced the `B_stend` gate — the 114 consecutive `bl` that put
  our producer before the Z1 consumer in the same activation, the free TKMWL
  and group slots with the three negative searches, and `%ZWMIN` reading
  `zwstt` itself); the apply; **`esstt_ksta` and `zwstt_build` on the patched
  image against the untouched dump** over a `tmst` grid, bit for bit at the
  neutral calibration and `min((v × f) >> 10, 0xFFFF)` / `min(stock + a, 127)`
  with the values forced; the three stubs on their own with their exact
  instruction counts; the producer against the model tick by tick, including
  one sequence that asserts the fuel factor is **held** and the advance
  **dropped** on the same activation and the `tmst` gate on both sides of
  `ff_zwst_tmax`; and group 69 through the dispatcher and through `ecu_sim`'s
  `21 45`.
* `tests/test_ff_rail_patch.py` (53) — E5's half over seven layers: the stock
  facts (the `sth` at 0x45845C is not a branch, the three `b` that reach it and
  the `bne` that jumps **past** it, `hdrpsol_main`'s single caller, the `lhz` at
  0x458480 that makes the `KLPRMAX` clamp re-read the cell, `interp_2d_u16`'s
  closing `clrlwi`, the `_savegpr_25` tail that puts LR in the caller's frame,
  the free TKMWL and group slots with the three negative searches, and the flat
  `KLWBHO1SMX`); the apply; **`hdrpsol_main` on the patched image against the
  untouched dump** over 7 × 5 × 5 × 7 = 1,225 operating points and every
  mode-bit path — bit for bit at `prail_add` = 0, `min(stock + add, 0xFFFF)`
  with it forced, and `0x8031F2` never past 22000 even at `prail_add` = 0xFFFF;
  the stub alone with its exact instruction counts and its register set; the
  producer against the model tick by tick, including the FAULT drop **on the
  same activation the fuel factor is held** and the three window statistics
  over a synthetic `wbho1s`/`dwi`/`0x80316E`/`prist` stream; the two whole-SRAM
  proofs; and group 109 through the dispatcher and through `ecu_sim`'s `21 6D`.
* The E0 proof: `test_f_1024_leaves_rk_untouched` for `rk` in
  {0, 1, 0x7FFF, 0xFFFF}, and `test_one_activation_moves_only_our_own_ram`,
  which diffs the whole 64 KB of SRAM between the stock and the patched image
  after the same hooked site has run (E2 repeats it in
  `test_one_activation_still_moves_only_our_own_ram` with **both** start
  features enabled at a real calibration, and separately proves that each of
  its three stubs writes only the one cell it publishes). Its D2 counterpart is
  `test_a_handler_writes_only_the_three_result_bytes`, which does the same for
  a measuring handler: a tester cannot disturb the control path. Its **E1**
  counterpart is `test_the_whole_ignition_event_moves_no_ram_outside_our_block`,
  which runs task 41's `bl 0x41D38C` to completion on stock and on patched —
  once with the blend disabled and once with it enabled at the neutral map —
  and finds `zwgru` bit-identical and no SRAM byte moved outside our block.
  Its **E5** counterpart is
  `test_one_setpoint_activation_moves_nothing_outside_our_block`, which runs
  `hdrpsol_main` to completion on stock and on patched — once with the adder
  disabled and once with it enabled at the neutral curve — and finds the four
  setpoint cells bit-identical and nothing moved outside our block.

## Bench

`test/procedure.md` — the on-chip read-back test, the DDLI recipe, the task-set
decision from `ff_src_seen`, the E0 equivalence run, and the **#37
fault-injection matrix** with the expected mode and F per step.
`test/procedure_d2.md` — what VCDS group 111 must show (#39), the
non-destructive read of EEP_CONF block 8 **before** flashing, and the
**battery-disconnect test** that is the acceptance criterion of #38.
`test/procedure_e1.md` — what group 108 shows with the blend off and on, the
knock-logging recipe that calibrates `dzw_E` from +0 towards +2 °CA only in
cells where `dwkrz` stays 0, and the `KFZWOP - KFZW` budget table as the hard
ceiling (#34).
`test/procedure_e2.md` — the **cold-start logging recipe** (`tmst`,
`ksta_adapted`, `f_st`, injections and ignitions since start, lambda, `dwi`,
start time), how to calibrate `ff_fst_map` from a start series beginning at
1.2× warm and never past the injection window, the `zwstt` limits, and what
group 69 must show (#35).
`test/procedure_e5.md` — the **13.0 bar hard stop**, the "pump saturated"
decision rule (`0x80316E` pinned at `VMSVMX`, `0x8031F6` at 0), the first
injection-window margin table this project can produce at all, the +2 bar
calibration recipe up to the 15 bar of headroom `KLPRMAX` leaves, what group
109 must show, and a half-page **design note** for a window-following torque
limiter at the min-chain 0x0C7CF8 (#36).
`test/tolerance.json` — the limits for `tools/logcmp.py` on the E0 run.
`logging/sessions/ff_fuel.json` — the variable list, with `patch_offset` on
every `ff_*` entry so `med9log.py log --patch patches/ff_fuel/patch.json`
resolves the addresses from `patch.json` and cannot go stale.
