# Start, after-start and warm-up enrichment; start ignition

Agent B8, brief `docs/agent_briefs/B8_start_and_warmup.md`, issue #16.
Date: 2026-09-15. Dump `data/passat_azx_ori.bin` (03H906032 / 1037382557,
SHA-256 `b15590d3…09b3`), unchanged — `python3 tools/checksum.py verify -q`
prints `ALL OK (65 blocks)` before and after.

Tags as in `docs/agent_briefs/00_common_rules.md`.

## 0. Working environment (reproduce)

```bash
mkdir -p /tmp/ghidra_B8
cp -R ghidra_projects/med9.gpr ghidra_projects/med9.rep /tmp/ghidra_B8/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
./.venv/bin/python ghidra_scripts/import_symbols.py \
    --project-dir /tmp/ghidra_B8 --project-name med9 --repo "$PWD"
# -> 215 functions named, 206 labels created, 3 skipped
./.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B8 --project-name med9 0x41A268
```

RAM addresses are resolved with the application SDA bases r13 = 0x7FFFF0 and
r2 = 0x5C9FF0 (`re/findings/scheduler.md` §7). Who-touches-this-RAM-cell
questions below were answered by scanning every D-form instruction of the
image for r13/r2-relative accesses; the committed equivalent is
`python3 tools/callgraph.py data/passat_azx_ori.bin --xref-store <addr> <addr>`
(and `ghidra_scripts/decompile.py --refs <addr>` for pointer-table
references, which the D-form scan cannot see).

---

## 1. The coolant-temperature variable (task: confirm A3's `cand_mw_tmot`)

**Verdict: A3's `cand_mw_tmot` at RAM 0x8021EF is confirmed as `tmot` —
VERIFIED-STATIC for the address and the writers, and the scaling is
`T[°C] = 0.75 * x - 48`.**

| Fact | Evidence |
|---|---|
| 0x8021EF has exactly **two** writers and 25 readers | `stb` at 0x11AB28 and 0x43257C; readers spread over 0x03920C…0x42C9F4 |
| cyclic writer `tmot_task` = `FUN_004324dc` (0x4324DC-0x43259B) | keeps a 0..9 counter in 0x8021DC and, when it wraps, does `0x802228 = lookup_1d_u16(<sensor curve>, 0x80345A); 0x8021EF = 0x802228 >> 8` |
| the other writer is the module init `FUN_0011aa28` (0x11AA28) | same two lines, plus ~20 calibration defaults |
| so **0x802228 is `tmot_w` (u16)** and **0x8021EF is `tmot` (u8) = tmot_w >> 8** | 0x4324DC, 0x11AB28 |
| the sensor characteristic is one of two u16 curves, 0x5D770E (normal) or 0x5D76BC (substitute, selected by 0x7FEB51), evaluated over the raw ADC word 0x80345A | 0x4324DC |

**Scaling proof (VERIFIED-STATIC, independent of any community table).** The
u8 temperature axes of the start maps are round Celsius values under
`0.75*x - 48` and under no other plausible affine rule:

```
KFWKSTT y axis 0x5C6C62 : 24 31 37 44 55 64 84 91 101 117 144 184
                        = -30 -24.75 -20.25 -15 -6.75 0 15 20.25 27.75 39.75 60 90 °C
KFKSTT  y axis 0x5C6E14 : 21 27 31 37 44 51 64 77 91 104 144 197
                        = -32.25 -27.75 -24.75 -20.25 -15 -9.75 0 9.75 20.25 30 60 99.75 °C
```

(0 °C = 64, 15 °C = 84, 30 °C = 104, 60 °C = 144, 90 °C = 184 — exact.)
So **1 LSB = 0.75 °C, offset -48 °C, u8**, and `tmot_w` (u16) is
0.75/256 °C per LSB. A sweep of `re/calibration_draft.csv` finds **209**
tables with an axis on exactly this grid, which is the rest of the
confirmation.

## 2. `tmst` — the engine temperature latched at start (VERIFIED-STATIC)

**RAM 0x8021F6, u8, same 0.75 °C/LSB, offset -48.** It is the y axis of every
start map in §3.

```
0005BB80  lbz  r12,-0x1520(r13)   ; 0x7FEAD0  start flag (B_st equivalent)
0005BB88  beq  0x0005BBB0
0005BB8C  lbz  r12,0x21FF(r13)    ; tmot   0x8021EF
0005BB90  stb  r12,0x2201(r13)    ; 0x8021F1  <- raw latch of tmot at start
0005BB94  lbz  r11,-0x149C(r13)   ; 0x7FEB54  "coolant value substituted"
0005BB9C  beq  0x0005BBA8
0005BBA0  lbz  r4,-0x2BF9(r13)    ; 0x7FD3F7  substitute value
0005BBA8  lbz  r4,0x2203(r13)     ; 0x8021F3  modelled engine temperature
0005BBAC  stb  r4,0x2206(r13)     ; 0x8021F6 = tmst
```

0x8021F6 has exactly **one** writer (0x5BBAC) and ~40 readers; 0x8021F1 has
two (0x5BB90 and 0x0FA0FC, the temperature-model module).

**0x7FEAD0 is the start flag (`B_st` equivalent)**: six writers in the
engine-state module at 0x0BD7A0-0x0BD864 and 31 readers spread over the fuel,
ignition, idle and diagnosis modules. Its companion **0x7FE921** is the
"start finished" flag consumed by the start-quantity module (§3), and
0x7FE920 selects the two `gk_rk` branches (§3.3).

## 3. The start-quantity module `ESSTT` — `FUN_0041a268` (VERIFIED-STATIC)

`FUN_0041a268` (0x41A264-0x41A68B; Ghidra names the body 0x41A268) is the FR's
**`%ESSTT` Einspritzung Start**. Its twin `FUN_0041a690` (0x41A68C-0x41A82B)
runs the same computation on a second map set — the FR's `…HDR`
high-pressure-start variant (`KFKSTTHDR` / `KFWKSTTHDR`). Decompiled body,
relevant tail:

> **Correction 2026-09-17 (brief E2, issue #35):** the `return` on the first
> line below is a decompiler artefact. The early-out does not return — it sets
> `r31 = 0x400` and **branches to 0x41A680**, the same `sth` the normal path
> uses, so that store executes on every activation whether the start is over or
> not. §9.3 has the five instructions and what it means for the S1 hook. The
> high-pressure twin `FUN_0041a690` really does branch past its store.

```c
if (DAT_007fe921 != 0) { DAT_0080302c = 0x400; return; }        /* not starting -> 1.0 */
...
DAT_007fd269 = clamp(DAT_007fd298 - DAT_007fd26b, 0, 255);      /* anztist */
a = lookup_2d_g_u8_u16_u16(12,0x5C6E14, 2,0x5C6E20, 0x5C6E24,   /* KFKSTT  */
                           tmst=0x8021F6, prist=0x8031DA);
b = lookup_2d_u8(0x5C6C60, tmst, anztist=0x7FD269);             /* KFWKSTT */
DAT_0080302a = (b * a) >> 7;
c = lookup_2d_u8(0x5C6C46, tmst, nmot8=0x7FCE95);               /* KFWKSTN */
v = (c * DAT_0080302a) >> 8;
v = mul_q15(v, DAT_007fd067 << 8);                              /* tester trim */
d = lookup_2d_u8(0x5D3739, tmst, 0x800EEC);
DAT_00803028 = mul_q15(v, d << 8);                              /* ksta      */
DAT_0080302c = mul_q15(DAT_007fd27b << 8, max(DAT_00803028,0x400)); /* * kstaa */
```

`mul_q15(x,y) = min((x*y)>>15, 0xFFFF)` is `FUN_00410060` (0x410060);
`FUN_00410084` (0x410084) is the same with `>>14`.

### 3.1 The maps

| FR name | value array | shape / type | y axis (tmst) | x axis | call site(s) |
|---|---|---|---|---|---|
| **KFKSTT** | **0x5C6E24** | 12 x 2, **u16**, 1024 = 1.0 | 0x5C6E14, 12 u8, -32.25…99.75 °C | 0x5C6E20, 2 u16 = `prist` 1000 / 4000 | 0x41A50C, 0x41A57C |
| **KFWKSTT** | **0x5C6C7C** | 12 x 14, u8, **128 = 1.0** | 0x5C6C62, 12 u8, -30…90 °C | 0x5C6C6E, 14 u8 = injections since start (0,3,4,5,6,7,8,11,12,15,17,18,23,24) | 0x41A520, 0x41A590 |
| **KFWKSTN** | **0x5C6C50** | 4 x 4, u8, **256 = 1.0** | 0x5C6C48, 4 u8, -30 / -6.75 / 25.5 / 90 °C | 0x5C6C4C, 4 u8 = nmot at 40 rpm/LSB (320/400/600/800 rpm) | 0x41A5CC, 0x41A75C |
| **KFWKSTTHDR** (twin of KFWKSTT) | 0x5C6D40 | 12 x 14, u8 | 0x5C6D26, 12 u8, -30…90 °C | 0x5C6D32, 14 u8 | 0x41A6FC |
| **KFKSTTHDR** (twin of KFKSTT) | 0x5C6E66 | 12 x 2, **u8** | 0x5C6E56, 12 u8, -35.25…110.25 °C | 0x5C6E62, 2 u16 | 0x41A720 |
| weighting, 4 x 4 (repeat-start) | 0x5C6E8C | 4 x 4, u8 | 0x5C6E80, 4 u16 (input 0x80218C) | 0x5C6E88, 4 u8 = tmst -30/-15/0/30 °C | 0x41A544 |
| weighting over 0x800EEC | 0x5D3745 | 6 x 4, u8, 128 = 1.0 | 0x5D373B, 6 u8 = tmst -20.25…90 °C | 0x5D3741, 4 u8 (input 0x800EEC) | 0x41A614, 0x41A7A8 |

`KFKSTT` contents (both `prist` columns are identical, i.e. the rail-pressure
dependence is not calibrated in this dataset):

```
tmst °C  -32.25 -27.75 -24.75 -20.25 -15   -9.75  0     9.75  20.25 30    60    99.75
raw       25395  21402  18842  15360 12394 10036  7168  5116  3939  3584  2868  1895
/1024      24.80  20.90  18.40  15.00 12.10  9.80  7.00  5.00  3.85  3.50  2.80  1.85
```

`KFWKSTT` (decay of the start enrichment over the injection count, 128 = 1.0;
three of the twelve rows shown):

```
inj:      0    3    4    5    6    7    8   11   12   15   17   18   23   24
-30 °C   128  128  128  128  115  115  115  115   51   51   51   32   32   19
  0 °C   128  128  128  128   77   77   77   77   44   44   44   44   44   44
 90 °C   128  128  128  128   96   96   96   96   83   83   83   83   83   83
```

`KFWKSTN` is 255 in all 16 cells (≈1.0 at 256 = 1.0), i.e. effectively off —
as the FR recommends ("Für Neuapplikationen sollte wann immer möglich
KFWKSTN = 1.0 bedatet werden").

### 3.2 The RAM cells it produces

| RAM | meaning | scaling |
|---|---|---|
| 0x7FD269 | `anztist`, injections since start = clamp(0x7FD298 - 0x7FD26B, 0, 255) | u8 |
| 0x7FD26B | injection counter latched at start (written 0x41A6B8 / 0x41A820) | u8 |
| 0x7FD298 | free-running injection counter (incremented at 0x41C2C4) | u8 |
| 0x80302A | `KFKSTT * KFWKSTT` | u16, 1024 = 1.0 |
| **0x803028** | **`ksta`** — start quantity factor before adaptation | u16, 1024 = 1.0 |
| **0x80302C** | **`ksta * kstaa`** — what the fuel path consumes | u16, **1024 = 1.0** |
| 0x7FD27B | `kstaa`, start-quantity adaptation (`%STADAP`), written at 0x41B6B0 | u8, 128 = 1.0 |

The 1024 = 1.0 scale is proved by the early-out `DAT_0080302c = 0x400` taken
on the "start finished" flag 0x7FE921 — outside the start the factor has to
be exactly 1.

### 3.3 Where it multiplies into the fuel (the cranking lever)

`gk_rk` = `FUN_0041aa48` (0x41AA48, B6 §9) begins with a two-way switch on
0x7FEA33 (set to 1 by `FUN_0041ade4` at 0x41AE3C when 0x7FE920 is set,
cleared by `FUN_0041ae70` at 0x41AED4):

```
0041AA50  lbz    r8,-0x15BD(r13)   ; 0x7FEA33
0041AA60  beq    0x0041AA80        ; ---- 0x7FEA33 == 0 : the start path ----
0041AA64  lbz    r12,-0x2D80(r13)  ; 0x7FD270          (other path)
0041AA68  lbz    r11,0x1D02(r13)   ; 0x801CF2
0041AA6C  lhz    r10,0x3030(r13)   ; 0x803020
0041AA70  mullw / mullw / >>16
0041AA80  lbz    r12,0x1D02(r13)   ; 0x801CF2  tester mixture trim, 128 = 1.0
0041AA84  lhz    r11,0x303C(r13)   ; 0x80302C  ksta * kstaa, 1024 = 1.0
0041AA88  mullw  r12,r12,r11
0041AA8C  rlwinm r9,r12,25,7,31    ; >> 7      (saturated to 0xFFFF below)
0041AAA8  sth    r31,0x303E(r13)   ; 0x80302E
0041AAAC  lhz    r11,-0x12B8(r13)  ; 0x7FED38  rl (relative charge)
0041AAB4  mullw  r12,r12,r11
0041AAB8  rlwinm r9,r12,21,11,31   ; >> 11
```

so **`rk_base = ((fgru_trim * ksta_adapted) >> 7) * rl >> 11`**, and from
there the chain B6 documented (`+ 0x8030F8`, `/ 0x80304A`, `* fr`, `+ fra`,
`* frm`, `- 0x80315C`, `* ZGST` → `rk` at 0x803038 → `rk2ti`).

**Insertion point S1 (cranking).** Cleanest is the `sth` that publishes
0x80302C at the end of each start module — **0x41A680** (`sth r31,0x303C(r13)`
in `FUN_0041a268`) and **0x41A808** (same store in `FUN_0041a690`). A patch
there multiplies `ksta_adapted` by `f_st(E, tmst)` in Q10 and saturates at
0xFFFF. It is bit-identical at E0 by construction, and it cannot affect any
other operating point because 0x80302C is forced to 0x400 outside the start.
The alternative — the `mullw`/`srawi` pair at 0x41AA88/0x41AA8C inside
`gk_rk` — is one instruction pair but sits in the hottest path of the
segment task, so prefer the two `sth` sites.
Fixed point at the hook: u16, **1024 = 1.0**, saturate at 0xFFFF.

## 4. The engine-state chain (VERIFIED-STATIC)

Five one-byte flags, in the order they change during a start:

| RAM | role | written by | rule |
|---|---|---|---|
| **0x7FEAD0** | engine **not** running (off / cranking / stalled) | `FUN_000bd6e8` 0x0BD6E8-0x0BD893 | set when the speed-signal counter 0x7FCE98 > 4; cleared when 0x802128 bit 3 is set and the segment period 0x7FD68C is shorter than `200000000 / KAL(0x5C866C)`, i.e. nmot above a threshold |
| 0x7FE91F | crankshaft turning | 0x0BD1D4 / 0x0BD200 | thresholds `0x7FD059` / `0x7FD05A`, two 3-point curves over temperature (0x5CEE11, 0x5CEE18) |
| **0x7FE91D** | **`B_st`** — start attempt active | `FUN_00419cac` 0x419CAC-0x419CD7 | `0x7FEAD0 ? 0 : (0x7FE91F ? 1 : keep)` |
| **0x7FE920** | **start end reached** | `FUN_00419cd8` 0x419CD8-0x419D3B | `0x7FE91D && (0x7FD058 & 1)` |
| 0x7FD05B | cycles since start end, u8, saturates | same | `0x7FE920 ? ++ : 0` |
| **0x7FE921** | **`B_stend`** — start finished | same | `0x7FD05B > 3` |
| 0x7FECCA | 0x7FE921 sampled into the segment task | 0x422A60-0x422A68 (`lbz 0x7FE921; stb 0x7FECCA`) | used by the ignition chain, §5 |
| 0x7FEA33 | `gk_rk` path selector | 0x41AE3C (set when 0x7FE920), 0x41AED4 (cleared) | 0 → start path (`ksta`), 1 → running path |

`FUN_000d0dfc` (0x0D0DFC-0x0D0E87) clears 0x7FE920 / 0x7FE921 / 0x7FE91D / 0x7FD05B
whenever 0x7FEAD0 is set, and runs the **after-start timer**:

```c
if (DAT_007fe91f != 0 && DAT_008011d8 != 0xFFFF) DAT_008011d8++;   /* 0x0D0E54 */
```

**0x8011D8 is a u16 time-since-engine-turning counter** (one writer, 0x0D0E54;
25 readers across the rail-pressure, catalyst, diagnosis and idle modules).
It is not reset at start end, only at power-up (0x11BD3C), so it is
"time since the engine started turning" — the FR's `tnst_w` role.

### 4.1 After-start and warm-up enrichment: what this dataset actually does

**Negative finding (VERIFIED-STATIC): there is no separate `fnsk` / `fwlk` /
`fnswl_w` factor multiplying `rk` in this software.** The complete list of
multiplicative terms on the fuel path is fixed by `gk_rk` (0x41AA48) and by
B6 §9, and every one of them is accounted for:

| term | RAM | what it is |
|---|---|---|
| tester mixture trim | 0x801CF2 | `(0x7FD066 << 6 + 0x6000) * K(0x5D350C=128) >> 15`, i.e. `0.75 + n/512` in Q7; `0x7FD066` has **no code writer at all** — it is only reachable through the tester pointer table at 0x0A3ADC (`decompile.py --refs 0x7FD066`), so it is 1.0 in normal operation |
| **start quantity** | **0x80302C** | §3 — the only temperature-driven enrichment on the fuel path |
| running mixture | 0x803020 | the torque/λ cascade, §4.2 |
| charge | 0x7FED38 | `rl`, u16, 4096 = 100 % (writer 0x418A3C, source `rl_w` 0x7FEFB2) |
| `fr` / `fra` / `frm` | 0x802DF8, 0x802E00, 0x801D1A, 0x801E36, 0x801E28 | lambda control and its adaptation (B6 §9) |
| `ZGST` | 0x801D8C[cyl] | per-cylinder balancing |

So the **after-start decay is `KFWKSTT` over the injection count** (§3.1) and
nothing else, and the **warm-up enrichment is expressed as a torque/efficiency
and λ request**, not as a fuel factor. That is the BDE (FSI) form of the FR's
ESNSWL/LANSWL pair: the ECU heats the catalyst with ignition retard (§5.1) and
holds λ = 1 rather than enriching.

### 4.2 The running-mixture cascade (for completeness; partly HYPOTHESIS)

`FUN_00419da4` (0x419DA4-0x41A263) builds 0x803020, the factor `gk_rk` uses
once the start has ended:

```c
0x80301C = 0x7FD266 * (0x7FD065 * 0x7FD267 *
              ((0x801D00 | 0x7FD263) * ((0x801D06>>8) * iVar3 >> 8)) >> 14) >> 7;
0x803022 = min(0x80301C + 0x1000, 0xFFFF);          /* 0x1000 = 4096 */
0x803020 = mul_q15(0x803026, (0x7FD264 * 0x803022) >> 7);   /* sth at 0x41A250 */
```

with the sub-factors written by `FUN_004302dc` (0x7FD264, 0x7FD265, 0x7FD261),
`FUN_00430448` (0x7FD267, 0x7FD268, 0x803026 — its two maps 0x5C6B64 and
0x5C6C12 are **all 128**, i.e. neutral) and `FUN_0010c874`
(0x801CF4-0x801D02, the maps 0x5D36E0 / 0x5D3568 / 0x5D361B / 0x5D3610).
All of these are ≤ 1.0 weightings; none of them enriches over coolant
temperature. Scaling: 0x803020 is Q12 (4096 = 1.0), because the running branch
of `gk_rk` computes `(0x7FD270 * 0x801CF2 * 0x803020) >> 16` with two Q7
factors and must produce the same 1024 = 1.0 as the start branch.

**Insertion point S2 (post-start, if a warm-running ethanol factor is ever
wanted here rather than at B6's `rk` hook): the `sth` of 0x803020 at
0x41A250**, Q12, saturate at 0xFFFF. It is *not* recommended — it duplicates
B6's hook at 0x42247C and covers every operating point, not just warm-up.

## 5. The start ignition angle `zwstt` (VERIFIED-STATIC)

**During the start the whole per-bank ignition angle is replaced by one RAM
byte, `zwstt` at 0x802096 — `zwgru`, the bank offsets and the knock retard are
all bypassed.** In `zwbas_per_bank` (`FUN_0041d10c`, 0x41D10C, B7 §4):

```c
if (DAT_007fecca == '\0')            /* start not finished */
     iVar10 = (int)DAT_00802096;     /* <- zwstt replaces everything */
else iVar10 = iVar9 + DAT_007fd347 + dwkrz[cyl];
iVar10 = iVar10 + cand_DZW_BANK_OFFSET;    /* s8 clamp, then 0x7FD30B / 0x7FD30C */
```

0x7FECCA is a copy of the "start finished" flag 0x7FE921 sampled into the
segment task (`lbz r12,-0x16CF(r13); stb r12,-0x1326(r13)` at
0x422A60/0x422A64), so the condition is exactly `!B_stend`.

`zwstt` is built by **`FUN_00431294` (0x431294-0x43139F)**, in the same 10 ms
module group as `FUN_004310c8`, the driver of `zwgru_kfzw_lookup`:

```c
if (DAT_007fecca == '\0') {                       /* only while starting */
  if ((DAT_007fce0c & 2) == 0) {
    a = lookup_2d_g_u8_u8_s8(8,0x5C7B54, 8,0x5C7B5C, 0x5C7B64, zdgz=0x7FCE14, tmst=0x8021F6);
    b = lookup_2d_g_u8_u8_s8(3,0x5C7B1A, 6,0x5C7B1D, 0x5C7B23, nmot8=0x7FCE95, tmst);
    v = clamp_s8(a + b);
  } else {
    v = lookup_2d_g_u8_u8_s8(3,0x5C7B37, 6,0x5C7B3A, 0x5C7B40, nmot8, tmst);
  }
  c = lookup_1d_g_u8_s8(6,0x5C7BA5, 0x5C7BAB, 0x7FD3E5);
  DAT_00802096 = clamp_s8(c + v);                 /* stb at 0x431384 */
}
```

| object | address | shape / type | axes | note |
|---|---|---|---|---|
| **`KFZWSTT`** start-angle map | **0x5C7B64** | 8 (zdgz) x 8 (tmst), **s8**, 0.75 °CA/LSB | y 0x5C7B54 = 0,3,5,7,8,9,11,12 ignitions since start; x 0x5C7B5C = -30, -20.25, -15, -9.75, 0, 15, 30, 90 °C | call 0x431304 |
| `KFZWSTN` speed term | 0x5C7B23 | 3 (nmot) x 6 (tmst), s8 | y 0x5C7B1A = 5/10/15 (200/400/600 rpm at 40 rpm/LSB); x 0x5C7B1D = -30…90 °C | **all zero** |
| alternative map (`0x7FCE0C` bit 1 set) | 0x5C7B40 | 3 (nmot) x 6 (tmst), s8 | y 0x5C7B37 = 5/10/20; x 0x5C7B3A = -30, -20.25, -9.75, 0, 30, 80.25 °C | 0…+8, -4 in the hot column |
| 1D additive term over 0x7FD3E5 | 0x5C7BAB | 6, s8 | axis 0x5C7BA5 = 40,80,120,160,200,240 | **all zero** |

`KFZWSTT` contents (rows = ignitions since start, columns = `tmst`; raw s8
counts, x 0.75 gives °CA, positive = before TDC):

```
zdgz \ tmst  -30  -20.25  -15  -9.75   0    15    30    90 °C
   0        -66    -55   -46   -39   -22   -7    -2    -4
   3        -66    -55   -42   -29    -9    0    -2    -4
   5        -47    -39   -28   -11     4    0    -2    -4
   7        -30    -25   -16     1     7    0    -2    -4
   8        -20    -13    -8     5     7    0    -2    -4
   9        -12     -9     1     8     7    0    -2    -4
  11         -9     -9     8     8     7    0    -2    -4
  12         13      8     8     8     7    0    -2    -4
```

i.e. the first combustion of a -30 °C start fires at **-49.5 °CA (49.5 ° after
TDC)** and the angle walks forward to +9.75 ° by the twelfth ignition, while a
hot start sits at a flat -3 °. `zdgz` (0x7FCE14) is the low byte of the u16
ignition counter 0x7FEDA0, incremented once per new firing bank at 0x0ACC1C
and copied to 0x7FCE14 at 0x0ACC24.

**Insertion point Z1 (start ignition): the `stb r31,0x2096(r13)` at 0x431384**,
or an added term in `FUN_00431294` before the final `clamp_s8`. Format **s8,
0.75 °CA per LSB** — the fixed point B7 established for the whole ignition
chain. An ethanol-dependent advance of +2…+4 ° at cold start is +3…+5 counts.
Note that during the start there is no knock protection acting on this value
(the knock retard is bypassed), so the offset must be small and should be
limited to `tmst` below about 40 °C.

### 5.1 Warm-up ignition: an efficiency request, not a `dzwwl` map

> **Note 2026-09-23 (G4, #41, calibration_names.md §11.8):** 0x803046 / 0x803044
> are very probably per-bank **lambda** setpoints, not efficiency setpoints:
> 0x803046 becomes 0x80304A (`FUN_0041AF2C`), which `gk_rk` divides the fuel
> mass by, and `%ATM` keys its lambda correction `KFATLAMS` with it. The
> dataflow below is unchanged; read "efficiency demand" as "lambda setpoint"
> (HYPOTHESIS until the inputs of `eta_coordinator` are traced).

The FR's `ZWWL` (`dzwwl`, `KFZWWLNM`, `KFZWWLRL`) has no direct equivalent.
Once the start has finished, the warm-up / catalyst-heating retard reaches
`zwgru` through the **torque-coordinator efficiency demand**:

* `FUN_00442c18` (0x442C18-0x44308F) produces the two efficiency setpoints
  0x803046 / 0x803044 (u16, **4096 = 1.0**), their mean 0x803042 and
  `0x7FD271 = 0x803042 >> 5` (u8, 128 = 1.0).
* `FUN_004362d4` (0x4362D4-0x436453) turns 0x803042 into the additive angle
  **0x800004** (s8) — exactly 0 when the efficiency demand is 1.0
  (`if (0x803042 == 0x1000) cVar2 = 0`).
* `FUN_004310c8` (0x4310C8) adds a second term **0x7FD313** =
  `KF(nmot8, 0x7FD271)` from the 8 x 12 s8 map at **0x5C76D5**
  (y 0x5C76C1 = 8 nmot points at 40 rpm/LSB; x 0x5C76C9 = 12 efficiency
  points 83…154, i.e. 0.65…1.20), values +11 … -1 counts.
* Both land in `zwgru_build` (0x41D38C) as the `0x800004` and `0x7FD313`
  terms B7 already listed.

So a flex-fuel ignition change during warm-up is covered by B7's additive hook
at 0x41D40C; no separate warm-up map exists to shift.

## 6. Model and emulator check (VERIFIED-DYNAMIC)

`emu/start_model.py` re-implements both computations in the ECU's own fixed
point, reading every calibration constant out of the image rather than
hard-coding it. `tests/test_start_model.py` runs the **real** code under the
A5 Unicorn harness and compares bit for bit:

```bash
./.venv/bin/python -m emu.start_model                    # print both tables
./.venv/bin/python -m unittest tests.test_start_model -v # 14 tests, ~1.7 s
```

```
Ran 14 tests in 1.710s
OK
```

What is compared:

| | firmware entry | cases |
|---|---|---|
| `lookup_2d_u8` on `KFWKSTT`, `KFWKSTN`, 0x5D3739 | 0x40D18C | 3 maps x 17 `tmst` x 10-12 x-values |
| `lookup_2d_g_u8_u16_u16` on `KFKSTT` | 0x40E72C | 17 x 8 |
| `lookup_2d_g_u8_u8_s8` on `KFZWSTT`, `KFZWSTN` | 0x40DC7C | 2 maps x 10-12 x 17 |
| `lookup_1d_g_u8_s8` on 0x5C7BAB | 0x40F454 | 11 |
| `mul_q15` incl. the 0xFFFF saturation | 0x410060 | 56 |
| **`%ESSTT` end to end** -> 0x803028 / 0x80302A / 0x80302C | `FUN_0041a268` body, 0x41A274-0x41A684 | 204 (`tmst` x `anztist`) + 144 (`prist` x `nmot` x trim x `kstaa`) |
| **`zwstt` end to end** -> 0x802096 | `FUN_00431294` | 204 + 170 + 50 |

`FUN_00431294` runs under `emu.call()` unchanged. `FUN_0041a268` is entered
with `emu.run(0x41A274, until=0x41A684)`, i.e. after its
`bl 0x000b8234` critical-section entry and before the matching
`bl 0x000b81d8`: those two OS primitives dispatch through the 0xFFFFFFF0
vector, which the harness does not model. Everything between them is the real
instruction stream.

What the model says the ECU does, in physical units (nmot 400 rpm,
prist 4000):

```
cranking factor ksta * kstaa (1.0 = no enrichment)
  tmot C :       -30     -20     -10       0      20      40      60      90
  inj   0:    22.76   14.94    9.76    6.97    3.83    3.26    2.79    2.07
  inj   8:    20.45   10.50    5.87    4.19    2.42    2.16    1.94    1.55
  inj  12:     9.07    5.95    3.35    2.40    1.53    1.73    1.44    1.34
  inj  24:     3.38    3.03    2.21    2.40    1.53    1.73    1.44    1.34

start ignition zwstt (deg CA, + = before TDC)
  tmot C :       -30     -20     -10       0      20      40      60      90
  zdgz  0:   -49.50  -41.25  -29.25  -16.50   -4.50   -2.25   -2.25   -3.00
  zdgz  5:   -35.25  -29.25   -8.25    3.00   -0.75   -2.25   -2.25   -3.00
  zdgz 12:     9.75    6.00    6.00    5.25   -0.75   -2.25   -2.25   -3.00
```

## 7. What the flex-fuel patch needs from this

> **Added 2026-09-17 (brief E2, issue #35): S1 and Z1 are taken.**
> `patches/ff_fuel` now owns all three words — 0x41A680, 0x41A808 and
> 0x431384, all three in the **on-chip** flash 0x404000-0x47FFFF. §9 below has
> the task each one runs in, the proof that the 10 ms producer precedes the Z1
> store inside one activation, the dead-register set at each word and the two
> axes E2 chose for `ff_fst_map`. S2 (0x41A250) and the warm-running ignition
> word (0x41D40C, taken by E1) are unchanged by this brief.

| | where | format | when it acts |
|---|---|---|---|
| **S1 — cranking fuel** | `sth` at **0x41A680** (and 0x41A808 for the HDR twin), publishing 0x80302C | u16, **1024 = 1.0**, saturate 0xFFFF | ~~only while `B_stend` (0x7FE921) is clear; the ECU forces 1.0 afterwards, so the hook is inert outside the start~~ **corrected 2026-09-17 (E2, §9.3): 0x41A680 also executes with 0x400 in r31 once `B_stend` is set, so the hook must test `B_stend` itself** |
| **Z1 — start ignition** | `stb` at **0x431384**, publishing `zwstt` 0x802096 | **s8, 0.75 °CA per LSB** | same window; **no knock protection acts here** |
| S2 — running mixture (not recommended) | `sth` at 0x41A250, publishing 0x803020 | u16, Q12 (4096 = 1.0) | every running operating point; duplicates B6's hook at 0x42247C |
| warm-running ignition | B7's word at 0x41D40C | s8, 0.75 °CA per LSB | after start end |

The 2-D `f_st(E, tmst)` map of `docs/05_flexfuel_design.md` §3.5 should use the
`KFWKSTT` temperature breakpoints (0x5C6C62: -30, -24.75, -20.25, -15, -6.75,
0, 15, 20.25, 27.75, 39.75, 60, 90 °C) so a cell maps one-to-one onto a stock
row, and a small E axis (0, 20, 40, 60, 85, 100 %). `f_st(0, ·) = 1024` makes
the patched ECU bit-identical at E0 — `tests/test_start_model.py` asserts that
for both hooks.

Two calibration notes that fall out of the numbers:

* The stock cranking factor already runs 2.1x at 90 °C and 22.8x at -30 °C.
  Ethanol needs roughly 1.5x the mass at a warm start and much more when cold,
  but the **injection window** is the binding constraint at 22.8x (B6 §8:
  `dwi`, RAM 0x803088); log it before calibrating `f_st` below about -10 °C.
* `KFWKSTN` (0x5C6C50) is entirely 255 and `KFZWSTN` (0x5C7B23) entirely 0.
  Both are free real estate if a speed dependence is ever wanted without
  writing new code — but changing them changes gasoline behaviour too, so
  prefer the additive patch.

## 8. Open questions

| Question | Status |
|---|---|
| The physical meaning of RAM 0x800EEC (the x axis of the 0x5D3745 and 0x5D3568 weightings, 4 breakpoints 45/51/58/61) and of 0x80218C (the 0x5C6E8C axis). Both weightings are ≤ 1.0 and are 1.0 at the top of their range, so they only reduce `ksta`. | open; they do not block S1, which is downstream of both |
| Whether 0x7FD3E5 and 0x7FD3F7 are `tans` and a modelled `tmot`. They have no r13-relative writer (only reads), i.e. they are written through a pointer or an indexed store; `decompile.py --refs` shows the tester pointer table at 0x0A3AE0 for their neighbours. They feed the (all-zero) 1D `zwstt` term and the `0x5D3610` map. | **SETTLED (2026-09-23, G4, calibration_names.md §11.1).** Both do have r13 writers (the search above missed them): **0x7FD3E5 is the intake-air temperature `tans`**, 0.75 °C/LSB − 48 (VERIFIED-STATIC unit; the label is COMMUNITY via VCDS group 004.4), written at 0x0F8FD4 / 0x11A990 / 0x11A998 from the NTC curve 0x5D728F over ADC channel 6, and **0x7FD3F7 is a low-pass-filtered engine temperature** in the `tmot` unit, written at 0x0F9F24 / 0x0F9FC4 / 0x11ADD0 from 0x8021F3. So the 1D term 0x5C7BAB is over `tans` −18 … 132 °C, and it is not a battery voltage (calibration_names.md §10.4 had guessed 1/16 V) |
| The FR names of the 0x419DA4 / 0x4302DC / 0x430448 / 0x10C874 sub-factor cascade (§4.2). Structure and scaling are VERIFIED-STATIC; the module names are not. | open |
| `KFWKSTT`'s x axis is "injections since start" (`anztist` = 0x7FD298 - 0x7FD26B) — VERIFIED-DYNAMIC as an index, COMMUNITY that the FR calls it `anztib`/`anztist`. | naming only |
| **Added 2026-09-17 (E2, #35):** the tick PERIOD of the after-start timer `tnst_w` 0x8011D8. `afterstart_timer` (0x0D0DFC) increments it while 0x7FE91F is set, but its task is not established, so §4's "time since the engine started turning" has no unit. | open; `procedure_e2.md` §5 asks for its slope against `raster_setB_1ms_count` in the first bench log |
| **Added 2026-09-17 (E2, #35):** which of the two `%ESSTT` twins actually runs in a given start. Both are called unconditionally from their tasks (§9.1) and both publish 0x80302C, and the FR calls the second the high-pressure-start variant, but nothing in the dump says when the ECU picks it. E2 hooks both, so the patch does not care; a calibrator does. | open; bench — log `ksta_adapted` and compare it against both `emu.start_model` tables |
| `re/med9_draft.xdf` was **not** regenerated, to avoid a conflict with the agents editing other rows of `re/calibration_draft.csv` in parallel. Rebuild it once with `python3 tools/draft_to_xdf.py re/calibration_draft.csv -o re/med9_draft.xdf --min-confidence hypothesis` after the wave is merged. | for the integrator |

## 9. Added 2026-09-17 (brief E2, issue #35) — the three hook sites: task, order and register liveness (VERIFIED-STATIC)

§3.3 and §5 named the insertion points; before writing a stub the patch needs
to know *which task each store runs in*, *whether the 10 ms producer has
already run when the store executes*, and *which registers and which of
LR / CR / CTR / XER are dead at the word being replaced*. All three answers
come from the dump alone. Reproduce every line below with

```bash
# CPU -> file offset: 0x41A680 -> 0x216680, 0x41A808 -> 0x216808,
#                     0x431384 -> 0x22D384   (tools/med9lib.py cpu_to_file)
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
    --file-off 0x216640 --addr 0x41A640 --len 0x50      # S1 site 1 + epilogue
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
    --file-off 0x2167C0 --addr 0x41A7C0 --len 0x70      # S1 site 2 + epilogue
./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
    --file-off 0x22D290 --addr 0x431290 --len 0x118     # Z1, whole function
./.venv/bin/python3 tools/find_branch_refs.py data/passat_azx_ori.bin \
    0x41A264 0x41A68C 0x431294
```

### 9.1 Which task each site runs in

**Correction to a fact quoted in the E2 brief.** The brief states that
`find_branch_refs.py` finds no `bl` and no pointer to the two `%ESSTT`
functions and that they are reached through a table. That is an artefact of
asking for the wrong address: Ghidra labels the *body* (0x41A268 / 0x41A690),
while the `bl` targets the *entry* one word earlier (`addi r11,r1,0`, the
EABI millicode prologue). Asked for the entries, both have exactly one
ordinary `bl` caller:

| Store | In function | Entry | Its only caller | Task |
|---|---|---|---|---|
| **0x41A680** `sth r31,0x303C(r13)` | `esstt_ksta` | 0x41A264 | 0x422464 | **`task_segment_a` 0x4223B0** — engine-synchronous, TCB 5, id 40, prio 0x0A |
| **0x41A808** `sth r3,0x303C(r13)` | `esstt_ksta_hdr` | 0x41A68C | 0x422530 | **`task_segment_b` 0x4224BC** — engine-synchronous, TCB 6, id 41, prio 0x0A |
| **0x431384** `stb r31,0x20A6(r13)` | `zwstt_build` | 0x431294 | 0x432B04 | **`task_100ms_int` 0x4328E4** — task set A's **10 ms** raster, id 19 |

The two segment tasks are in the *common* (event/ISR) group of §11.8 of
`re/findings/scheduler.md`, i.e. they run whichever task set is live; the
10 ms raster is set A's, and §11.8 settles that **set A is the live set**
(`zwstt` has no other producer at all, which is one half of that proof).

So the two S1 sites are **asynchronous to our 10 ms producer** — exactly like
D1's `rk` hook at 0x42247C, which sits in the same `task_segment_a` — and
their stubs must check the state-block magic. The Z1 site is not:

> **The Z1 producer provably runs first, in the same activation.** `ff_fuel`'s
> task-set-A periodic hook is the word at **0x432940**, and `zwstt_build` is
> called from **0x432B04**, both at the top level of 0x4328E4. Everything in
> between is straight-line:
>
> ```bash
> ./.venv/bin/python3 tools/blobdis.py data/passat_azx_ori.bin \
>     --file-off 0x22E940 --addr 0x432940 --len 0x1C8 | grep -vc ' bl '
> # 0   -- all 114 words from 0x432940 to 0x432B04 inclusive are `bl`
> ```
>
> No conditional branch, no early return, no loop: 114 consecutive
> argument-less `bl`. `ff_start_update()` therefore writes `zwst_add`
> 0x1C4 bytes of instruction stream before `zwstt_build` consumes it, every
> activation, with no staleness at all. The magic check in the Z1 stub is kept
> anyway (one `lwz` + `xoris` + `cmplwi`), because it costs three instructions
> and is what makes the stub inert when `ff_fuel` is not running — e.g. if the
> live set were ever B after all, in which case our tick would fire at
> 0x12067C and `zwstt_build` would never be called either.

### 9.2 Register and LR liveness at each word

All three functions use the EABI save/restore millicode, which is what settles
LR. `_savegpr_*` at 0x0B8234/0x0B8238 stores the caller's LR (captured with
`mflr r0` before the `bl`) into **4(old_sp)**, the caller's own LR slot;
`_restgpr_*_x` at 0x0B81D8/0x0B81DC reloads it, does `mtlr r0`, pops the frame
with `ori r1,r11,0` and returns with `blr` — i.e. the `bl` to the restore
helper never comes back to the caller. `zwstt_build` open-codes the same thing
(`stw r0,0x14(r1)` / `lwz r0,0x14(r1)` + `mtlr r0`).

**Consequence: at all three sites LR holds a value nobody will read again**
(the return address of the immediately preceding `bl`), and the real LR is
already in memory. A `bl` into a trampoline is free at every site, and none of
them needs a frame.

| | 0x41A680 | 0x41A808 | 0x431384 |
|---|---|---|---|
| value stored | r31 | **r3** | r31 |
| r0 | dead — reloaded by `_restgpr_28_x` from 4(r11) | dead — `_restgpr_29_x` | dead — `lwz r0,0x14(r1)` at 0x431388 |
| r11 | dead — written at 0x41A684 `addi r11,r1,0x18` | dead — written at 0x41A824 | dead — never written in the function, volatile, and three `bl`s precede the site |
| r12 | dead — last read 0x41A674, never read after | dead — written at 0x41A80C | dead — last read 0x4312B8, never read after |
| LR | dead (above) | dead (above) | dead — `mtlr r0` at 0x43138C |
| CR0 | dead — no CR read between the store and the return | dead — `cmpwi r12,0` at 0x41A810 writes it first | dead — no CR read after the store |
| CTR, XER | untouched by the whole tail | untouched | untouched |
| r1 | not touched by any stub | | |

The value register itself is *also* dead after the store at all three sites
(r31 and r3 are both reloaded or volatile), but the stubs do not rely on that
and never write r31 or r3: it costs nothing to keep the displaced instruction
a pure read of its operand, and it keeps the stub correct if a later dataset
moves the store.

That leaves **r0, r11, r12** as the scratch set at every site, which is enough
for the arithmetic S1 needs (base, value, product) with nothing spilled. None
of the three is r2 or r13, so `tools/blobdis.py --check-sda` passes.

### 9.3 Two properties of the sites that make them safe

* **S1 is NOT inert outside the start, and §3's `return` is a decompiler
  artefact.** This is the one thing in this section that had to be found by
  reading the words rather than the decompilation, and it is the reason the
  S1 stub carries a gate. §3 renders the top of `esstt_ksta` as
  `if (B_stend) { 0x80302C = 0x400; return; }` and §7 concludes "the hook is
  inert outside the start". The compiler shared the epilogue instead:

  ```
  0041A2D0  lbz   r12,-0x16cf(r13)   ; B_stend 0x7FE921
  0041A2D4  cmpwi r12,0
  0041A2D8  beq   0x41a2e4           ; not finished -> the real computation
  0041A2DC  li    r31,0x400          ; finished -> the neutral 1.0 ...
  0041A2E0  b     0x41a680           ; ... published THROUGH the hooked store
  ```

  `tools/find_branch_refs.py data/passat_azx_ori.bin 0x41A680 0x41A808
  0x431384 --no-ptr` lists exactly one branch into each word — 0x41A2E0 for
  the first, and the ordinary computation paths 0x41A7E0 and 0x431374 for the
  other two. So **0x41A680 runs on every activation of the segment task for as
  long as the engine runs**, with r31 = 0x400, and a stub that scaled it
  unconditionally would multiply the ECU's explicit "no start enrichment" by
  `f_st` at every operating point. Both S1 stubs therefore read `B_stend`
  0x7FE921 themselves and take the untouched path when it is set — the same
  condition the stock code tests, in the same cell, which makes the property
  true of the *store* instead of of one path to it.

  The **high-pressure twin is built differently**: its `bne 0x41A824` at
  0x41A6A4 jumps past the store to the epilogue, so 0x41A808 genuinely is
  unreachable once `B_stend` is set. The gate is in both stubs anyway, because
  they share the code and because the asymmetry is a compiler decision, not a
  fact anyone should depend on.

  **Z1 needs no such gate.** `zwstt_build` tests 0x7FECCA at 0x4312A8 and
  `bne 0x431388` goes to its epilogue — past the store at 0x431384. The Z1
  word is genuinely unreachable once the start has finished, and there is no
  shared-epilogue branch into it (`find_branch_refs` above).
* **Z1's offset is not clipped away by `zwmin`.** `%ZWMIN` (`zwmin_build`
  0x458E74, called from 0x45CB0C in set A's **20 ms** task 0x45CAC4) chooses
  between a map and `zwstt` on bit 3 of `cand_CWZWMN` 0x5C7972. That byte is
  **0x01** in this dataset, so bit 3 is clear and the branch at 0x458F10 is
  taken: 0x458F70 `lbz r27,0x20A6(r13)` reads `zwstt` itself and 0x458F7C
  publishes it to 0x7FD328, which `zwmin_select` 0x41D440 turns into `zwmin`
  0x7FD32A. The floor therefore moves with the value, and the advance survives
  to `zwout`. (`zwstt` has five references in all — `sda_xref.py --var
  0x802096`: the store at 0x431384, this read, the consumer at 0x41D22C in
  `zwbas_per_bank`, one read at 0x41B0CC in `%STADAP`, and the store at
  0x1137E0 which writes 0x8020**97**, the byte after it.)

### 9.4 What E2 took, and the axes it chose

Sites taken by `patches/ff_fuel` (see §7's table for the format):

| Hook | Word | Region | Replaces | Note |
|---|---|---|---|---|
| S1 site 1 | **0x41A680** | on-chip flash | `sth r31,0x303C(r13)` | `ff_st_hook_a` |
| S1 site 2 | **0x41A808** | on-chip flash | `sth r3,0x303C(r13)` | `ff_st_hook_b`, r3 not r31 |
| Z1 | **0x431384** | on-chip flash | `stb r31,0x20A6(r13)` | `ff_zwst_hook` |

`ff_fst_map` is 6 (E) x 6 (`tmst`), Q10, and its `tmst` axis is six of the
twelve `KFWKSTT` breakpoints (0x5C6C62, §3.1) so every cell lines up with a
stock row:

| count | 24 | 44 | 64 | 91 | 117 | 184 |
|---|---|---|---|---|---|---|
| °C | -30 | -15 | 0 | +20.25 | +39.75 | +90 |

Why these six of the twelve. The stock cranking factor is strongly convex in
`tmst` (22.8x at -30 °C, 2.1x at 90 °C, §6), so the breakpoints have to be
dense where it moves fastest and may be sparse where it is flat; -30 / -15 / 0
covers the steep part at one breakpoint every 15 °C, +20.25 and +39.75 bracket
the warm-start region where the calibration starts at 1.2x, and +90 pins the
fully warm end. **+39.75 °C is also the default `ff_zwst_tmax`**, so the
temperature at which the start *advance* is switched off is exactly a
breakpoint of the *fuel* map rather than a number between two of them.

The E axis is `ff_fst_e_axis[6]` = 0, 20, 40, 60, 85, 100 % — the one §7
recommends, with 85 as a breakpoint because E85 is the calibration target.
Both axes are calibration, not compiled constants, and both are checked
strictly increasing by `ffcal001.py`.
