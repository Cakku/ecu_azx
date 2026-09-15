# Rail pressure control (HDR) and the injection window (AWEA)

Agent B9, brief `docs/agent_briefs/B9_rail_pressure_and_window.md`, issue #17.
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
mkdir -p /tmp/ghidra_B9
cp -R /Users/carlo/ecu_azx/ghidra_projects/med9.gpr \
      /Users/carlo/ecu_azx/ghidra_projects/med9.rep /tmp/ghidra_B9/
export GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec
/Users/carlo/ecu_azx/.venv/bin/python -m pyghidra.ghidra_launch \
    --install-dir "$GHIDRA_INSTALL_DIR" \
    ghidra.app.util.headless.AnalyzeHeadless /tmp/ghidra_B9 med9 \
    -process passat_azx_ori.bin -noanalysis \
    -scriptPath ghidra_scripts -postScript import_symbols.py "$PWD"
# -> 227 functions named, 222 labels created, 3 skipped (0x400000, 0x702000,
#    0x707000 have no memory block)
```

Read-only disassembly / decompilation from the command line (safe in parallel
with other agents):

```bash
GHIDRA_INSTALL_DIR=/usr/local/Cellar/ghidra/12.1.3/libexec \
/Users/carlo/ecu_azx/.venv/bin/python ghidra_scripts/decompile.py \
    --project-dir /tmp/ghidra_B9 --project-name med9 0x<addr>
```

`r2 = 0x5C9FF0`, `r13 = 0x7FFFF0` (application SDA bases,
`re/findings/scheduler.md` §7). Every r13-relative reference list below was
produced by a full-image D-form scan equivalent to
`tools/callgraph.py --sda`; the reproduction script is
`tools/sda_xref.py` (added by this brief).

---

## 1. The answer in one table

| What | Address | Tag |
|---|---|---|
| **`prsoll` rail-pressure setpoint, u16** | RAM **0x8031F4** | VERIFIED-STATIC |
| **`prist` rail-pressure actual, u16** | RAM 0x8031DA (B6) | VERIFIED-STATIC |
| **`prdiff` = prsoll - prist, s16** | RAM **0x8031CA** | VERIFIED-STATIC |
| **`hdrpsol_main`** — setpoint module (`%HDRPSOL`) | **0x45822C** | VERIFIED-STATIC |
| **`hdr_pi_controller`** — PI controller (`%HDR`) | **0x457BC8** | VERIFIED-STATIC |
| **`hdrpist_select`** — actual-value select / substitute (`%HDRPIST`) | **0x4580A4** | VERIFIED-STATIC |
| **`vstmsv_main`** — MSV feed-forward + rail model (`%VSTMSV`) | **0x4589B4** | VERIFIED-STATIC |
| **`amsv_main`** — MSV drive, pump volume limit (`%AMSV`) | **0x4565F0** | VERIFIED-STATIC |
| **`KFPRSOLHOM`** homogeneous setpoint map, 8x8 u16 | **0x5D5324** | VERIFIED-STATIC |
| **`KFPRSOLSCH`** stratified setpoint map, 8x8 u16 | **0x5D52A4** | VERIFIED-STATIC |
| **`KFPRSOLHKS`** / mode-3 map, 8x8 u16 | **0x5D54A4** | VERIFIED-STATIC |
| **`KFPRSOLHMM`** / mode-4 map, 8x8 u16 | **0x5D53A4** | VERIFIED-STATIC |
| **`KFPRSOLKH`** cat-heating / start map, 8x8 u16 | **0x5D5224** | VERIFIED-STATIC |
| **`KFPRSOLOFF`** additive offset map, 8x8 u16 | **0x5D5424** | VERIFIED-STATIC |
| shared setpoint **load axis** `{u16 n=8; u16 axis[8]}` | **0x5D5578** | VERIFIED-STATIC |
| shared setpoint **nmot axis** `{u16 n=8; u16 axis[8]}` | **0x5D558A** | VERIFIED-STATIC |
| **`KLPRMAX`** setpoint ceiling, 6-point curve, all **22000 = 110.0 bar** | **0x5D553E** | VERIFIED-STATIC |
| **`PRSOLMN`** setpoint floor, u16 = **7000 = 35.0 bar** | **0x5D5572** | VERIFIED-STATIC |
| **`CWPRSOL`** setpoint code word, u8 = **0x2E** | **0x5D521E** | VERIFIED-STATIC |
| **`VHDPMX`** pump delivery ceiling, u16 = **25120** | **0x5D559C** | VERIFIED-STATIC |
| **`KLGPHDR`** P-gain curve over `prdiff`, 7 points | **0x5D51C0** | VERIFIED-STATIC |
| **`KLBIHDR`** I-gain curve over `prdiff`, 7 points | **0x5D51DE** | VERIFIED-STATIC |
| **MSV volume limit** `VMSVMX`, u16 = **5000** | **0x5D4BC6** | VERIFIED-STATIC |
| **`CWAMSV`** MSV code word, u8 = **0xD3** | **0x5D49CC** | VERIFIED-STATIC |
| **1 LSB of every rail pressure = 0.005 bar (5 mbar)** | — | see §2 |

---

## 2. The unit of `prist` / `prsoll` / `dp`: **0.005 bar per LSB**

B6 left the unit open and floated 0.01 bar/LSB
(`re/findings/injection.md` §11). **That is refuted.** Four independent
readings agree on **0.005 bar/LSB**, i.e. 5 mbar, i.e. 200 LSB per bar.

**(a) `KLPRMAX` = 22000.** The setpoint ceiling at 0x5D553E is a 6-point
curve whose six values are all **22000** (§3.3). 22000 x 0.005 =
**110.0 bar** — the published VW maximum rail pressure for the FSI
high-pressure circuit. At 0.01 bar/LSB it would be 220 bar, which no
2004 FSI pump, rail or sensor reaches. **VERIFIED-STATIC** for the number,
**COMMUNITY** for "110 bar is the spec".

**(b) `KFPRSOLHOM` is a textbook FSI pressure map.** With 0.005 bar/LSB the
homogeneous map at 0x5D5324 reads (rows = nmot, columns = load):

```
              load  0%   10%   20%   30%   40%   60%   80%  100%
  750 rpm           35    35    40    45    50    50    50    50   bar
 1250 rpm           40    45    50    60    60    60    60    60
 2000 rpm           55    55    60    65    70    70    70    70
 2500 rpm           65    65    65    70    80    80    80    80
 3500 rpm           65    75    75    80    90    90    90    90
 4500 rpm           80    90    95    95    95    95    95    95
 5700 rpm           90    90    95    95    95    95    95    95
 6200 rpm           90    95    95    95    95    95    95    95
```

35 bar at idle rising to 95 bar at high speed and load is exactly the
documented FSI operating range. At 0.01 bar/LSB the idle value would be
70 bar and full load 190 bar.

**(c) The sensor characteristic.** `%GGDSKV` converts the averaged rail
pressure ADC (`0x802030`) through the self-describing 3-point curve at
**0x5C5D518A**... corrected: **0x5D518A** (`lookup_1d_u16` call at
0x457B90 region, see §4.1):

```
0x5D518A:  n = 3
  axis (ADC, 10-bit sample << 4, so 16384 = 5.000 V):  1632, 5899, 16384
  values:                                                  0,  9000, 31120
```

The two segments have identical slope (9000/4267 = 2.1092;
22120/10485 = 2.1097), i.e. the curve is one straight line. 1632/16384 x 5 V
= **0.498 V at zero pressure** — the canonical ratiometric 0.5 V offset of a
Bosch high-pressure sensor. The span to 4.5 V (ADC 14746) is 27664 LSB;
at 0.005 bar/LSB that is a **138 bar** sensor full scale, i.e. a nominal
0-140 bar part used on a 110 bar circuit. At 0.01 bar/LSB it would be a
277 bar sensor. **VERIFIED-STATIC** for the curve, **HYPOTHESIS** for the
0.5/4.5 V reading.

**(d) The `dp` axis.** B6's shared differential-pressure axis 0x5D3DBE runs
`400 .. 24000`; at 0.005 bar/LSB that is **2 .. 120 bar**, which brackets the
110 bar ceiling with the usual margin. At 0.01 it would be 4 .. 240 bar.

**Consequence for B6's numbers.** `KLTIKRPR x sqrt(dp) = 566,900` with
`dp` in 0.005 bar units. `KRKATE` = 3858 and `frt` are unchanged; only the
physical label of the axis moves.

**The VCDS route agrees but does not settle it alone.** The measuring
handlers transmit `prist` and `prsoll` **divided by two** with display format
**0x53**:

```
0003DB94  lhz    r6,0x31EA(r13)        ; prist  (u16)
0003DB98  li     r3,0x53               ; display format 0x53
0003DB9C  rlwinm r5,r6,31,17,31        ; r5 = prist >> 1
0003DBA0  sth    r5,0x2AE2(r13)        ; B  (16-bit payload)
0003DBA4  rlwinm r4,r5,24,24,31        ; A  = high byte
0003DBA8  b      0x00038EB4
0003DBAC  lhz    r6,0x3204(r13)        ; prsoll (u16), identical treatment
```

`prist >> 1` in 0.01 bar units is what a tester displaying "bar with two
decimals" wants: 22000 -> 11000 -> 110.00 bar. **VERIFIED-STATIC** that the
tester value is `prist/2`; the meaning of format 0x53 itself is **open** (no
tester log yet). Measuring ids: **500 = `prist` (0x8031DA)**,
**501 = `prsoll` (0x8031F4)**, **516 = 0x8031CC**, **1691 = `prdiff`
(0x8031CA)**, **1687 = 0x8031D2** (`re/measuring_vars.csv`, A3).

---

## 3. `%HDRPSOL` — the setpoint, `hdrpsol_main` at 0x45822C

One process, called once per activation of the on-chip task at 0x45CAC4
(`bl` at 0x45CC08; see §7 for the raster problem).

### 3.1 The map bank and its axes

```
0045827C  bl axis_search_u16_hint(0x5D5578, [0x803508], hint 0x7FD5D0)   ; load key
00458290  bl axis_search_u16_hint(0x5D558A, nmot_w,     hint 0x7FD5D4)   ; nmot key
   ... interp_2d_u16(<map>, nx = u16@0x5D5578 = 8, key_y = nmot, key_x = load)
```

`interp_2d_u16(val, nx, key_y, key_x)` indexes `val[iy*nx + ix]`
(`re/symbols.csv`, 0x40C444), so **rows are `nmot`, columns are load**.

| Axis | Address | Breakpoints | Physical |
|---|---|---|---|
| columns (x) | 0x5D5578 | `{n=8}` 0, 6554, 13107, 19661, 26214, 39322, 52429, 65535 | 0, 10, 20, 30, 40, 60, 80, 100 % of full scale |
| rows (y) | 0x5D558A | `{n=8}` 3000, 5000, 8000, 10000, 14000, 18000, 22800, 24800 | 750, 1250, 2000, 2500, 3500, 4500, 5700, 6200 min^-1 (1/4 rpm per LSB) |

The x input is **RAM 0x803508**, written at 0x433884 inside the torque-
structure function `FUN_004336E0` (0x4336E0-0x434C13) as
`0x803508 = min(2 * v, u8@0x5C8C0C << 8)` with `u8@0x5C8C0C = 254`, i.e.
capped at 65024. It is the **relative charge / torque request**, the FR's
`rlsol_w` (**HYPOTHESIS** for the FR name; **VERIFIED-STATIC** that it is
produced by the torque structure and that the breakpoints are 10 % steps of
a 16-bit full scale).

### 3.2 Map selection (all six maps share the two axes above)

`bVar2 = CWPRSOL = u8 @ 0x5D521E = 0x2E` (0b0010_1110).

```
if (CWPRSOL & 1)                      -> prsoll_raw = u16 @ 0x5D5574   (fixed value, OFF here)
else if (0x7FD04D & 1)                -> prsoll_raw = previous 0x8031F0
else switch on the mode bits of 0x7FB69A / 0x80156F:
    0x7FB69A & 0x80                   -> KFPRSOLKH   0x5D5224
    0x7FB69A & 0x10 | & 4 | 0x80156F&1-> KFPRSOLHMM  0x5D53A4
    0x7FB69A & 8                      -> KFPRSOLHKS  0x5D54A4   (+ offset if CWPRSOL & 0x20)
    0x7FB69A & 2                      -> KFPRSOLSCH  0x5D52A4
    else (homogeneous)                -> KFPRSOLHOM  0x5D5324   (+ offset if CWPRSOL & 0x20)
0x8031F0 = prsoll_raw
```

**`CWPRSOL & 0x20` is set (0x2E & 0x20 = 0x20), so the offset map is live**
on the homogeneous and the `0x7FB69A & 8` paths:

```
off = interp_2d_u16(KFPRSOLOFF 0x5D5424, 8, key_nmot, key_load)
fac = lookup_1d_g_u8_s8(n=6 @0x5D5552, axis 0x5D5553 = {97,124,151,171,184,197},
                        val 0x5D5559 = {127,127,64,0,0,0}, x = u8 @ 0x7FD3F7)
prsoll_raw = clamp(map + ((fac * off) >> 7), 0, 0xFFFF)
```

`KFPRSOLOFF` peaks at 5000 = **+25 bar** at 2000 rpm / 20-30 % load and is 0
above 5700 rpm. `0x7FD3F7` is a u8 temperature (the same variable indexes
`KLPRMAX`); read as the usual Bosch u8 temperature (0.75 °C/LSB, -48 °C
offset) the fade-out runs 25 °C -> 80 °C, i.e. this is a **warm-up rail
pressure raise**. **VERIFIED-STATIC** for the arithmetic, **HYPOTHESIS** for
the temperature interpretation.

Map values (all in 0.005 bar):

| Map | min | max | in bar |
|---|---|---|---|
| `KFPRSOLHOM` 0x5D5324 | 7000 | 19000 | 35 .. 95 |
| `KFPRSOLSCH` 0x5D52A4 | 10000 | 20000 | 50 .. 100 |
| `KFPRSOLHKS` 0x5D54A4 | 12000 | 20000 | 60 .. 100 |
| `KFPRSOLHMM` 0x5D53A4 | 12000 | 19000 | 60 .. 95 |
| `KFPRSOLKH` 0x5D5224 | 7000 | 19000 | 35 .. 95 |
| `KFPRSOLOFF` 0x5D5424 | 0 | 5000 | 0 .. +25 |

### 3.3 Setpoint limits

```
0x8031EC = lookup_1d_g_u8_u16(n=6 @0x5D553E, axis 0x5D553F = {49,56,63,68,72,77},
                              val 0x5D5546 = {22000 x 6}, x = u8 @ 0x7FD3F7)   ; KLPRMAX
0x8031F2 = prsoll_raw
if (prsoll_raw < KLPRMAX) {
    if (prsoll_raw < 0x5D5572 = 7000) 0x8031F2 = 7000                          ; floor
} else 0x8031F2 = KLPRMAX                                                      ; ceiling
```

So the **hard window of the setpoint is 7000 .. 22000 = 35.0 .. 110.0 bar**,
and the ceiling `KLPRMAX` (0x5D5546, six u16 = 22000) is **the single
calibration constant that caps any flex-fuel rail raise**.

### 3.4 The pump volume limit as the setpoint rate limiter

This is the `VHDP` role the brief asks about. It is a **rate** limit on
`prsoll`, not a value limit:

```
0x8031F6 = (clamp(VHDPMX(0x5D559C=25120) - vhdp_dem(0x803212), 0, 0xFFFF)
            * lookup_1d_u16(0x5D5524, 0x803202)) >> 14        ; spare pump volume
           (the curve 0x5D5524 is {n=6; axis 10000,12000,14000,16000,18000,22000;
            values 1638 x 6} = a flat 0.09998 over the whole pressure range)
0x8031E4 = mul_div_sat(nmot_w, 0x2000, u16@0x5D5222 = 8000)    ; ~ 1.024 * nmot, strokes/time
0x8031EA = (kaparail(0x8031FE) * u16@0x5D58F0 = 20133) >> 15   ; volume per bar (rail + fuel
                                                               ; compressibility, see 4.4)
0x8031E8 = min( ((0x8031F6 * 0x8031E4) >> 3) / 0x8031EA , 0xFFFF)   ; max RISE per step
0x8031E6 = min( ((0x803208  * 0x8031E4) >> 3) / 0x8031EA , 0xFFFF)
           * (u16@0x5D5220 = 16384) >> 14                          ; max FALL per step
0x8031EE = clamp(0x8031F2, prsoll_prev - 0x8031E6, prsoll_prev + 0x8031E8)
```

(`mul_div_sat` = `FUN_004101A0` = `min(a*b/c, 0xFFFF)`; the two `>>3` /
`/0x8031EA` pairs are `FUN_0040C2BC`, a saturating 32/16 divide fed the
product split across two arguments.)

**In words: the setpoint may only climb as fast as the spare high-pressure
pump volume, divided by the compressibility of the rail, times engine
speed.** Demanding more pressure than the pump can build does not overshoot;
it only makes `prsoll` ramp. That is exactly the FR's `VHDP` / `KLNEHDP`
behaviour and it is the mechanism that will bite first if a flex-fuel
strategy raises the setpoint at WOT.

### 3.5 Output stage of the setpoint

```
_0x8031E0 = PT1(lookup_1d_u16(0x5D5560, nmot_w), 0x8031EE)     ; filtered variant
0x8031F4  = prist_w                                            ; default: track the actual value
if (!(CWPRSOL & 8 && 0x7FD2D8 & 4) && !(0x7FD2F5 & 2)) {
    0x8031F4 = 0x8031EE;                                       ; normal
    if (CWPRSOL & 0x10) 0x8031F4 = 0x8031E0;                   ; filtered (OFF: 0x2E & 0x10 = 0)
}
```

`CWPRSOL = 0x2E` has bit 3 (0x08) set and bit 4 (0x10) clear, so in this
dataset **`prsoll` (0x8031F4) is the rate-limited, `KLPRMAX`-clamped map
value 0x8031EE**, unfiltered, and it falls back to tracking `prist` while
the `0x7FD2D8 & 4` (start / no-control) condition holds.

---

## 4. `%HDR` — the PI controller, `hdr_pi_controller` at 0x457BC8

`bl` at 0x45CC0C, immediately after the setpoint process.

### 4.1 Structure

```
0x8031CA = clamp(prsoll(0x8031F4) - prist(0x8031DA), -32768, 32767)      ; prdiff

; --- P branch ---
gp   = lookup_1d_g_s16_u16(n=7 @0x5D51C0, axis 0x5D51C2, val 0x5D51D0, prdiff)   ; KLGPHDR
gpn  = lookup_1d_u8(0x5D519D, nmot_u8)                                           ; FWPHDR
0x8031BC = min((gp * gpn) >> 7, 0xFFFF)
0x8031C2 = rate_limit(0x8031BC, prdiff, state 0x7FB224, up 0x5D520E, dn 0x5D520C) ; FUN_0040CBF8
0x8031C4 = 0x8031C2                                                              ; hdrpp_w

; --- D-ish / proportional-on-error branch ---
gi   = lookup_1d_g_s16_u16(n=7 @0x5D51DE, axis 0x5D51E0, val 0x5D51EE, prdiff)   ; KLBIHDR
gin  = lookup_1d_u8(0x5D51AE, nmot_u8)                                           ; FWIHDR
0x8031BE = min((gi * gin) >> 7, 0xFFFF)
0x8031C6 = clamp((prdiff * 0x8031BE) >> 14, -32768, 32767)
0x8031C8 = clamp(0x8031C6 + 0x8031C4, -32768, 32767)

; --- I branch (only while every enable bit in 0x7FD2F4 is set) ---
0x8031C0 = integrate(0x5D5212, 0x8031C4, state 0x7FB228,
                     upper 0x5D5208, lower 0x5D520A)                             ; FUN_0040CBF8
0x7F9422 = 0x8031C0                                                              ; mirrored for diag
0x8031CC = 0x8031C0
if (!(0x7FD2F4 & 1)) 0x8031CC = clamp(0x8031C8 + 0x8031C0, -32768, 32767)        ; hdrr_w

; --- to volume ---
0x8031D2 = clamp(((clamp((0x8031CC * kaparail 0x8031FE) >> 12) * 20133) >> 13))   ; the actuator
                                                                                 ; contribution
```

`KLGPHDR` (0x5D51C0): axis (s16) `-3018, -1000, -300, 0, 300, 1000, 3000`
(= -15.1, -5, -1.5, 0, +1.5, +5, +15 bar), values
`456, 561, 655, 918, 655, 561, 457` — gain highest on small errors, rolled
off on large ones. `KLBIHDR` (0x5D51DE) uses the same axis shape.

### 4.2 Integrator enable and anti-windup

The I branch runs only when **every** bit of `0x7FD2F4` is set:
bit 1 (set in `%AMSV`, §5.2 — "actuator not on its limit"), bit 2
(`0x80201E & 2` sensor OK and no `0x7FECF8` / `0x7FD2D8 & 2`), bit 5
(nmot inside `0x5D5204 = 6000` .. `0x5D5206 = 14000`, i.e. **1500 .. 3500
min^-1**), bit 6 (`|0x8031CE| < 0x5D5210`, the setpoint is settled), bit 7
(`0x801F99 < 0x5D51BF`, a temperature gate), bit 3 (`0x803200` inside
`0x5D51FC`..`0x5D51FE`) and bit 4 (`0x802BEA` inside `0x5D5200`..
`0x5D5202`). `0x5D519C & 2` is the master enable.

`0x8031D0` is a PT1 of `prsoll` (coefficient `0x5D5214`) and
`0x8031CE = prsoll - 0x8031D0` is the "setpoint is moving" detector.

### 4.3 Where the controller output goes

**`0x8031D2` is the HDR actuator output**, and its only consumers are
measuring id 1687 (handler 0x0442E4, format 0x63) and `%AMSV` at 0x456634.

### 4.4 `%VSTMSV` — feed-forward and the rail model, `vstmsv_main` at 0x4589B4

```
0x803202 = prist_w, or prsoll if (CWVSTMSV 0x5D559E & 2) or (0x7F9CB0 & 1)
0x8031FE = lookup_2d_g_u8_u16_u16(nx@0x5D5608, xaxis 0x5D560A, ny@0x5D5609,
                                  yaxis 0x5D5614, val 0x5D5628,
                                  0x7FD2FA, 0x803202)          ; KFKAPAKR, kaparail_w
0x8031F8 = ((0x80303A * 0x5D5604) >> 3) * nmot_u8 >> 11        ; fuel volume flow demand
0x803200 = that, after the 0xAB/0x100 and 0x5D559F corrections
0x803208 = mul_div_sat(0x803200, 0x1000, 0x803204)             ; normalised demand
0x80320E = mul_sat(0x803200, interp_2d_u16(0x5D574A, ...))
           + interp_2d_u16(0x5D57AA, ...)                      ; KFVSTVG / KFVSTVO
0x803210 = (0x80320E * lookup_1d_u16(0x5D5894, temp)) >> 14    ; KLTMVST
0x803212 = 0x803210                                            ; the steady-state pump volume
                                                               ; demand -- the input of the
                                                               ; volume headroom in 3.4
0x8031FC = prsoll - prsoll_prev(0x80320C)
0x8031FA = (0x8031FC * 0x5D5222) / nmot                        ; d(prsoll)/d(stroke)
0x803206 = ((((0x8031FA * 20133) >> 16) * lookup_1d_u16(0x5D587A, prist)) >> 16)
           * 0x8031FE >> 7                                     ; transient volume
0x80320A = clamp(0x803212 + 0x803206, 0, 0xFFFF)               ; total feed-forward
```

`0x8031FE` (`kaparail_w`, the FR's `KFKAPAKR`) is the volume-per-bar factor
that appears both in the setpoint rate limiter (§3.4) and in the controller
output conversion (§4.1).

---

## 5. `%AMSV` — MSV drive and the pump volume limit, `amsv_main` at 0x4565F0

### 5.1 The actuator chain

```
CWAMSV = u8 @ 0x5D49CC = 0xD3   (bits 0,1,4,6,7 set; bit 5 clear)

if (CWAMSV & 0x20)  v = u16 @ 0x5D4BD0                       ; fixed (OFF here)
else if (CWAMSV & 1) v = clamp(0x80320A + 0x8031D2, 0, 0xFFFF)  ; feed-forward + PI   <-- active
else                 v = 0x80320A                            ; feed-forward only
0x803176 = v                                                 ; total volume demand
0x80316C = mul_div_sat(v, 0xC6F, u16 @ 0x5D4BC4 = 16000)
0x80316E = min(0x80316C, u16 @ 0x5D4BC6 = 5000)              ; *** THE PUMP VOLUME LIMIT ***
0x803166 = lookup_1d_g_u16_s16(n @0x5D4AFC, axis 0x5D4AFE, val 0x5D4B44, 0x80316E)
                                                             ; volume -> MSV closing angle
0x803162 = clamp(0x803166, s16 @0x5D49D2 = 0, s16 @0x5D49D4 = 1100)
0x803168 = 0x80317A (+0x803162 when CWAMSV & 0x10)           ; dwmsvo_w  (MSV open angle)
0x803164 = clamp(0x803178 - 0x803168, 0x80316A, s16 @0x5D49DA)  ; dwmsvs_w (MSV close angle)
```

**`0x80316E` is the delivered-volume request after the pump limit and
`0x803164`/`0x803168` are the two MSV drive angles handed to the output
stage.** `0x5D4BC6 = 5000` (out of the 0xFFFF scale of `0x80316C`) is the
single constant that caps the pump's delivery per stroke.

### 5.2 The "controller on its limit" bit — the HDR anti-windup

```
if ((prdiff(0x8031CA) < 1  || 0x803166 < s16@0x5D49D4=1100) &&
    (prdiff >= 0           || s16@0x5D49D2=0 < 0x803166))
        0x7FD2D9 &= ~2;          ; MSV angle is inside its range
else    0x7FD2D9 |=  2;          ; MSV angle pinned at a limit in the direction of the error
```

`0x7FD2D9` bit 1 is read back by `%HDR` (`(byte)DAT_007fd2d8 & 2` at
0x457BCC, the byte above 0x7FD2D8) and by `%HDRPSOL`; it is the
**"rail pressure control cannot follow" flag**. There is no torque
intervention attached to it in this software — it only freezes the
integrator and forces `prsoll` to track `prist`.

---

## 6. `%HDRPIST` — the actual value, `hdrpist_select` at 0x4580A4

Two independent acquisitions of the same sensor are cross-checked:

```
0x8031DE = clamp(u32@0x803438 / 5 - u16@0x5D5216, 0, 0xFFFF)   ; fast path
0x80202E = lookup_1d_u16(0x5D518A, 0x802030) + (0x800EE8 >> 7) ; averaged path (sensor curve,
                                                               ; plus the offset adaptation)
0x8031D8 = clamp(0x80202E - 0x8031DE, -32768, 32767)           ; deviation
|0x8031D8| > u16@0x5D5218 = 400  (2.0 bar)   -> 0x7FEA6C = 0
|0x8031D8| < 400 - u16@0x5D521A = 300 (1.5 bar) -> 0x7FEA6C = 1   (hysteresis)

if (0x80201E & 0x10)          prist = substitute 0x80201A
else if (0x7FEA6C)            prist = 0x8031DE
else                          prist = PT1(u16@0x5D521C = 26214 (0.4 in Q16), 0x80202E)
```

`0x5D5216 = 0` is the raw offset; the running offset adaptation lives in
`0x800EE8` (`profadp_w`). The averaged ADC `0x802030` is
`FUN_0040FFF0(samples_sum << 4, sample_count)`, i.e. a plain mean of the
10-bit samples scaled to 14 bits.

---

## 7. Raster: all of `%HDR*` runs in **one** task, and the period is doubtful

Every process above is called exactly once, from one flat `bl` list:

```
0045CC00  bl 0x00457A88   ; sensor average + curve  (also called from 0x430E24)
0045CC04  bl 0x004580A4   ; %HDRPIST  select / substitute
0045CC08  bl 0x0045822C   ; %HDRPSOL  setpoint
0045CC0C  bl 0x00457BC8   ; %HDR      PI controller
0045CC10  bl 0x004589B4   ; %VSTMSV   feed-forward + rail model
0045CC14  bl 0x00457734   ; rail sensor diagnosis
0045CC18  bl 0x004565F0   ; %AMSV     MSV drive + pump volume limit
...
0045CCEC  bl 0x00455040   ; rkti_pre  (B6: frt / tv / fcorr)
```

**VERIFIED-STATIC** (full-image `bl` scan, `tools/blscan`-equivalent: each of
these targets has exactly the one call site listed).

That list is inside the on-chip task whose entry is **0x45CAC4**, which B1
recorded as **TCB 11, priority 0x08, "1000 ms (HYPOTHESIS; <= 1500 ms
VERIFIED)"** (`re/findings/scheduler.md` §§ around line 177 and 267).

**A rail-pressure PI controller and an MSV feed-forward cannot run at 1 Hz.**
Either B1's period hypothesis for priority 0x08 is wrong, or this task is
re-activated from somewhere the static scan does not see. This is recorded
as an open item in §11; it does not change any address here, but it does
change the latency estimate for B6's option C/D (`KRKATE` / `frt`) and it
should be settled before anyone calibrates a rail raise.

---

(§8 onwards: the injection window. Written next.)
