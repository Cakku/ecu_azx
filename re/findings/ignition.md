# Ignition chain: ZWGRU / KFZW, the final `zw`, and knock retard

Agent B7, brief `docs/agent_briefs/B7_ignition.md`, issue #15.
Date: 2026-09-15. Dump: `data/passat_azx_ori.bin` (03H906032 / 1037382557,
SHA-256 `b15590d3…09b3`), unchanged — `tools/checksum.py verify -q` prints
`ALL OK (65 blocks)` before and after.

Ghidra project: a private copy of the shared `ghidra_projects/med9.*` at
`/tmp/ghidra_B7`, with the 369 merged symbols applied by `import_symbols.py`.
Every decompilation below is reproducible with

```bash
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B7 --project-name med9 <address>
```

---

## 0. Quick reference

| What | Address | Note |
|---|---|---|
| **`KFZW`** base ignition map | **0x5C75FE** | 16 nmot x 12 rl, s8, 0.75 °/LSB (§2) |
| `KFZW` nmot axis (`SNM16ZUUW`) | 0x5C7736 block / 0x5C7738 data | 16 u16, 0.25 rpm/LSB, 520-6520 rpm |
| `KFZW` rl axis (`SRL12ZUUW`) | 0x5C7758 block / 0x5C775A data | 12 u16, 100 %/4096 per LSB, 10.2-103.9 % |
| **`KFZW2` / `KFZWLB*` / `…OUT`** | — | **do not exist in this dataset** (§2.1) |
| **`KFZWOP`** torque-model optimum | **0x5CA3F1** | 16 nmot x 11 rl, s8; axes 0x5CA3D6 / 0x5CA3E6 (§9). Never shift it |
| `zwgru` base-angle sum | `FUN_0041d38c` 0x41D38C | **insertion point: the word at 0x41D40C** (§11) — **TAKEN by `patches/ff_fuel` 2026-09-17, §11.4** |
| per-bank angle + knock retard | `FUN_0041d10c` 0x41D10C | writes 0x7FD30B / 0x7FD30C (§4, §7) |
| **`zwdelta_load`** load/temperature term | 0x7FD338, by `FUN_00459334` | s8, 0.75 °/LSB, added in 0x41D10C after `zwgru`; **shares the advance budget with the ethanol offset** (§14, added 2026-09-23 G2) |
| ZWMIN / ZWSEL / ZWOUT | 0x41D440 / 0x41D464 | clamp -54 ° .. +58.5 ° (§8) |
| output driver | `FUN_0041cd9c` 0x41CD9C | `zw * 15/2` → 0.1 ° for the TPU stage |
| **`dwkrz`** per-cylinder knock retard | **0x7FCE57 … 0x7FCE5C** | 6 bytes, firing order 1-5-3-6-2-4, VCDS groups 020-024 (§5) |
| knock controller | `FUN_00416d6c` 0x416D6C | `%KRREG`; entered from 0x416374 ← 0x417BCC (§13) |
| ignition angle shown by VCDS | 0x7FEF87 / 0x7FEF88 | group 003 field 4, measuring var id 9 |
| everything runs in | ERCOSEK **task id 41**, entry 0x4224BC | TCB entry 6 at 0x47870C (§10) |
| **fixed point** | **s8, 0.75 °CA per LSB** | +1 ° = +1.333 counts (§6) |

## 1. Where the ignition code lives

The whole base-ignition and knock-control chain sits in the **on-chip flash**
(`INT_FLASH`, CPU 0x404000-0x480000, file 0x200000+), not in the external
flash. That is the same region as the seven ISR tasks B1 found at
0x41707C-0x4170DC, and it is where the segment-synchronous (crank-angle)
code lives.

## 2. The base ignition map — KFZW at 0x5C75FE (VERIFIED-STATIC)

`FUN_0041d334` (0x41D334) is the `%ZWGRU` base-map lookup:

```c
void FUN_0041d334(void)      /* zwgru_kfzw_lookup */
{
  DAT_007fd5ec = axis_search_u16_hint(&DAT_005c7736, DAT_007fee74, DAT_007fd5ec);
  DAT_007fd5f0 = axis_search_u16_hint(&DAT_005c7758, DAT_007fefb2, DAT_007fd5f0);
  interp_2d_s8 (&DAT_005c75fe, DAT_005c7758, DAT_007fd5ec, DAT_007fd5f0);
}
```

| Object | Address | Shape | Evidence |
|---|---|---|---|
| **KFZW** value array | **0x5C75FE** (file 0x1D75FE) | 16 rows (nmot) x 12 cols (rl), **s8**, 192 B | `interp_2d_s8` call at 0x41D378 |
| nmot axis block | 0x5C7736 | `{u16 n=16; u16 bp[16]}`, values 0x5C7738 | `axis_search_u16_hint` at 0x41D34C |
| rl axis block | 0x5C7758 | `{u16 n=12; u16 bp[12]}`, values 0x5C775A | `axis_search_u16_hint` at 0x41D360 |

Axis breakpoints, raw and converted:

```
nmot (0x5C7738, 16 pts, 1/4 rpm per LSB):
  2080 2880 3360 4000 6080 8000 10080 11680 13280 14880 16480 18080 20000 22080 24000 26080
=  520  720  840 1000 1520 2000  2520  2920  3320  3720  4120  4520  5000  5520  6000  6520 rpm

rl (0x5C775A, 12 pts, 100 % / 4096 per LSB):
   416  640  864 1056 1280 1696 2144 2560 2976 3424 3840 4256
= 10.2 15.6 21.1 25.8 31.3 41.4 52.3 62.5 72.7 83.6 93.8 103.9 %
```

That is exactly the FR's `SNM16ZUUW` (16-point speed) x `SRL12ZUUW`
(12-point relative load) pair that `KFZW` is declared over
(FR p3085-3094, p3090-3091 ABK table).

Map contents (s8, **0.75 °CA per LSB**, see §6), row = nmot, col = rl:

```
      10.2 15.6 21.1 25.8 31.3 41.4 52.3 62.5 72.7 83.6 93.8  104 %rl
 520    47   37   32   27   23   19   15    9    3    2    1    0
 720    51   44   39   29   24   23   17   10    4    3    2    1
 840    54   50   42   37   32   26   18   12    6    4    3    2
1000    56   55   48   40   36   31   23   17   10    8    7    6
1520    57   56   50   43   38   35   30   22   16   13   11    9
2000    59   58   51   46   41   37   33   27   22   18   16   13
2520    60   59   52   47   43   38   35   31   27   24   22   21
2920    61   60   53   48   44   39   37   32   29   27   26   25
3320    62   61   54   49   46   40   38   33   31   29   28   27
3720    62   62   55   51   47   42   38   34   32   31   29   27
4120    62   62   56   52   48   46   39   35   33   32   30   28
4520    62   62   55   52   50   46   40   36   33   32   30   29
5000    63   62   57   53   52   47   42   37   34   33   31   30
5520    63   62   59   55   54   50   44   38   35   34   33   32
6000    63   62   61   57   53   48   44   38   36   35   34   33
6520    63   62   61   57   53   47   43   37   35   34   33   32
```

i.e. 47.25 °BTDC at low load / high speed down to 0 ° at 520 rpm full load —
the shape of a naturally-aspirated base ignition map.

**Indexing convention** (from `re/findings/calibration_maps.md` §1.3):
`interp_2d_s8(val, nx, key_y, key_x)` reads `val[iy*nx + ix]`, so with
`nx = 12` the row stride is the **rl** axis and the row index is **nmot**.

### 2.1 No KFZW2 / KFZWLB blend in this dataset (VERIFIED-STATIC, negative)

The FR describes `zwgru` as a linear interpolation between `KFZW` and `KFZW2`
by the inlet-camshaft factor `fwnwe`, with a second pair `KFZWLB1/KFZWLB2`
selected by the swirl flap, and a duplicated `…OUT` set for exhaust-cam
control (FR p3086-3087, `NW_NORM_POS` / `NW_KAT_POS`).

**This software has exactly one base map.** `0x5C75FE` is the only value array
that is read through the 0x5C7736 / 0x5C7758 axis pair, `FUN_0041d334` is the
only caller of that lookup, and `FUN_0041d38c` (§3) adds deltas to its result
without any second map or blend factor. `re/calibration_draft.csv` lists no
other `map_2d*` row with `x_axis_addr = 0x5C775A` or `y_axis_addr = 0x5C7738`.
So `SY_NWS`/`SY_LBK` are compiled out here and the `KFZW/KFZW2` blend of the
2004 TFSI FR does not exist in the 2006 3.2 FSI dataset.

*Consequence for flex fuel:* the "blend two maps" pattern of
`docs/05_flexfuel_design.md` §3.4 has no stock vehicle in this ECU. The
ethanol offset has to be an **added term** in the chain of §3.

## 3. The base-angle sum — `zwgru` at 0x41D38C (VERIFIED-STATIC)

```c
void FUN_0041d38c(void)      /* zwgru_build */
{
  if (DAT_007fea72 != '\0') {              /* recompute enable */
    DAT_007fd316 = FUN_0041d334();         /* KFZW(nmot, rl)            */
    DAT_007fd314 = FUN_0041d280();         /* delta map 0x5C753E, below */
  }
  iVar1 = (int)DAT_007fd316;
  if ((DAT_005c753c & 0x80) == 0)          /* codeword bit 7 */
    iVar1 += (s8)MEM_0x800006 - (s8)MEM_0x800007;   /* lbz 0x16/0x17(r13) */
  iVar1 += DAT_007fd314 + (s8)MEM_0x800004          /* lbz 0x14(r13)      */
         + (int)DAT_007fd313 + (int)DAT_007fd337;
  if (iVar1 < 0x80) { if (iVar1 < -0x80) iVar1 = -0x80; }
  else              iVar1 = 0x7f;
  DAT_007fd315 = (char)iVar1;              /* zwgru, s8, 0.75 deg/LSB */
}
```

`FUN_0041d280` (0x41D280) is a second delta over the same two axis indices:

```c
int FUN_0041d280(void)
{
  d = (DAT_00800ec0 >> 1) - (DAT_00800020 >> 1);   clamp to s16
  v = interp_2d_s8(&DAT_005c753e, 12, DAT_007fd5ec, DAT_007fd5f0);
  v = (v * (short)d) >> 15;                        clamp to s16
  if (v >= (char)DAT_005c753d) v = DAT_005c753d;   upper clamp
  return (char)v;
}
```

so `0x5C753E` is a 16x12 s8 **delta** map on the same nmot/rl grid, weighted by
a 1.15 fixed-point factor and limited by the scalar at `0x5C753D`. Its
contents are zero everywhere except a small negative island around
2000-3000 rpm / 41-73 % load (-67 .. -13 LSB = -50 .. -10 °CA), which is the
shape of a `KFDZWKG`-style knock-limit shift or a mode delta.

## 4. Per-bank output and the knock-retard injection — 0x41D10C (VERIFIED-STATIC)

`FUN_0041d10c` (0x41D10C) runs inside a critical section
(`FUN_000b8234` … `FUN_000b81d8`) and loops **twice**, `i = 1` then `i = 0` —
the two banks of the V6:

```c
for (i = 1; i >= 0; i--) {
    zwb = DAT_007fd315;                         /* zwgru  */
    if      (bitset(DAT_007fd309, i)) zwb += DAT_007fd319;   /* variant A offset */
    else if (bitset(DAT_007fd308, i)) zwb += DAT_007fd318;   /* variant B offset */
    /* clamp s8 */
    acc = zwb + DAT_007fd338 + DAT_007fd31a + DAT_0080208e;  /* corrections */
    /* clamp s8 */
    if (DAT_007fecca == 0)
        out = DAT_00802096;                     /* substitute value */
    else
        out = acc + DAT_007fd347
                  + cand_mw_percyl_block[ DAT_007fd310[i] ];  /* KNOCK RETARD */
    out += DAT_005c753a;                        /* calibration offset, s8 */
    /* clamp s8 */
    DAT_007fd30c[i] = (char)out;                /* final per-bank zw */
}
DAT_007fd30d = (char)acc;                       /* pre-knock value, bank 1 */
```

`cand_mw_percyl_block` is `0x7FCE57`, the six per-cylinder knock-retard bytes
A3 found (§5). `DAT_007fd310[i]` maps bank -> the cylinder currently being
served. `DAT_005c753a` is a **plain s8 calibration offset added to every
bank's final angle** — the last calibration term before the output.

## 5. Knock retard per cylinder

`FUN_00416d6c` (0x416D6C, called only from `FUN_00416374`) is `%KRREG`, the
stationary knock controller. Its working set (VERIFIED-STATIC):

| RAM | Role | Evidence |
|---|---|---|
| **0x7FCE57 … 0x7FCE5C** | `dwkrz`-equivalent, the per-cylinder retard shown by the tester, **6 bytes** | written at the tail of `FUN_00416d6c` (`(&cand_mw_percyl_block)[DAT_007fce3b] = DAT_007fce55`); read by `FUN_0041d10c` at 0x41D210 and by the measuring handlers 0x39CD0-0x39D48 |
| 0x7FCE78 … 0x7FCE7D | the controller's internal per-cylinder retard, clamped to `<= 0` | `(&DAT_007fce78)[cyl] = DAT_007fce80` with `if (0 < DAT_007fce80) DAT_007fce80 = 0` |
| 0x7FCE3B | current cylinder index (0..5) | array subscript in both functions |
| 0x7FCE77 | mean retard over the non-masked cylinders | `FUN_0040ffa8(sum, count)` = divide |
| 0x7FCE74 | mean of the six calibration bytes at 0x5C83F8 | same divide helper |
| 0x7FCE55 / 0x7FCE76 | the exported per-cylinder / mean retard | stored at 0x41702C / 0x417040 |
| 0x5C83F8 … 0x5C83FD | **6-byte per-cylinder ignition offset calibration** | `(&DAT_005c83f8)[cyl]` |
| 0x5C8416 | 16x4 s8 map, axes 0x5C8402 (16) / 0x5C8412 (4), indexed by `cand_mw_nmot` and 0x7FCE4C | `lookup_2d_g_u8_u8_s8` call in `FUN_00416d6c` |

The loop bound is literally `while (uVar7 < 6)` in two places — the **six**
cylinders of the V6, so the V6 cylinder count is confirmed in the code, not
assumed.

### 5.1 Cylinder order (COMMUNITY)

From the measuring-block group table (`re/findings/measuring_groups.txt`),
groups 20-24 are the knock-retard groups. VCDS shows them in cylinder order:

| Group | Fields (var ids) | RAM |
|---|---|---|
| 020 | 88, 92, 90, 93 | 0x7FCE57, 0x7FCE5B, 0x7FCE59, 0x7FCE5C |
| 021 | 89, 91 | 0x7FCE58, 0x7FCE5A |
| 022 | nmot, rl, 88, 92 | cyl 1, 2 |
| 023 | nmot, rl, 90, 93 | cyl 3, 4 |
| 024 | nmot, rl, 89, 91 | cyl 5, 6 |

so the array index -> cylinder map is

```
idx 0 1 2 3 4 5   ->   cyl 1 5 3 6 2 4
```

which is the **firing order 1-5-3-6-2-4** of the VW VR6-derived 3.2. The
array is therefore stored in firing order, not in cylinder order. Tagged
COMMUNITY because the group-to-cylinder convention comes from VCDS practice,
not from the dump.

## 6. Fixed-point format

Every ignition variable in §2-§5 is a **signed 8-bit** quantity, clamped to
-0x80..0x7F at each stage (VERIFIED-STATIC). The scale is **0.75 °CA per LSB**
(so -96 ° .. +95.25 °, and `KFZW`'s 63 = 47.25 °BTDC):

* the measuring handlers for the knock-retard bytes (0x39CD0-0x39D48) emit
  VAG conversion formula **0x22 with A = 0x4B (75)**, and the group-003
  ignition-angle variable (id 9, RAM 0x7FEF87, handler 0x391C4) emits
  formula **0x1B with A = 0x4B** — both of the VAG "0.01 * A * (B - 128)"
  family, i.e. 0.01 x 75 = **0.75 ° per count** (VERIFIED-STATIC: the fmt/A
  bytes are in `re/measuring_vars.csv`; COMMUNITY: the formula table itself
  is the published VAG KW1281/KWP2000 one).
* 0.75 °/LSB is the standard Bosch `zw` resolution and is what makes the
  `KFZW` values read as a sensible map.

So **an ethanol offset of +1 ° of advance is +1.333 counts**; the natural
calibration unit for a patch is 0.75 ° steps, and anything finer needs the
offset accumulated in a wider intermediate.

## 7. Correction to §4 — the two per-bank slots are 0x7FD30B / 0x7FD30C

`FUN_0041d10c` walks its output pointer **downwards**
(`puVar6 = &DAT_007fd30d; … puVar6 = puVar6 - 1; *puVar6 = …`), so with
`i = 1` first it writes **0x7FD30C** (bank 1) and with `i = 0` **0x7FD30B**
(bank 0). `DAT_007fd30d` keeps the last pre-knock accumulator and
`DAT_007fd30a` is set to `DAT_007fd30b` (bank-0 copy for the torque model).
`FUN_0041d464` reads `(&DAT_007fd30b)[i]` for `i = 0, 1`, which closes the
loop. VERIFIED-STATIC.

## 8. ZWMIN, ZWSEL/ZWOUT and the hardware output (VERIFIED-STATIC)

> **Added 2026-09-16 (D3, issue #41).** The *producer* of `0x7FD32B` /
> `0x7FD32C` that this section consumes is **`FUN_00458E74` (0x458E74),
> `%ZWMIN`**. It searches its own 16-point speed axis 0x5C7A36 (key 0x7FD5F4)
> and reads five 16 × 12 s8 maps with that key and the shared `rl` key
> 0x7FD84C: 0x5D5BCB (default, `cand_KFZWMN`), 0x5D5C8B (`cand_KFZWMNUM`),
> 0x5D5D4B (`cand_KFZWMNST` — the branch that otherwise takes `zwstt`),
> 0x5D5E0B (`cand_KFZWMS`) and 0x5C7973 (`cand_KFZWMNLB`, all −24.75 degCA,
> on the same `0x40` bit this table already lists). Full account and the
> reason every label stays HYPOTHESIS: `re/findings/calibration_names.md` §4.
> The `DAT_007fd317` the ZWSEL row below adds for the second bank comes from
> the 16 × 12 s8 map **0x5C7772** (`FUN_00431140`; `calibration_names.md` §3).

| Step | Function | What it does |
|---|---|---|
| ZWMIN select | `FUN_0041d440` (0x41D440) | `zwmin = DAT_007fd32b` or `DAT_007fd32c` when `DAT_007fd306 & 0x40`; result in **0x7FD32A** |
| ZWSEL / ZWOUT | `FUN_0041d464` (0x41D464, entered at 0x41D440) | per bank: picks between the bank angle `(&DAT_007fd30b)[i]`, `zwmin` (0x7FD32A) and `DAT_007fceed`; adds `DAT_007fd317` for the second bank; **clamps to -0x48 .. +0x4E** (= -54 ° .. +58.5 °); stores into `DAT_007fd32e[bank]` |
| tester export | same function | `DAT_007fef85 = DAT_007fd32e[DAT_007fef81]`, and `DAT_007fef87` / `DAT_007fef88` per bank, with the inverted safety copies `DAT_007fd334` / `DAT_007fd335`. **0x7FEF87 is measuring-variable id 9 = VCDS group 003 field 4** |
| driver | `FUN_0041cd9c` (0x41CD9C, called from `FUN_0041cc0c`) | `FUN_004741f0((short)(zw * 0xF >> 1), DAT_00802080, cyl)` and `DAT_007fadde[cyl] = zw` |

The driver multiply is the **independent proof of the fixed-point format**:
`zw * 15 / 2 = zw * 7.5`, i.e. it converts 0.75 °/LSB into the 0.1 °/LSB the
TPU stage (`FUN_004741f0`, with `DAT_00802080` as the dwell) expects.
**0.75 °CA per LSB is VERIFIED-STATIC**, not inferred from the VAG formula
table.

## 9. KFZWOP — the torque-model optimum ignition map (VERIFIED-STATIC)

```c
void FUN_00436b90(void)      /* 0x436B90, called from task_100ms_int (0x4328E4) */
{
  DAT_00802666 = lookup_2d_g_u8_u8_s8(
        DAT_005ca3d4,  &DAT_005ca3d6,       /* ny = 16, nmot axis  */
        DAT_005ca3d5,  &DAT_005ca3e6,       /* nx = 11, rl axis    */
        &DAT_005ca3f1,                      /* value array         */
        cand_mw_nmot, cand_mw_rl);
}
```

| Object | Address | Shape |
|---|---|---|
| **KFZWOP** value array | **0x5CA3F1** (file 0x1DA3F1) | 16 rows (nmot) x 11 cols (rl), **s8**, 176 B |
| `ny` / `nx` bytes | 0x5CA3D4 / 0x5CA3D5 | 16 / 11 |
| nmot axis (`SNM16OPUW`) | 0x5CA3D6, 16 u8, 40 rpm/LSB | 560 720 1000 1240 1520 1760 2000 2520 3000 3520 4000 4520 5000 5520 6000 6520 rpm |
| rl axis (`SRL11OPUW`) | 0x5CA3E6, 11 u8, 100/128 %/LSB | 10.2 15.6 21.1 31.3 41.4 52.3 62.5 72.7 83.6 93.8 103.9 % |

Contents (s8, 0.75 °/LSB → 15 °..45 °BTDC), row = nmot, col = rl:

```
      10.2 15.6 21.1 31.3 41.4 52.3 62.5 72.7 83.6 93.8  104 %rl
 560    38   34   30   27   26   25   23   20   21   24   25
 720    40   36   32   30   28   28   24   21   22   24   26
1000    44   40   36   33   31   30   27   24   24   25   27
1240    48   45   40   36   32   31   29   27   26   27   28
1520    55   50   46   39   34   33   31   30   29   30   30
1760    58   53   48   41   36   34   33   32   32   32   32
2000    58   53   48   41   36   35   34   34   34   34   34
2520    60   54   48   41   37   36   36   36   37   37   36
3000    60   55   50   43   39   37   37   38   39   39   38
3520    60   56   51   43   41   40   40   40   41   41   40
4000    60   56   51   45   42   42   42   42   42   42   42
4520    59   55   51   47   44   43   42   42   43   43   43
5000    59   55   52   48   45   43   42   42   43   44   44
5520    59   55   52   48   46   44   43   43   43   44   44
6000    57   53   50   48   46   44   43   43   43   44   44
6520    55   52   50   47   45   44   44   43   43   44   44
```

The axis counts match the FR declaration `KFZWOP (SNM16OPUW, SRL11OPUW)`
(FR p736, `mdbas-zwoptnwa0`) exactly, and the 11 load breakpoints are the
`KFZW` 12-point load axis with the 25.8 % point dropped.

`DAT_00802666` is consumed by `FUN_00423344` (0x423344), the **`zwopt`
assembly**, which is call #6 of the ignition task and runs immediately before
ZWGRU:

```c
void FUN_00423344(void)
{
  base = (DAT_007fd306 & 0x40) ? DAT_00802664 : DAT_00802663;
  s = DAT_00800004._1_1_ + DAT_00800004._2_1_ + base + DAT_00800004._0_1_;
  if (DAT_007fd306 & 0x40) s += DAT_00802623;   /* 16x11 s8 map 0x5C9EF2 */
  if (DAT_007fd306 & 0x80) s += DAT_00802622;   /* 16x11 s8 map 0x5C9E25 */
  s += DAT_00802666;                            /* KFZWOP */
  clamp s8;
  DAT_00802665 = (char)s;                       /* zwopt */
}
```

and `zwopt` (0x802665) then feeds the torque model: `FUN_00423250` computes
`DAT_0080262e = FUN_0043619c(zwopt, DAT_007fd30a)` — the ignition efficiency
`etazwb` from the difference between `zwopt` and the bank-0 base angle
(`DAT_007fd30a`, the copy `FUN_0041d10c` makes of 0x7FD30B). That is the FR's
`MDBAS`/`MDZW` `etazwb` path.

The two 16x11 s8 delta maps that ride on `KFZWOP` are **0x5C9E25** (axis block
0x5C9E08: ny=16 @0x5C9E0A, nx=11 @0x5C9E1A) and **0x5C9EF2** (axis block
0x5C9ED5: ny=16 @0x5C9ED7, nx=11 @0x5C9EE7), both read by `FUN_0043621c`
(0x43621C). Both are **all zeros** in this dataset.

**Per the brief and `docs/05_flexfuel_design.md` §3.4, KFZWOP is not to be
shifted.** It is the torque-model reference; moving it desynchronises the
requested and delivered torque. It is listed here only so a later agent can
verify it stays untouched.

## 10. Where the ignition runs — ERCOSEK task 41

`FUN_004224bc` (0x4224BC) is **entry 6 of `tbl_os_task_control_blocks`**
(TCB at 0x47870C: entry 0x4224BC, prio 0x0A, flag byte 0x7FE642, **task
id 41**) — see `re/findings/scheduler.md` §4. It is a straight-line list of
42 `bl`s, the ignition module chain, in FR order:

| Call site | Target | Role |
|---|---|---|
| 0x4224DC | 0x423344 | `zwopt` assembly (§9) |
| **0x4224E0** | **0x41D38C** | **`zwgru` — KFZW + deltas (§3)** |
| 0x4224F4 | 0x41D108 (`FUN_0041d10c`) | per-bank angle + knock retard (§4) |
| 0x422500 | 0x41D440 | ZWMIN select + ZWSEL/ZWOUT (§8) |
| 0x422560 | 0x41D460 | ZWOUT re-entry |
| 0x422568 | 0x41CC0C | ignition output scheduling -> `FUN_0041cd9c` |

Its period is **not** established. B1 could not pin the activation of any
raster task ("activation goes through the TCB pointer"), and nothing in the
image forms the address 0x47870C or 0x7FE642, nor loads the literal 41 next
to a call. Since the module set is the full ZWGRU/ZWMIN/ZWOUT chain and the
output driver is called from the same task, the task is almost certainly
**segment-synchronous** (once per ignition event) — HYPOTHESIS, to be settled
by one dynamic run.

## 11. Insertion point for an additive ethanol ignition offset

> **TAKEN 2026-09-17 (brief E1, issue #34).** `patches/ff_fuel` now occupies
> the word at **0x41D40C**; `patch.json` carries it as a hook with
> `"onchip_edit": true` and `old` = `7c635214`. Anything that wants an
> additive ignition term from here on has to go through `ff_state.dzw_e`
> (0x7FFB29) or find another site. §11.4 below records what was built and the
> two corrections §11.1/§11.2 needed.

**Use `zwgru_build` at 0x41D38C** (`FUN_0041d38c`). It is after the base map
and every base delta, and before

* the bank offsets and the **knock retard** (`FUN_0041d10c`, task call #12),
* `zwmin` and the early/late selection and the -54 °..+58.5 ° clamp
  (`FUN_0041d464`, task call #16),

so knock control and every safety limit still act on top of the offset, which
is exactly what `docs/05_flexfuel_design.md` §3.4 asks for.

### 11.1 The exact site

```
0041d3f0  lbz   r12,-0x2cdd(r13)     ; DAT_007fd313
0041d3f4  extsb r11,r11
0041d3f8  lbz   r10,-0x2cb9(r13)     ; DAT_007fd337
0041d3fc  add   r11,r3,r11
0041d400  extsb r12,r12
0041d404  add   r3,r11,r12
0041d408  extsb r10,r10
0041d40c  add   r3,r3,r10            <-- REPLACE THIS WORD
0041d410  cmpwi r3,0x7f              ; s8 clamp
0041d414  ble   0x0041d420
0041d418  li    r3,0x7f
0041d41c  b     0x0041d42c
0041d420  cmpwi r3,-0x80
0041d424  bge   0x0041d42c
0041d428  li    r3,-0x80
0041d42c  lwz   r0,0xc(r1)
0041d430  stb   r3,-0x2cdb(r13)      ; zwgru = 0x7FD315
```

Replace the single instruction at **0x41D40C** with `bl ff_zw_offset` and put

```asm
ff_zw_offset:
        add   r3,r3,r10              ; the displaced instruction
        lbz   r12,<dzw_e>(r13)       ; s8 ethanol offset, 0.75 deg/LSB
        extsb r12,r12
        add   r3,r3,r12
        blr
```

> **Corrected 2026-09-17 (E1, issue #34): the `r13` form above is forbidden.**
> r13 is the application's SDA base and patch code may never touch it
> (`docs/04_re_guidelines.md` §7, C2's rule, `tools/blobdis.py --check-sda`
> fails the build on it). The stub as built addresses the state block
> **absolutely**, and pays two instructions for it:
>
> ```asm
> ff_zw_hook:
>         add   r3,r3,r10                              ; the displaced insn
>         lis   r11,PATCH_RAM@ha                       ; = lis r11,0x80
>         lwz   r12,(PATCH_RAM+0x00)@l(r11)            ; ff_state.magic
>         xoris r12,r12,0x4646
>         cmplwi r12,0x3031                            ; "FF01"?
>         bnelr                                        ; no state -> stock
>         lbz   r12,(PATCH_RAM+0x29)@l(r11)            ; ff_state.dzw_e
>         extsb r12,r12
>         add   r3,r3,r12
>         blr                                          ; -> 0x41D410
> ```
>
> Ten instructions, six on the path an uninitialised block takes; it writes
> only r11 and r12. The magic check is not decoration: the block is external
> SRAM and is **not** cleared at cold start (`ram.md` §3.2), so without it the
> first milliseconds after power-up would add a random byte to the ignition
> angle.

in free space. Why this site is safe:

* **Live registers.** At 0x41D40C only `r3` (the accumulator), `r10` (the last
  term) and `r1` are live; `r11`/`r12` are dead (both were consumed by
  0x41D3FC / 0x41D404). The routine may use `r11`, `r12` and `r0` freely.
* **LR.** The caller already saved LR to `0xC(r1)` at 0x41D394 and reloads it
  at 0x41D42C, so a `bl` at 0x41D40C only clobbers a value that is no longer
  needed. No stack frame is required in the patch.
* **Saturation.** The clamp at 0x41D410 still bounds the sum to s8, so an
  over-large offset cannot wrap.

### 11.2 Format of the offset

* **Type**: `signed char`, **0.75 °CA per LSB**, positive = advance.
  +1 count = +0.75 °, +2 = +1.5 °, +8 = +6 ° (the top of the range
  `docs/05_flexfuel_design.md` §3.4 proposes).
* **Where it comes from**: `dzw_e = f_zw(E) * KFDZWE(nmot, rl)`. Both factors
  are cheap to evaluate in the 100 ms flex-fuel task and to leave in one RAM
  byte; the segment-synchronous patch above then only does a byte load and an
  add. Keep the 100 ms producer clamped to a configurable maximum (e.g. 8
  counts = 6 °) so a sensor fault cannot advance the engine.
* **Reusing the existing 0.75 ° axes**: a new `KFDZWE` can share the KFZW
  axis blocks at 0x5C7736 / 0x5C7758 and the already-computed axis indices
  `DAT_007fd5ec` / `DAT_007fd5f0`, so the producer can be a single
  `interp_2d_s8(&KFDZWE, 12, DAT_007fd5ec, DAT_007fd5f0)` — but only if it
  runs in task 41, after `FUN_0041d334` has refreshed those indices.

> **Not what E1 did, and why (2026-09-17).** The producer runs in the 10 ms
> raster, not in task 41, so `DAT_007fd5ec` / `DAT_007fd5f0` are stale or
> meaningless there and must not be read. `ff_ign.c` searches **its own**
> 8-point axes (`ff_dzw_nmot_axis` / `ff_dzw_rl_axis` in FFCAL001) from
> `nmot_w` 0x7FEE74 and `rl_w` 0x7FEFB2 — the same two cells `FUN_0041d334`
> reads — and the breakpoints are taken **from** 0x5C7736 / 0x5C7758, so a
> cell still lines up with a `KFZW` row and column and §12's budget table can
> be read against it. The two stock helpers are not called either: their
> arithmetic is re-implemented with a fixed trip count, because patch code may
> not contain a loop whose trip count is data.

### 11.3 The alternative, and why it is second choice

`DAT_005c753a` (§4) is an existing s8 calibration constant added to **every**
bank's final angle in `FUN_0041d10c`, after the knock retard. Writing an
ethanol offset there needs **no code patch at all** — only a RAM-backed
redirect of that one byte. But:

* it is a global constant the calibration already uses (its current value has
  to be preserved and added to the offset), and
* it is applied after the knock retard and after the substitute-value branch,
  so a fault path that replaces the angle with `DAT_00802096` bypasses it
  inconsistently.

Use it only as a bench experiment to prove the chain end to end
(bump it by +2 counts, watch group 003 field 4 move by 1.5 °).

### 11.4 Added 2026-09-17 (brief E1, issue #34) — what was actually built

`patches/ff_fuel` (`src/ff_ign.c` + `ff_zw_hook` in `src/hooks.S`), specified
by `emu/models/flexfuel.py`, proven in `tests/test_ff_ign_patch.py`, bench
procedure in `patches/ff_fuel/test/procedure_e1.md`.

| | |
|---|---|
| **Site** | 0x41D40C, `7C 63 52 14` → `4B D3 4C 55` (`bl 0x152060`) |
| **Technique** | docs/06 §4 **technique 3** — the displaced word is an instruction, not a branch, so the stub re-does it and `blr`s to 0x41D410 |
| **Cost** | **10 instructions** (6 with no valid state block); `zwgru_build` end to end goes 276 → 286 |
| **Producer** | the 10 ms raster, `dzw_e = clamp(round(f_zw(E) × ff_dzw_map(nmot_w, rl_w) / 256), ±min(ff_dzw_max, 16))` |
| **Carrier** | `ff_state.dzw_e`, RAM **0x7FFB29**, s8, 0.75 °CA, inside the block's checksummed core |
| **Ships** | disabled: `ff_zw_enable` = 0, `ff_dzw_map` all zero, `f_zw(E0)` = 0 |
| **Diagnostics** | VCDS measuring block **108**, ids 2192-2195 (§13.2's two acceptance signals are fields 3 and 4) |

Three facts this section asserted were confirmed on the applied image rather
than assumed:

1. **`zwgru` is bit-identical with the offset at 0** over the whole 9 × 9
   nmot/rl grid `tests/test_zw_model.py` uses, comparing the patched image
   with the untouched dump;
2. **the offset is a plain add before the clamp**: with `dzw_e` forced to
   ±k for k in {−128, −16, −8, −1, 0, 1, 2, 8, 16, 127} the result is exactly
   `clamp_s8(stock + k)`, so an over-large offset saturates and cannot wrap;
3. **r10 and r1 survive and the return lands at 0x41D410**, with LR free as
   §11.1 predicted.

What this section got wrong, both corrected in place above: the `r13` load in
§11.1's sketch (forbidden — the stub addresses RAM absolutely) and the
suggestion in §11.2 to reuse the task-41 axis keys (impossible from a raster
task — the producer searches its own axes).

## 12. Python model and emulator check (VERIFIED-DYNAMIC, emulated)

`emu/zw_model.py` re-implements, in the ECU's own integer arithmetic:

* `axis_search_u16_hint` (0x40C9CC) — `(index << 16) | frac`,
* `interp_2d_s8` (0x40C3B4) — `val[iy*nx + ix]`, bilinear, s8 cells,
* `zwgru_kfzw_lookup` (0x41D334) — `KFZW(nmot_w, rl_w)`,
* `zwgru_build` (0x41D38C) — the base-angle sum, with an optional
  `ethanol_offset` argument at exactly the point §11 patches,
* `KFZWOP` for comparison.

`tests/test_zw_model.py` runs the **real functions out of the dump** under
A5's Unicorn harness (`emu/Med9Emu`) and compares them with the model:

```
$ ./.venv/bin/python -m unittest tests.test_zw_model -v
...
Ran 8 tests in 1.7s
OK
```

What it covers: both KFZW axes searched at every breakpoint, one below, one
above, the midpoints and every hint index (the hint must not change the
result — it does not); `interp_2d_s8` over every cell corner of the real
KFZW array plus four fractional positions per cell; `FUN_0041d334` end to end
with nmot_w/rl_w in RAM, including that it writes the axis keys back to
0x7FD5EC/0x7FD5F0; and `FUN_0041d38c` over a 9x9 input grid crossed with five
correction sets.

**The check found a real modelling error**, which is why it is worth having:
the three tester-adaptation bytes `FUN_0041d38c` reads are **not**
consecutive. The disassembly is `lbz r12,0x16(r13)` / `lbz r11,0x17(r13)` for
the codeword-gated difference and `lbz r11,0x14(r13)` for the always-added
term, i.e. **0x800006 - 0x800007** and **0x800004** with r13 = 0x7FFFF0 —
not `DAT_00800004[0..2]` as Ghidra's `._0_1_` / `._2_1_` field names suggest.

`./.venv/bin/python -m emu.zw_model` prints the KFZW table in degrees and
these spot checks:

| nmot, rl | KFZW | KFZWOP | headroom |
|---|---|---|---|
| 1800 rpm, 35 % | 27.75 ° | 29.25 ° | 1.50 ° |
| 3000 rpm, 60 % | 24.75 ° | 27.75 ° | 3.00 ° |
| 4500 rpm, 90 % | 22.50 ° | 31.50 ° | 9.00 ° |
| 6000 rpm, 100 % | 24.75 ° | 33.00 ° | 8.25 ° |

`KFZWOP > KFZW` everywhere, and the gap widens exactly where the engine is
knock-limited. That gap is the physical budget an ethanol advance can spend:
the flex-fuel offset should stay inside it, which is a useful sanity bound for
the `dzw_E(nmot, rl)` map of `docs/05_flexfuel_design.md` §3.4 (its proposed
+0..+2 °, up to +6 ° at high load, fits).

## 13. Knock control: detection, enrichment/monitoring hooks (VERIFIED-STATIC)

### 13.1 Module layout

| Address | Function | Role |
|---|---|---|
| 0x417BCC | `FUN_00417bcc` | thin callback: `if (r4 == 1 && r5 == 1) FUN_00416374(r6 & 0xFF)`. Registered through the data word at **0x072574**, not called by any `bl`. `r6` is the cylinder index, so this is the per-knock-window entry into `%KRKE` |
| 0x416374 | `FUN_00416374` | `%KRKE` — knock detection; keeps the per-cylinder reference levels `0x7FCE4D[6]`, `0x7FEE2A[6]`, `0x7FEE38[6]`, reseeds them from the calibration scalars 0x5C83F0 / 0x5C83DE on an enable edge, and computes the integration window `0x7FCE4B` as the minimum of 0x5C83EA…0x5C83ED and `DAT_008020c4` with a floor of 2 |
| 0x416D6C | `FUN_00416d6c` | `%KRREG` — stationary retard (§5), the only callee of 0x416374 that writes `0x7FCE57[6]` |
| 0x4163E0 | `FUN_004163e0` | reads three 8 x 4 s16 maps at **0x5C82BE**, **0x5C831A**, **0x5C8376** (axes 0x5C82B6/0x5C82A6, 0x5C8312/0x5C8302, 0x5C836E/0x5C835E) — the `%KRKE` reference/threshold family (`KFKE0…KFKE7`-equivalents) |

### 13.2 The stock "bad fuel" detector — the mirror image of a flex-fuel offset

`FUN_000f436c` (0x0F436C) is the FR's `zwgru-low-octane-fuel` branch
(FR p3090, labels `CNOKT`, `KFSWKFZK`, `KFSWKFZKR`, `KFDZK`, `TSWZK`,
`TSWZKR`). It watches the **mean** knock retard `DAT_007fce76` (0x7FCE76):

```c
if (mean_retard < KFSWKFZK(y, x))        /* more retard than the threshold  */
    if (++cnt_0x802090 > 0x5D5BBE) set   bit0 of 0x7FD31B;   /* latch on    */
if (KFSWKFZKR(y, x) < mean_retard)       /* recovered                        */
    if (++cnt_0x80208F > 0x5D5BBF) set   bit1 of 0x7FD31B;   /* latch off   */
0x7F9424 bit0 = the latch;
DAT_0080208e = (latch) ? KFDZK(y, x) : 0;
```

| Object | Address | Shape | FR name |
|---|---|---|---|
| switch-on threshold | **0x5D5A3E** | 16x12 s8, nx at 0x5C89E0 | `KFSWKFZK` |
| switch-back threshold | **0x5D5AFE** | 16x12 s8, same axes | `KFSWKFZKR` |
| retard applied | **0x5D597E** | 16x12 s8, same axes | `KFDZK` |
| debounce counts | 0x5D5BBE / 0x5D5BBF | u8 | `TSWZK` / `TSWZKR` |
| resulting delta | 0x80208E | s8 RAM | fed straight into `FUN_0041d10c` |
| latch bits | 0x7FD31B bits 0/1, 0x7F9424 bit 0 | | |

> **Added 2026-09-16 (D3, issue #41): the axes of these three maps.** The keys
> `FUN_000f436c` passes are 0x7FD820 (rows) and 0x7FD84C (columns), and
> `tools/sda_xref.py --var` finds exactly one writer for each: the central axis
> process `FUN_000BDB58`, which computes them as
> `axis_search_u16_hint(0x5C8982, nmot_w)` at 0x0BDB98 and
> `axis_search_u16_hint(0x5C89E0, rl_w)` at 0x0BDBAC. So the three maps are
> over **nmot_w 520…6520 rpm (16 points, data at 0x5C8984)** and **rl_w
> 10.2…103.9 % (12 points, data at 0x5C89E2)** — VERIFIED-STATIC, and the same
> grid as four more ignition deltas. `re/findings/calibration_names.md` §3.

`DAT_0080208e` is one of the terms `FUN_0041d10c` adds (§4), so the stock
software already has an additive, RAM-borne ignition delta that is computed
in a slow task and consumed segment-synchronously — exactly the shape the
flex-fuel offset needs. **With E85 this detector must never latch**; watching
0x7FD31B bits 0/1 is a cheap acceptance signal alongside `dwkrz` staying at
zero.

> **Added 2026-09-17 (E1, issue #34).** The latch byte now has a
> `re/symbols.csv` row (`zwgru_low_octane_latch`, 0x7FD31B, VERIFIED-STATIC):
> bit 0 is set at 0x0F43D0 (`ori r3,r12,1`) and cleared at 0x0F43E4
> (`rlwinm r3,r11,0,24,30`), bit 1 set at 0x0F4450 (`ori r3,r12,2`) and cleared
> at 0x0F4464, all four stored with `stb r3,-0x2CD5(r13)`. Do not confuse it
> with **0x7FD31C**, a different cell with its own writers (0x0C645C) and
> readers (0x458F38, 0x4590A0). `patches/ff_fuel` publishes `0x7FD31B & 3` as
> **measuring block 108 field 4** and `max(dwkrz[0..5])` as field 3, so both
> acceptance signals are one VCDS group away — and E1's shape is this
> section's shape on purpose: produce an s8 delta in a slow task, consume it
> segment-synchronously.

### 13.3 Where else the knock retard is read (monitoring targets)

`0x7FCE76` (mean retard, exported) is read at 0x039AA4 (measuring handler),
0x0C3A5C, 0x0C4414, 0x0F439C / 0x0F4410 (the detector above), 0x40A3CC,
0x436AA4, 0x454C6C and **0x4594E8**. The last one sits in the exhaust-gas
temperature model: `if (DAT_005d396c & 8) tabgm += DAT_007fce76;` — i.e. the
knock retard raises the modelled exhaust temperature, which is what pulls
component-protection enrichment in. That is the **knock-related enrichment
path** the brief asks for: it is indirect, through the exhaust-temperature
model, not through a dedicated knock-enrichment map.

> **Correction 2026-09-17 (brief E2, VERIFIED-STATIC): the paragraph above is
> wrong about 0x4594E8.** It is not the exhaust-gas temperature model and it
> is not an addition to `tabgm`; it is a **comparison inside the knock-control
> load window**. Brief E3 found this while tracing the lambda path and
> recorded it in `re/findings/calibration_names.md` §9.5, which reads: the
> word "sits in `FUN_0045943C`, a *comparison* — `cand_WKRMKR` 0x5D6123 <
> `wkrm` — inside the knock-control load window that produces 0x7FEA80, whose
> only reader is 0x103044". E3 could not make the correction here because
> brief E1 owned `ignition.md` at the time; E2 makes it now.
>
> Re-disassembled independently for this correction
> (`tools/blobdis.py data/passat_azx_ori.bin --file-off 0x594C0 --addr
> 0x4594C0 --len 0x50`; r13 = 0x7FFFF0, docs/02 §4):
>
> ```
> 004594E4  3D 60 00 5D  lis    r11, 0x5d
> 004594E8  89 8D CE 86  lbz    r12, -0x317a(r13)   ; wkrm 0x7FCE76
> 004594EC  89 6B 61 23  lbz    r11, 0x6123(r11)    ; cand_WKRMKR 0x5D6123
> 004594F0  7D 8C 07 74  extsb  r12, r12
> 004594F4  7D 6B 07 74  extsb  r11, r11
> 004594F8  7C 0C 58 00  cmpw   r12, r11
> 004594FC  41 81 00 28  bgt    0x459524
> ```
>
> There is no `add` and no store to a temperature cell on the path: both
> operands are sign-extended bytes fed straight into `cmpw`/`bgt`, and the
> next pair (0x459500-0x45950C) compares a second r13 cell against 0x5D611C
> in the same shape. So **0x7FCE76 has no route into the exhaust-temperature
> model**, and the claim that knock retard pulls component-protection
> enrichment in through `tabgm` is withdrawn. The knock-related enrichment
> path asked for in brief B7 is therefore still **not located**; see
> `calibration_names.md` §9.5 for what else that pass excluded.
>
> Two evidence strings still repeat the withdrawn claim and are **for the
> integrator, not for an agent branch**: `re/symbols.csv` row `0x7FCE76`
> (`wkrm`) and `ghidra_scripts/b7_ignition_symbols.csv` row `0x7FCE76` both
> end "…and by the exhaust-temperature model at 0x4594E8". `re/symbols.csv`
> merges with `merge=union`, so editing that row on a branch that runs beside
> another one produces a duplicated symbol row at merge time (it already did
> once, commit 31b2b31); E2 therefore left both files untouched. The correct
> text is "…and by `bbkr_load_window` 0x45943C, which compares it with
> `cand_WKRMKR` 0x5D6123". E3's own row for `0x45943C` in `re/symbols.csv`
> already points here.

`0x7FCE74` (mean of the six per-cylinder calibration offsets 0x5C83F8) and
`0x7FCE77` (mean of the controller's internal retards) are local to `%KRREG`
plus one measuring handler each (0x03B658, 0x40A3B0).

## 14. Added 2026-09-23 (brief G2, doc debt of #34) — `zwdelta_load` 0x7FD338, a load/temperature ignition term on top of `zwgru`

**Nothing in this section is newly derived.** Brief F4 found the term while
naming calibration objects and filed it in `re/findings/calibration_names.md`
§10.5 (read that section and the `re/calibration_names.csv` rows for the
evidence). This file had not mentioned it, although §4's decompilation always
showed the add (`DAT_007fd338` in the `acc = …` line). G2 moves it here so the
ignition chain is complete in one place. The names follow G4's pass 4
(`calibration_names.md` §11.1): 0x7FD3E5 is `tans`, the intake-air
temperature, and 0x7FD3F7 is `tmot_filt`. F4's "battery voltage" reading of
0x7FD3E5 is withdrawn.

| What | Address | Status, evidence |
|---|---|---|
| producer `zwdelta_7FD338_build` | `FUN_00459334` 0x459334 | VERIFIED-STATIC (F4): `stb` to 0x7FD338 at **0x459428**, its only writer (`tools/sda_xref.py --var 0x7FD338`) |
| **`zwdelta_load`** | RAM **0x7FD338**, s8, **0.75 °CA/LSB** | VERIFIED-STATIC (F4): read at **0x41D120** inside `zwbas_per_bank` 0x41D10C (§4), and at 0x1182C8 |
| `zwdelta_7FD338_weight_map` | 0x5D5F81 (struct 0x5D5F6B) | VERIFIED-STATIC (F4): `lookup_2d_u8`, y = nmot 720…6520 rpm, x = rl 15.6…146.1 %, 128 = 1.0. **0 below 46.9 % charge**, 1.0 above 62.5 % |
| `zwdelta_7FD338_map` | 0x5D5FFB | VERIFIED-STATIC (F4, x unit G4): s8, **−6.0 … +2.25 °CA** over (y = nmot 720…6000 rpm, x = 0x7FD339). 0x7FD339 = `tans` 0x7FD3E5 because code word `CW_7FD339_SRC` 0x5D5F6A = 0 |
| `zwdelta_7FD338_add_map` | 0x5D6075 | VERIFIED-STATIC (F4, y unit G4): s8, **−3.75 … +7.5 °CA** over (y = `tmot_filt` 38.25…121.5 °C, x = rl 15.6…96.9 %). **Largest cold and at load**, zero or negative hot |

Per F4 (`re/symbols.csv` row 0x459334):

```
zwdelta_load = clamp_s8( (weight_map(nmot, rl) * zwdelta_map(nmot, tans)) >> 7
                         + add_map(tmot_filt, rl) )
```

**Where it sits in the chain.** `zwgru` (§3, which now includes E1's
`dzw_e` at 0x41D40C, §11.4) → `zwbas_per_bank` (§4) adds the bank offset,
then `acc = zwb + zwdelta_load + 0x7FD31A + 0x80208E` (the last is the
low-octane `KFDZK` delta of §13.2), clamps to s8, then adds the knock retard
and 0x5C753A → ZWMIN / ZWOUT clamp −54 … +58.5 ° (§8). So `zwdelta_load` is
**after** the ethanol offset and **before** knock control and every output
limit. This is VERIFIED-STATIC from §4's decompilation.

**Why it matters for the ethanol blend: both terms spend the same advance
budget.** E1's `dzw_e` and `zwdelta_load` both add advance on top of `KFZW`.
The budget §12 and `procedure_e1.md` §B4 read against `ff_dzw_map` is
`KFZWOP − KFZW`, and that difference **does not include** `zwdelta_load`. At
load (≥ 47 % charge, where the weight map opens) and with a cold engine, the
stock term can already add up to +7.5 °CA (`add_map`), plus up to +2.25 °CA
from the `tans` map. In that region the stock software may already have spent
part or all of the budget before any ethanol advance is added. Hot, the add
map is zero or negative and gives the budget back. So:

* read the `KFZWOP − KFZW` headroom **minus the live `zwdelta_load`** at the
  cell being calibrated, not the bare table. `zwdelta_load` is not in any
  measuring block. It is logged over DDLI in
  `logging/sessions/tuning_checklist.json` (`zwdelta_load`, 0x7FD338, signed);
* calibrate `ff_dzw_map` warm, and treat a cold, high-load cell as having
  less room than the table says;
* the acceptance signals do not change (`dwkrz` at zero, 0x7FD31B & 3 at 0;
  §13.2, measuring block 108). This term is one more reason to watch them
  across the whole warm-up, not only hot.

The same caveat is cross-referenced from `docs/05_flexfuel_design.md` §3.4,
where a calibrator reading the E1 design will meet it.
