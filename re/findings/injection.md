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
