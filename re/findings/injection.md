# Injection path: relative fuel mass `rk` -> injection time `ti`

Agent B6, brief `docs/agent_briefs/B6_injection_path.md`, issue #14.
Date: 2026-09-15. Dump `data/passat_azx_ori.bin`, SHA-256
`b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`
(`python3 tools/checksum.py verify -q` -> ALL OK (65 blocks)).

Tags as in `docs/agent_briefs/00_common_rules.md`:
**VERIFIED-STATIC** = read out of the bytes of the dump,
**VERIFIED-DYNAMIC** = reproduced by the emulator,
**COMMUNITY** = outside knowledge (FR, forums),
**HYPOTHESIS** = our inference.

## 0. Working environment (reproduce)

```bash
mkdir -p /tmp/ghidra_B6
cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_B6/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B6 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
# -> 198 functions named, 168 labels created, 3 skipped (0x400000, 0x702000,
#    0x707000 have no memory block; they are the un-dumped / peripheral aliases)
```

Read-only decompilation from the command line (safe in parallel with other
agents, it opens the project read-only):

```bash
./.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B6 --project-name med9 0x<addr>
```

---

## 1. Result in one table

Everything here is **VERIFIED-STATIC** unless marked otherwise: it is read out
of the disassembly of the named address, with `r2 = 0x5C9FF0` and
`r13 = 0x7FFFF0` (the application SDA bases, `re/findings/scheduler.md` §7).

| What | Address | How it was found |
|---|---|---|
| **`rk2ti` — the `rk x frt` multiply, the flex-fuel lever** | **`mullw r31,r4,r5` at 0x0AC39C**, in `rk2ti` 0x0AC370 | see §3 |
| `fkkvs_func` — computes `frt`, `fcorr`, `tv` per injection | 0x0AC4B8 | see §4 |
| `rkti_pre` — per-injection-type driver of `fkkvs_func` | 0x455040 | callers of 0x0AC4B8 |
| `rkti_dp_angle` — `dp` from `prist` and the back-pressure curve | 0x0AC42C | callers |
| `rksplit` — splits `rk` over the injections | 0x41C3A0 | writer of 0x8030B4-0x8030BE |
| `aes_ti_out` — applies `rk2ti`, sums `ti`, sets the "ti at minimum" flag | 0x41C4BC (+ 0x41C730) | writers of 0x8030C4 |
| Engine-synchronous task A (ERCOSEK TCB 5, id 40, prio 0x0A) | 0x4223B0 | callers of 0x41C4BC; B1 table |
| Engine-synchronous task B (ERCOSEK TCB 6, id 41, prio 0x0A) | 0x4224BC | callers of 0x41C730 |
| **`KRKATE`** injector constant, u16 scalar = **3858** | **0x5D3DBC** | `lis r3,0x5D; lhz r3,0x3DBC(r3)` at 0x0AC528 |
| **`KLTIKRPR`** rail-pressure/flow correction, 12 x u16 | **0x5C72F8** (axis 0x5D3DBE) | `subi r3,r2,0x2CF8` at 0x0AC4F8 |
| shared `dp` breakpoint axis, `{u16 n=12; u16 axis[12]}` | **0x5D3DBE** | `addi r3,r3,0x3DBE` at 0x0AC4E8 |
| **`TVUB`-equivalent** injector dead time, 12 x s16, same axis | **0x5C7310** | `subi r29,r2,0x2CE0` at 0x0AC590 |
| **`FKKVS`** rail-pulsation correction, 8x8 u16 map | **0x5C71F8** | `subi r3,r2,0x2DF8` at 0x0AC564 |
| **`KLHDEV`** injector master curve, 10-point u16 | **0x5C729C** | `subi r3,r2,0x2D54` at 0x0AC3C4 |
| **`TIMINP`** minimum injection time, u16 = **900** | **0x5C7328** | `lhz r12,-0x2CC8(r2)` at 0x0AC410 |
| **`KLPBR`** cylinder back-pressure curve, 12 points | **0x5C72C6** (axis 0x5C72C8, values 0x5C72E0) | 0x0AC458-0x0AC468 |
| `rk` — relative fuel mass for the segment, u16 | RAM 0x803038 | `lhz r12,0x3048(r13)` at 0x41C3B8 |
| `ti` total (the VCDS group 002.3 value), u32 | RAM 0x8030C4 | measuring handler 0x03EA3C |
| `nmot` (u16, 1 LSB = 0.25 min^-1) | RAM 0x7FEE74 | `lhz r5,-0x117C(r13)` at 0x0AC56C, FKKVS x axis |
| `prist` rail pressure actual (u16) | RAM 0x8031DA | 0x45505C, 0x0AC460 |

## 2. How the chain was entered: the measuring variable

VCDS measuring-block **group 002** is `{1, 2, 595, 10}`
(`re/findings/measuring_groups.txt`), i.e. nmot, rl, **595**, ml. A3 had
already tagged fields 002.2 (load) and 003.2 (air mass) COMMUNITY, so field
002.3 is the community "injection time". Handler **0x03EA3C**:

```
0003EA3C  lwz    r3,0x30D4(r13)      ; RAM 0x8030C4, a 32-bit variable
0003EA40  cmplwi r3,0xFE01           ; clamp at 255*255
0003EA54  sth    r3,0x2AE2(r13)
0003EA58  rlwinm r12,r3,0,16,31
0003EA5C  li     r4,0xFF
0003EA60  divw   r5,r12,r4           ; B = ti / 255
0003EA64  li     r3,0x16             ; VAG display formula 0x16
0003EA68  b      0x00038EB4
```

So **`ti_sum` is RAM 0x8030C4** (VERIFIED-STATIC for the RAM address and the
handler; COMMUNITY for "this is what VCDS labels injection time").
Its only writers are `aes_ti_out` (0x41C4BC) and 0x41C730 — that is the
entry point into the whole chain.

## 3. `rk2ti` at 0x0AC370 — the conversion, instruction by instruction

Signature (EABI): `u16 rk2ti(u32 inhibit, u16 rk_i, u16 frt, u16 tv, u16 fcorr)`
in `r3..r7`.

```
0AC380  cmpwi  r3,0                 ; inhibit != 0 -> return 0
0AC39C  mullw  r31,r4,r5            ; *** rk_i * frt ***            <-- THE POINT
0AC3A4  rlwinm r31,r31,23,9,31      ; ti_raw = (rk_i * frt) >> 9
0AC3A8  cmplw  r31,0xFFFF           ; r5 = min(ti_raw, 0xFFFF)
0AC3C0  sth    r5,0x30F0(r13)       ; RAM 0x8030E0 = ti_raw, clamped
0AC3C4  subi   r3,r2,0x2D54         ; 0x5C729C = KLHDEV
0AC3CC  bl     0x40EFAC             ; lookup_1d_u16(KLHDEV, ti_raw)
0AC3D0  mullw  r3,r3,r30            ; KLHDEV(ti_raw) * fcorr
0AC3D8  rlwinm r30,r3,17,15,31      ; >> 15   (both are Q15)
0AC3DC  ble    0AC3EC               ; overflow-safe split:
0AC3E0    rlwinm r31,r31,24,8,31    ;   ti_raw >>= 8
0AC3E4    li r29,7                  ;   and shift 7 afterwards
0AC3EC  else li r29,15              ;   otherwise shift 15
0AC3F0  mullw  r31,r31,r30
0AC3F8  srw    r3,r31,r29           ; ti = ti_raw * KLHDEV * fcorr >> 30
0AC3FC  bl     0x40FCB8             ; saturating add of tv
0AC400  or.    r30,r3,r3 ; ble -> 0 ; negative -> 0
0AC410  lhz    r12,-0x2CC8(r2)      ; 0x5C7328 = TIMINP = 900
0AC418  blt    0AC420               ; ti = max(ti, TIMINP)
```

In closed form, with `KLHDEV` and `fcorr` both Q15 (0x8000 = 1.0):

```
ti_raw = (rk_i * frt) >> 9                       # u16, saturated
ti     = ((ti_raw * KLHDEV(ti_raw) * fcorr) >> 30) (+sat) tv
ti     = max(ti, TIMINP)                         # 0 if inhibit
```

The two-branch shift (`>>8` then `>>7`, versus `>>15`) is only overflow
avoidance; it is not a different scaling, and it costs one bit of `ti_raw`
above 0xFFFF.

## 4. `fkkvs_func` at 0x0AC4B8 — where `frt`, `fcorr` and `tv` come from

`void fkkvs_func(u16 rk_i, u16 dp, u16 *tv_out, u16 *frt_out, u8 use_fkkvs, u16 *fcorr_out)`
in `r3..r8`.

```
0AC4D0  sth  r4,0x30DE(r13)         ; RAM 0x8030CE = dp (kept for diagnosis)
0AC4D8  lwz  r5,-0x2A38(r13)        ; RAM 0x7FD5B8 = previous axis key (hint)
0AC4E8  addi r3,r3,0x3DBE           ; 0x5D3DBE = the dp axis {n=12; axis[12]}
0AC4EC  bl   0x40C9CC               ; axis_search_u16_hint(axis, dp, hint)
0AC4F0  stw  r3,-0x2A38(r13)        ; keep the key: tv reuses it below
0AC4F8  subi r3,r2,0x2CF8           ; 0x5C72F8 = KLTIKRPR value array (u16)
0AC50C..0AC524                      ; inline linear interpolation on that key
0AC528  lhz  r3,0x3DBC(r3)          ; 0x5D3DBC = KRKATE = 3858
0AC530  bl   0x00410084             ; frt = min((KRKATE * KLTIKRPR) >> 14, 0xFFFF)
0AC534  cmpwi r27,0                 ; use_fkkvs == 0 -> fcorr = 0x8000 (1.0)
0AC544  mullw r11,r31,r27           ; rk_i * frt              (the same product)
0AC548  rlwinm r31,r11,23,9,31      ; ti_eff = (rk_i * frt) >> 9
0AC560  sth  r31,0x30F2(r13)        ; RAM 0x8030E2 = ti_eff (the FKKVS y input)
0AC564  subi r3,r2,0x2DF8           ; 0x5C71F8 = FKKVS, 8x8 u16
0AC56C  lhz  r5,-0x117C(r13)        ; RAM 0x7FEE74 = nmot (the x input)
0AC570  bl   0x0040D2F8             ; lookup_2d_u16(FKKVS, ti_eff, nmot)
0AC584  sth  r31,0(r29)             ; *fcorr_out
0AC588  sth  r30,0(r28)             ; *frt_out
0AC58C  lwz  r31,-0x2A38(r13)       ; the *same* dp key again
0AC590  subi r29,r2,0x2CE0          ; 0x5C7310 = dead-time value array (s16)
0AC5A4..0AC5BC                      ; inline linear interpolation
0AC5C0  sth  r28,0(r26)             ; *tv_out
```

Consequences worth stating plainly:

* `frt = min((KRKATE * KLTIKRPR(dp)) >> 14, 0xFFFF)` — **`KRKATE` is a plain
  u16 scalar at 0x5D3DBC and 0x0AC528 is its only reference in the image**
  (see §7).
* The **dead time is a curve over `dp`**, not over battery voltage, and it
  **shares the `KLTIKRPR` breakpoint axis** 0x5D3DBE (the key is computed once
  and reused). The FR's `KLTVTSV` is therefore a `dp` curve in this dataset.
* `FKKVS` is indexed by `(ti_eff, nmot)` exactly as the FR describes, and
  `ti_eff` is computed with the *same* `(rk_i * frt) >> 9` expression that
  `rk2ti` uses — so a factor applied to `rk` or to `frt` moves the FKKVS
  operating point consistently, which is what we want.

## 5. The calibration data, read out of the dump

`xxd -s <file offset> -g 2 data/passat_azx_ori.bin`; CPU = file + 0x400000 in
this window (`tools/med9lib.py`).

### 5.1 `dp` axis 0x5D3DBE (file 0x1D3DBE), n = 12

`400, 800, 1200, 1800, 2800, 4200, 6120, 8180, 11340, 15000, 20000, 24000`

### 5.2 `KLTIKRPR` 0x5C72F8 (file 0x1C72F8), 12 x u16

`28344, 20029, 16605, 13558, 10854, 8868, 7369, 6373, 5415, 4710, 4088, 3736`

**`value * sqrt(dp)` is 566,900 +- 1 % across all twelve points** (566880,
566500, 575200, 575200, 574300, 574700, 576500, 576400, 576600, 576900,
578100, 578800). The curve is `k / sqrt(dp)`, i.e. the Bernoulli flow law
through a fixed orifice. That is independent proof that the axis input is a
**pressure difference** and that the value is a **time per unit fuel mass**.

### 5.3 Dead time 0x5C7310 (file 0x1C7310), 12 x s16, same axis

`950, 750, 587, 457, 362, 316, 340, 318, 329, 398, 407, 451`

### 5.4 `FKKVS` 0x5C71F8 (file 0x1C71F8), self-describing 8 x 8 u16

* y axis (`ti_eff`): `500, 1000, 2000, 3000, 4000, 5000, 6000, 7000`
* x axis (`nmot`):   `3200, 4000, 6000, 8000, 12000, 16000, 20000, 24000`
  (/4 = 800 .. 6000 min^-1, the same convention B5 found)
* values: 0x8000 .. 0x8666, i.e. **1.000 .. 1.050 in Q15** — a small
  multiplicative correction, as a fuel-rail pulsation term should be.

### 5.5 `KLHDEV` 0x5C729C (file 0x1C729C), self-describing 10-point u16

* axis (`ti_raw`): `550, 700, 850, 920, 1000, 1500, 2500, 3500, 4850, 6500`
* values: `32004, 32437, 32777, 32819, 32769, 32553, 32232, 32012, 31661, 31425`
  = **0.9767 .. 1.0016 in Q15** — the small-quantity linearisation.

### 5.6 `TIMINP` 0x5C7328 (file 0x1C7328), u16 = **900**

Also used by `aes_ti_out` as the "injection time is at its minimum" flag
(`ti == 0x5C7328` at 0x41C500 and the three sibling tests).

### 5.7 `KLPBR` 0x5C72C6 (file 0x1C72C6), self-describing 12-point

* axis: `0, 640, 1408, 2048, 2688, 3456, 4096, 4864, 5504, 6144, 6912, 7552`
* values: `27392, 20352, 9856, 5504, 3456, 2304, 1664, 1280, 1152, 1152, 1152, 1152`

Read by `rkti_dp_angle` (0x0AC42C) as
`dp = clamp(prist - ((KLPBR(x) * y) >> 17), 0, 0xFFFF)`, the
angle-dependent back-pressure of the FR's `KLPBR`/`KFPBRA`.

The five objects 0x5C729C, 0x5C72C6, 0x5C72F8, 0x5C7310, 0x5C7328 are
**contiguous and exactly adjacent** (42 + 50 + 24 + 24 + 2 bytes), which is
the independent check that the two bare value arrays really have 12 entries.

## 6. The multiplication point for the flex-fuel factor

### 6.1 The candidates, and why `rk` wins

| Candidate | Where | Verdict |
|---|---|---|
| **A. `rk` in RAM, 0x803038, between `gk_rk_out` and `rksplit`** | hook the `bl 0x41C3A0` at **0x42247C** in the segment task | **recommended** |
| B. the `mullw r31,r4,r5` at 0x0AC39C inside `rk2ti` | the arithmetic point itself | works, but `rk2ti` is a shared leaf with seven call sites (at most three of them fire in any one segment) and its registers are all live; a hook there has to be an in-place instruction rewrite, not a `bl` |
| C. `KRKATE`, the u16 scalar at 0x5D3DBC | a pure calibration change | perfect coverage, zero code — but it is a **constant**: it cannot follow E%, and `frt` is only recomputed in a slow raster (§6.4) |
| D. `frt` in RAM, 0x8030D2/D4/D6/D8 | after `rkti_pre` | four cells instead of one, and the slow-raster latency of C |
| E. `ti` (the FR's warning about `%UFRKTI`) | after `rk2ti` | not needed here, see §7 |

**Why A.** `rk` at RAM 0x803038 is written by exactly one instruction and read
by exactly seven, all of them inside `rksplit`:

```bash
python3 tools/callgraph.py data/passat_azx_ori.bin --xref-store 0x803038 0x803038
# ... or the r13-relative index used for this brief:
#   0x41ADD4  sth r6,0x3048(r13)   <- the only writer  (gk_rk_out)
#   0x41C3B8 0x41C3E0 0x41C3F4 0x41C408 0x41C440 0x41C454 0x41C4B0  (rksplit)
```

**VERIFIED-STATIC.** Every per-injection quantity that `rk2ti` ever sees
(0x8030B4, 0x8030B6, 0x8030B8, 0x8030BA, 0x8030BC, 0x8030BE) is derived from
that one cell inside `rksplit`, under exactly the same `0x803092` type bits
that `aes_ti_out` tests before calling `rk2ti`. So a factor applied there
reaches **homogeneous, both split modes and the start injection** — every
injection mode the software has.

The seeding writes of `FUN_00454EA0` (0x454ED4, 0x454F2C, 0x454F54, 0x455004,
0x45502C) do **not** escape this: they only run for a type whose bit is set in
0x803094 (requested) but clear in 0x803092 (current), i.e. never in the segment
whose `ti` is actually used, and `rksplit` runs before `aes_ti_out` in the task
(0x42247C before 0x422480).

### 6.2 The hook site

```
00422478  4B FF 89 6D  bl 0x0041ADE4   ; gk (writes rk -> 0x803038)
0042247C  4B FF 9F 25  bl 0x0041C3A0   ; rksplit          <-- replace this word
00422480  4B FF A0 3D  bl 0x0041C4BC   ; aes_ti_out (up to 3 x rk2ti, 7 call sites)
00422484  4B FF 95 3D  bl 0x0041B9C0   ; awea (ti -> crank angle, window limits)
```

* **Task:** `FUN_004223B0`, ERCOSEK TCB entry 5, **task id 40, priority 0x0A**
  (B1's table in `re/findings/scheduler.md` §4). It is the
  **segment-synchronous** task: it is the only caller of `aes_ti_out`, and its
  sibling `FUN_004224BC` (TCB 6, id 41) is the only caller of 0x41C730.
  Neither is one of the 10/20/100/1000 ms rasters.
* **Registers:** the task body is a flat list of **argument-less `bl`s**, so
  B1's §7 argument applies unchanged — **r3-r12, CR, CTR, XER are all dead**
  across 0x42247C, LR is free (the stub is entered with `bl`), and r1 is the
  task's own stack. The stub must not touch r2 (0x5C9FF0), r13 (0x7FFFF0) or
  r14-r31.
* **Patch shape:** `bl <ff_rk_scale>` in place of `bl 0x41C3A0`; the stub does
  `rk = min((rk * F) >> 10, 0xFFFF)` on RAM 0x803038 and ends with
  `b 0x41C3A0` so the original call still happens. One word changed.
* **Checksum:** 0x42247C is in the on-chip flash, covered by the 54-entry code
  descriptor table at file 0x0A0000, so `python3 tools/checksum.py fix` is
  required after the patch (`verify` must then print `ALL OK (65 blocks)`).

### 6.3 Fixed-point format of the value being scaled

`rk` is an **unsigned 16-bit** integer; `rksplit` and `gk_rk_out` both saturate
it at 0xFFFF. It carries no sign and no implicit fraction of its own — the
whole scaling lives in `KRKATE` and `KLTIKRPR` — so the factor is a plain
unsigned multiply. `docs/05_flexfuel_design.md` §4 specifies `ff_F_curve` in
**1/1024**, which fits: `rk_new = min((rk * F_q10) >> 10, 0xFFFF)` and
`F_q10 = 1024` is bit-identical to stock (asserted by
`tests/test_injection_model.py::test_flex_fuel_factor_1_is_a_no_op`).

**Headroom (HYPOTHESIS, from the calibration's own axes).** `ti_raw =
(rk * frt) >> 9`, `frt ~ 1370` at 100 bar, and the calibrated `ti_raw` range is
bounded by the `KLHDEV` axis (550..6500) and the `FKKVS` y axis (500..7000).
That puts the working `rk` at roughly 200..2700, i.e. a factor of 24 below the
0xFFFF saturation — an E100 factor of 1.63 cannot overflow it. The stub should
still clamp, because `rk` itself saturates at 0xFFFF in over-run/fault states.

### 6.4 If a calibration-only experiment is wanted first

`KRKATE` (0x5D3DBC, u16, **3858**) has **exactly one reference in the whole
image** — `lhz r3,0x3DBC(r3)` at 0x0AC528, with the `lis r3,0x5D` at 0x0AC51C
(`tools/callgraph.py --xref-store 0x5D3DB0 0x5D3DD8` finds that one and the
axis pointer at 0x0AC4E8, nothing else). So multiplying it by a fixed factor
is the minimal, reversible, code-free way to fuel a fixed blend on the bench
(E85 -> 3858 * 1.50 = 5787), covering every injection mode including start.
It is in the calibration block 0x5C0000-0x5FFFFF, so `checksum.py fix` applies
here too.

Its one drawback for the real feature is timing: `frt` is produced by
`rkti_pre` (0x455040), which the image calls **only** from `task_1000ms_int`
(`bl 0x455040` at 0x45CCEC), and the start-injection variant `FUN_00430974`
only from `task_100ms_int` (`bl 0x430974` at 0x432B00). The FR calls this the
"time-synchronous part" of `%RKTI`, so the split is by design — but it means a
factor applied through `frt`/`KRKATE` would follow E% with that raster's
latency, while a factor applied at `rk` acts on the very next segment.
(B1 flagged the exact periods of the two on-chip slow rasters as an open item;
whatever they are, `rk` is the faster path.)

## 7. The level-2 monitor: A2's warning does not bind here

A2's FR index (`re/findings/fr_index.md` §7) warns that `%UFRKTI` recomputes
an allowed fuel mass, so a flex-fuel patch should act on `rk` rather than on
`ti`. On **this** binary the constraint is weaker than feared, and the reason
is worth recording:

* Nothing outside the injection chain reads the computed `ti`. The four
  per-injection results 0x8030E4/E6/E8/EA are read only by `awea`
  (0x41B9C0) and 0x41C000/0x41C12C/0x41C178/0x41C1D0, and the total
  `ti_sum` 0x8030C4 only by the measuring handler 0x03EA3C and by
  `FUN_00432BDC`, which copies it to 0x802316 for diagnosis.
* Nothing outside `rksplit` reads `rk` (0x803038) — see §6.1.
* The monitoring-style function that *does* look at fuel mass,
  `FUN_00455C60` (0x455C60-0x4565EF, called from the same slow raster, using
  the coarse 8-bit `nmot` at 0x7FCE95 and its own 8-bit maps in 0x5D46xx /
  0x5D47xx), reads the **pre-`ZGST`** values `min(0x803030, 0x80303A)` and
  `min(0x803032, 0x803034)` — i.e. the bank values *before* the final
  cylinder-balancing multiply that produces 0x803038.

So a factor applied at 0x803038 is downstream of every consumer except
`rk2ti`: the monitor keeps seeing the gasoline fuel mass. That is the desired
behaviour for the MVP (no DTC), and it is also the thing to re-examine before
anyone trusts the monitor afterwards — it will no longer be monitoring the
fuel that is actually injected. **VERIFIED-STATIC** for the reference sets;
**HYPOTHESIS** that `FUN_00455C60` is the EGAS level-2 fuel path (its shape
says monitor; the FR page `UFRKTI` p3922 was not matched instruction by
instruction).

## 8. The `ti` limits and the injection window

* **Minimum:** `TIMINP` = u16 at **0x5C7328 = 900**, applied unconditionally at
  the end of `rk2ti` (0x0AC410-0x0AC41C). `aes_ti_out` compares the result
  against the same cell to raise the "injection time is at its minimum" flag
  (0x7FEA54 / 0x7FEA55). It is also read at 0x0ECE4C.
* **Maximum:** there is **no `ti` maximum** in `rk2ti`; the only ceilings in
  the arithmetic are the 0xFFFF saturations. The real limit is the
  **injection window, enforced in the angle domain** by `awea`
  (`FUN_0041B9C0`, 0x41B9C0-0x41BD7B), which converts
  `dwi = (ti * k_nmot[0x803072]) >> 13` (stored at 0x803088) and then clamps
  the start/end angles with the u8 window scalars **0x5D396D** and **0x5D396E**
  (both used as `value * 0x20`; **both are 0x00 in this dataset**, so today the
  limit is carried entirely by the runtime terms 0x7FD28E / 0x7FD290 and the
  constant `0x2300`). `awea` is the `bl 0x41B9C0` at 0x422484
  (`4B FF 95 3D`), immediately after `aes_ti_out`.
* Consequence for the flex-fuel work: raising `rk` by 50 % raises `dwi` by
  ~50 % and will hit that window before it hits any `ti` clamp — which is
  exactly what `docs/05_flexfuel_design.md` §3.6 predicted. 0x803088 (`dwi`)
  is the quantity to log.

## 9. Upstream: where `rk` itself comes from (`GK`), for briefs B7/B8

`gk_rk` = `FUN_0041AA48` (0x41AA48-0x41ADE3), called from 0x41AE5C and
0x41AF14 inside `FUN_0041ADE4`, which is the `bl` at 0x422478 — the
instruction before the hook site. It computes both banks and publishes the
one belonging to the current segment. Bank-A path, in order
(**VERIFIED-STATIC** from the decompilation; the FR labels are **COMMUNITY**):

| Step | Operation | RAM |
|---|---|---|
| base | `(0x801CF2 * 0x80302C) >> 7` (or a 3-term product in the second mode) | -> 0x80302E |
| fuel/air | `* 0x7FED38 >> 11` | |
| additive | `+ (s16)0x8030F8` | -> 0x80303E (x2) |
| per-injection normalisation (mode-dependent) | `(x << 12) / 0x80304A` | |
| **`fr`** closed-loop lambda factor, Q15 | `* 0x802DF8 >> 15` (bank B: 0x802E00) | |
| **`fra`** additive adaptation | `+ (s16)0x801D1A` | |
| **`frm`** multiplicative adaptation, Q15 | `* 0x801E36 >> 15` (bank B: 0x801E28) | -> 0x803034 / 0x803032 |
| component/diagnostic subtraction (skipped when 0x8033FA & 4) | `- 0x80315C` | -> 0x803030 / 0x80303A |
| **`ZGST`** per-cylinder balancing, Q15 | `* u16 0x801D8C[cyl] >> 15`, index from 0x7FEF7E / 0x7FEF80 | -> **0x803038 = `rk`** |

The four RAM cells 0x802DF8 / 0x802E00 (measuring ids 29 / 28, VCDS group
001.3 / 001.4) and 0x801E36 / 0x801E28 (measuring ids 33 / 34, VCDS group
032.2 / 032.4) are the lambda controller output `fr_w` and the multiplicative
adaptation `frm_w` per bank — **VERIFIED-STATIC** that they multiply `rk` in
Q15 at those instructions, **COMMUNITY** for the names. That closes the
`fra`/`frm` item of `docs/05_flexfuel_design.md` §7 on the fuel side.

> **Added 2026-09-17 (E3, #41): where the *lambda setpoint* enters this chain
> — and that it is not a map.** The "base" row above, `(0x801CF2 × 0x80302C)
> >> 7`, carries it: 0x801CF2 is `fgru_trim`, the Q7 base-mixture factor, and
> its only producer is the four-line `FUN_000E8D9C`:
> `fgru_trim = mul_shr15_sat(cand_KFGRUTRIM 0x5D350C = 128, 0x7FD066 × 64 +
> 0x6000)`, clipped at 255 — no table, no breakpoint search. The request
> therefore arrives as the single byte **0x7FD066**, whose only reader is
> 0x0E8DA8 and for which neither `tools/sda_xref.py --var` nor
> `tools/find_abs_refs.py --target` finds a writer, i.e. it is written
> through a pointer. Tracing that store is the cheapest remaining route to
> `%LAMSOLL` / `lamsbg_w`; `re/findings/calibration_names.md` §9.5 lists the
> other two.

> **Added 2026-09-22 (F4, #41): that store is the tester, and this chain has
> no lambda setpoint at all.** 0x7FD066 is **adaptation channel 10** of the
> table at 0x0A3AD8: `kwp_adaptation_service` 0x038708 writes it through
> `**(byte **)(&DAT_000a3ad8 + 40)` after clamping it between 26 (0x5C6085)
> and 179 (0x5C6084), and `adaptation_restore_all` 0x12E3F8 reloads it from
> EEP_CONF block 8 index 11 at every power-up. Default 128, so `fgru_trim` is
> exactly 1.0 unless a tester has moved it; the reachable range is
> **0.797 … 1.094**. E3's "the request arrives as the single byte 0x7FD066"
> was the right trace and the wrong conclusion: **no request arrives there**.
>
> The rest of the chain above is now complete in the same sense. Of the four
> multiplicative terms, `fgru_trim` is a tester constant, `0x80302C`
> (`ksta_adapted`) and `mixture_running` are the start/warm-up cascade
> (`calibration_names.md` §10.3, whose two `%LAMSOLL`-shaped maps
> `cand_KFMIXA` / `cand_KFMIXB` are **all 128**, i.e. λ = 1 everywhere), and
> `fr`/`fra`/`frm` are the closed loop — whose own setpoint 0x802CDE is
> computed by `lam_ist_from_rk` 0x43E164 **from 0x80303E / 0x80303A, i.e.
> from this chain's own output**. So the loop tracks whatever `rk` asks for.
> **There is no full-load or component-protection enrichment on the fuel path
> of this dataset**, and an ethanol factor at the `rk` hook of §3 is not
> fighting a hidden one. `calibration_names.md` §10.2 has the exclusion with
> the commands.

## 10. Verification: the Python model

`emu/models/injection.py` is a bit-exact model of `rk2ti` (0x0AC370),
`fkkvs_func` (0x0AC4B8) and `rkti_dp_angle` (0x0AC42C), reading every
calibration constant out of the image rather than hard-coding it.

```bash
./.venv/bin/python -m emu.models.injection            # print the constants and a sweep
./.venv/bin/python -m emu.models.injection --verify   # compare against the ECU code
./.venv/bin/python -m unittest tests.test_injection_model -v
```

`--verify` runs the three real functions in the A5 Unicorn harness over a grid
of inputs (rk 0..65535, frt 1..65535, tv, fcorr, dp 0..65535, nmot 0..40000,
`use_fkkvs` 0 and 1, both `rk2ti` shift branches, the inhibit path, the
`TIMINP` clamp and both saturations) and compares bit for bit:

```
all 4061 cases match
```

**VERIFIED-DYNAMIC** (emulator). Two things fell out of getting it exact:

* **No FPU anywhere in this chain.** Every step is 32-bit integer
  `mullw`/`rlwinm`/`srw`/`divwu`; there is no `lfs`/`stfs` in 0x0AC370-0x0AC5C4
  and none in the helpers it calls. The A5 note about FPU routines does not
  apply here.
* `tv` reaches `rk2ti` in r6 as a **zero-extended** u16 (every caller uses
  `lhz`), even though the dead-time array is `s16` and is interpolated with
  `lha`. A negative dead time would therefore act as a huge positive one. No
  point of the shipped curve is negative (950 .. 316), so this is latent.

## 11. Open items

| Question | Status |
|---|---|
| Physical unit of `ti`. `TIMINP = 900`, the `KLHDEV` axis 550..6500 and the `FKKVS` y axis 500..7000 are all consistent with **1 LSB = 1 us** (0.9 ms minimum, 7 ms full scale), but that is HYPOTHESIS. The VCDS handler divides by 255 and uses display formula 0x16 with A = 0xFF, which we have not decoded. | open — one logged drive with VCDS group 002 settles it |
| Physical unit of `dp` / `prist`. The axis runs 400..24000 and the values obey `k/sqrt(dp)` exactly, so it is a pressure; **0.01 bar/LSB** (4..240 bar) is the natural reading but unproven. | open — log the rail pressure measuring block against the RAM cell 0x8031DA |
| Absolute scaling of `rk` (hence `KRKATE` in ms/%) | open — follows from the two above |
| Exact periods of `task_100ms_int` (0x4328E4) and `task_1000ms_int` (0x45CAC4), which drive `frt` | **SETTLED 2026-09-16 (C4, #44): 10 ms and 20 ms** (the symbol names are wrong and kept only for cross-reference). VERIFIED-STATIC from the activation chain — ERCOSEK alarm 1 has a 35087-tick = 10 ms cycle and activates 0x4328E4; the /2 counter of the divider chain 0x40BEF0 that 0x4328E4 runs activates 0x45CAC4 — and VERIFIED-DYNAMIC from `emu/os_clock.py`. `scheduler.md` §11. Option C/D latency is therefore at most one 20 ms period, not one second |
| Is `FUN_00455C60` really the EGAS level-2 fuel monitor (`%UFRKTI`)? | **SETTLED — it is not (2026-09-23, G4, calibration_names.md §11.5).** It is the purge-fuel block of `%TEB`: its output **0x80315C is `rkte_w`**, the canister fuel that `gk_rk` subtracts from `rk` (the "component/diagnostic subtraction" of §9), clamped to [`FRKTEMN` −0.08, `FRKTEMX` +0.50] × `rk`; the 8-bit maps are `KFFTEVFX` / `FTEVFXHM` / `FTEVFXS` (the maximum purge-valve opening, selected by the `bdemod_w` mode bits). §7's conclusion still holds — nothing reads the post-`ZGST` `rk` — but its reading of this function as a monitor does not |
| Battery-voltage dependence of the dead time. In this dataset `tv` is a curve over `dp` only. A `TVUB`-style voltage term may live in the output stage (`KT_ES`) rather than in `%RKTI`. | open — B7/B9 territory |

---

## 12. Correction (2026-09-23, G4, issue #41): 0x80315C is the purge fuel `rkte`, not a diagnostic term

§7 read `FUN_00455C60` as the level-2 fuel monitor and §9 listed
`- 0x80315C` as a "component/diagnostic subtraction". Brief G4 decompiled the
function while naming its calibration (`calibration_names.md` §11.5):

* it computes the maximum purge-valve opening from `KFFTEVFX` (engine speed ×
  a pressure ratio) and, in the lean and stratified modes, `FTEVFXHM` /
  `FTEVFXS`, passes the purge mass through a transport delay and two mixing
  filters, and writes **0x80315C = the fuel that arrives through the canister
  purge**, clamped to `FRKTEMN` × `rk` … `FRKTEMX` × `rk` = −8 % … +50 %;
* `gk_rk` subtracts exactly that value from the bank fuel mass (skipped when
  0x8033FA bit 2 is set), so the injected fuel is the demand *minus* the purge
  fuel — the FR's `rkte_w`. VCDS measuring id 171 displays it (formula 0x14,
  a percentage).

For flex fuel this matters in one way: the purge fuel is modelled as
*gasoline*. With E85 in the tank the canister vapour is ethanol-rich, so the
subtraction is slightly wrong in the rich direction during purge; the lambda
controller has to absorb the error while purge is active (HYPOTHESIS: how large it gets on E85 is a bench question — log id 171 against the lambda controller output).
**VERIFIED-STATIC** for the dataflow and the clamp; the `%TEB` labels are
`static` where the FR's inputs and mode split match (see the sidecar rows).
