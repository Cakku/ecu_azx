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
