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
    iVar1 += (int)DAT_00800004._2_1_ - (int)(char)DAT_00800004;
  iVar1 += DAT_007fd314 + (int)DAT_00800004._0_1_
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
