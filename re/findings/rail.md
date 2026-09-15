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

**(c) The sensor characteristic.** `%GGDSKV` (`ggdskv_sensor`, 0x457A88)
converts the averaged rail-pressure ADC (`0x802030`) through the
self-describing 3-point curve at **0x5D518A**, via `lookup_1d_u16`:

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

## 8. The injection window — units first

The window arithmetic is in the **angle** domain, and its unit follows from
the one multiply that converts `ti` into an angle (the eight instructions
immediately before `awea_ti_to_angle`):

```
0041B9A4  lhz    r12,-0x117C(r13)   ; nmot_w  @ 0x7FEE74, 1 LSB = 0.25 min^-1
0041B9A8  lis    r11,0x0
0041B9AC  ori    r11,r11,0x8638     ; 34360
0041B9B0  mullw  r12,r12,r11
0041B9B4  rlwinm r12,r12,16,16,31   ; >> 16
0041B9B8  sth    r12,0x3082(r13)    ; k_nmot @ 0x803072
```

so `k_nmot = (nmot_w * 34360) >> 16 = rpm * 2.0971680`, and
`dwi = (ti * k_nmot) >> 13` (0x41BB58, B6). Substituting the physical
identity `angle[degCA] = t[us] * rpm * 6e-6`:

```
A  =  T * 8192 * 6e-6 / 2.0971680  =  T * 0.0234375        (A = degCA per angle LSB,
                                                            T = us per ti LSB)
```

**The angle LSB is 3/128 degCA = 0.0234375 degCA.** Two independent reasons:
(a) every u8 angle map in `%AWEA` is scaled by `* 0x20` (32) before use, and
32 x 3/128 = **0.75 degCA per map count** — the very resolution B7 proved for
the whole ignition chain (`re/findings/ignition.md`); (b) 720 degCA is then
exactly **0x7800**, and the hard latest-start constant (§9.2) works out to
exactly **360.0 degCA**. Neither is true for any neighbouring resolution.

**Therefore T = 1.000, i.e. `ti` is 1 us per LSB.** The fit is
`2.560117e-4` (from 34360) against `2.560000e-4` (from 1 us and 3/128 degCA),
0.005 % apart. **This closes the first open item of
`re/findings/injection.md` §11**: `TIMINP` = 900 is **0.9 ms**, the `KLHDEV`
axis 550..6500 is 0.55..6.5 ms and the `FKKVS` y axis 0.5..7.0 ms.
**VERIFIED-STATIC** (arithmetic of 0x41B9A4-0x41B9B8 plus B7's 0.75 degCA).

## 9. `%AWEA` — where the window is checked

Two processes:

| Part | Function | Call site |
|---|---|---|
| time-synchronous (angles + margins) | **`awea_angles` 0x45487C** (0x454880-0x454D9F) | `bl` at **0x45CCE8**, the task of §7 |
| angle-synchronous (the check itself) | **`awea_ti_to_angle` 0x41B9C0** (B6) | `bl` at **0x422484**, `task_segment_a` |

### 9.1 The two window terms

```
; --- awea_angles 0x45487C, head ---
if (prist_w > u16 @ 0x5D3CDC = 2600)          ; 13.0 bar
     0x7FEA48 = 1                             ; model invalid above that, see 12.4
else {
     0x7FEA48 = 0
     0x80306E = min((prist_w * 2) << 16 / 0x7FEFAE, 0xFFFF)     ; pressure ratio
     0x7FD290 = lookup_1d_g_u16_u8(n=6 @0x5D3BE8, axis 0x5D3BEA, val 0x5D3BF6, 0x80306E)
     if ((s8)0x7FEF85 > 0 && 0x7FD290 <= 0x7FEF85) 0x7FD290 = 0x7FEF85
}
0x7FD28F = lookup_1d_u8(n=6 @0x5D3BDB, axis 0x5D3BDC, val 0x5D3BE2, u8 @ 0x7FD407)
```

| Term | Table | Contents in this dataset | Physical |
|---|---|---|---|
| **`0x7FD290`** required end-of-injection margin | 0x5D3BE8, axis `{1024, 2181, 3717, 7557, 16261, 28416}`, values **`{67 x 6}`** | constant **67** | **50.25 degCA** |
| **`0x7FD28F`** latest permitted start | 0x5D3BDB, axis `{24, 77, 104, 131, 157, 224}`, values **`{200 x 6}`** | constant **200** (init default 0xC8 = 200 at 0x131250) | **150 degCA** above the 0x2300 base = **360.0 degCA** |

Both curves are **flat** in this dataset, so the window is effectively two
scalars. That is good news for the flex-fuel work: the margin does not move
with rail pressure today, and making it move is a pure calibration change.

### 9.2 The start-of-injection angle and the base

```
; --- awea_angles, homogeneous branch (0x803094 & 1) ---
if (0x7FE920 != 0)                              ; cranking
     wbho1s = lookup_2d_u8(0x5D3A01 | 0x5D3AD3, 0x7FD298, 0x7FD3F7) * 0x20 + 0x2300
else if (0x8033FA & 0x400)  wbho1s = s16 @ 0x5D3CDE
else {
     wbho1s = interp_2d_u8((0x7FD419 > u8@0x5D3970=25) ? KFWBHO1SW  0x5D3A83
                                                       : KFWBHO1SWE 0x5D3A33,
                           nx = u8 @ 0x5C88DB = 8, key_y = 0x7FD804, key_x = 0x7FD83C)
              * 0x20 + 0x2300
     if (0x7FEA49 == 0)
         wbho1s += (interp_2d_s8(KFDWBHO1SK 0x5D3971, ...) * u8@0x7FD28E) >> 3
}
0x803078 = wbho1s
```

**The two window maps are 8 columns x 10 rows of u8, 0.75 degCA per count,
plus the 0x2300 = 210.0 degCA base**, with the axes

* rows `0x7FD804` = `axis_search_u8(0x5C88A8, u8 nmot @ 0x7FCE95)`,
  `{n=10}` **19, 25, 38, 50, 63, 75, 88, 100, 125, 155** = about
  **760, 1000, 1520, 2000, 2520, 3000, 3520, 4000, 5000, 6200 min^-1**
  (40 min^-1 per LSB, A3's measuring id 1);
* columns `0x7FD83C` = `axis_search_u8(0x5C88DB, u8 rl @ 0x7FEF74)`,
  `{n=8}` **13, 27, 40, 53, 67, 80, 93, 107** (relative charge, A3's
  measuring id 2; the percent scaling of that u8 is still open).

Both axis searches are done once, at 0x115C08 / 0x115C18, and shared.

| Map | Address | raw range | degCA (base 210 included) |
|---|---|---|---|
| **`KFWBHO1SW`** 0x5D3A83 | 8x10 u8 | 0 .. 160 | **210 .. 330** |
| **`KFWBHO1SWE`** 0x5D3A33 | 8x10 u8 | 80 .. 240 | **270 .. 390** |
| **`KFDWBHO1SK`** 0x5D3971 | 8x10 s8 | -64 .. 0 | -48 .. 0 (x `0x7FD28E`/8) |
| start-branch maps 0x5D3A01 / 0x5D3AD3 | 2-D u8 over (0x7FD298, 0x7FD3F7) | | |
| fallback `wbho1s` | s16 @ 0x5D3CDE | | |

### 9.3 The check itself, in `awea_ti_to_angle` (0x41B9C0)

```
if ((0x7FEA48 == 0 && 0x7FE91F != 0) || (0x80201E & 0x20)) {
    dwi = min((ti_hom(0x8030E8) * k_nmot(0x803072)) >> 13, 0x7FFF)
    0x803088 = dwi                                            ; the logging variable
    if ( (s16)(wbho1s(0x80307E) - dwi)  <=  (s16)((u8@0x5D396E + u8@0x7FD290) * 0x20) ) {
         ;  *** the injection does not fit the window ***
         start = dwi + (s16)((u8@0x7FD290 + u8@0x5D396D) * 0x20)
         cap   = (u8@0x7FD28F * 0x20) + 0x2300
         0x80307E = min(start, cap)
    }
}
```

`u8 @ 0x5D396D = 0` and `u8 @ 0x5D396E = 0` in this dataset (B6), so the live
form is

> **trigger:** `wbho1s - dwi <= 0x7FD290 * 32` — the injection would end later
> than **50.25 degCA** in the internal angle reference;
> **reaction:** the start of injection is **advanced** to
> `dwi + 50.25 degCA`, capped at **360.0 degCA**.

### 9.4 What that means, and what it does not

> **Read §14 with this section.** Everything below is correct about *what the
> clamp does*, but §14 shows that in normal running (rail pressure above
> 13 bar) the clamp is **not armed at all** — the whole window machinery is a
> low-rail-pressure safety net.


1. **There is no exceedance flag and no DTC.** The clamp writes only
   `0x80307E`; nothing in the image tests for the branch having been taken,
   and no fault path is entered. **VERIFIED-STATIC**: `0x803088` (`dwi`) has
   exactly **one** reference in the whole image, the `sth` at 0x41BB7C
   (`python3 tools/sda_xref.py data/passat_azx_ori.bin --var 0x803088`), and
   the clamp sets no bit.
2. **The clamp never shortens `ti` and never removes fuel.** It moves the
   *start* earlier so the same quantity still ends at the margin. The cost is
   mixture preparation (injection starts before or against the intake event),
   not quantity.
3. **It saturates silently.** Once `dwi + 50.25 degCA` exceeds the 360.0 degCA
   cap, the injection simply runs past the margin with nothing reported.
4. **The only torque-domain consequence is armed by a fault**, not by the
   window (§10).

### 9.5 How much room is there, numerically

`dwi_max = wbho1s - 50.25 degCA`. With `KFWBHO1SW` at its high-speed,
high-load corner (160 counts -> 330 degCA):

```
dwi_max        = 279.75 degCA
at 6000 min^-1 : 279.75 / 36 degCA per ms  =  7.77 ms of ti
at 4000 min^-1 : 279.75 / 24               = 11.66 ms
```

Taking a stock WOT `ti` of about **4.5 ms at 6000 min^-1** (**ASSUMPTION** —
it has never been logged on this car), the hard clamp is reached at roughly
**+73 %** of the gasoline injection time. E85 at a stoichiometric +40 % lands
at 6.3 ms (margin 103 degCA); E100 at +63 % plus a 10 % WOT enrichment lands
at 8.1 ms and **does** hit the clamp. So:

* for E85 the **hard** window is not the binding constraint — it has roughly
  20 % reserve at the worst cell;
* what degrades first is mixture preparation, because the start of injection
  is being pushed towards and past intake-valve opening;
* for E100 plus enrichment the clamp does engage, silently.

`dwi` (0x803088) and `wbho1s` (0x80307E) are therefore the two variables to
log; the margin is `0x80307E - 0x803088 - 2144`.

---

## 10. The intervention path: an injection-window charge limit that only a fault arms

`awea_angles` also produces a **relative-charge limit** from the same two
window terms (0x454B28-0x454B70):

```
if ((0x80201E & 0x20) == 0)  0x803070 = 0xFFFF            ; no limit
else {
    n = (u8@0x7FD28F - u8@0x7FD290 + 0x118) * u8 @ 0x5D396F      ; 0x5D396F = 13
    0x803070 = min((n << 15) / k_nmot(0x803072), 0xFFFF)
}
```

`0x118 = 280` is `0x2300 / 0x20`, i.e. the base expressed in map counts, so
the bracket is the whole available window in 0.75 degCA counts; dividing by
`k_nmot` converts it to a time and `0x5D396F = 13` converts that into the
relative-charge domain. The result really is a charge, not a time: its
consumer arbitrates it against charge limits, and A3's measuring id **2051**
for the arbitrated value uses **format 0x21** — the same format as measuring
id 2, `rl` (`re/measuring_vars.csv`).

Chain (each step **VERIFIED-STATIC**, every reference list from
`tools/sda_xref.py --var`):

```
0x803070   (this function, the only writer besides the init at 0x13124C)
   |  min() with 0x80234C, 0x802358, 0x802360, 0x80235E
   v        at 0x0C7CF8-0x0C7D48 (a leaf reached through the process-pointer
            table at file 0x0B1F0C; 0x80234C comes from the charge-protection
            function 0x0FBE74)
0x80235A   *** VCDS measuring id 2051, format 0x21 ***
   |  min() with the driver request and every other charge limit
   v        at 0x445864 (0x4458DC reads 0x80235A)
0x80360E   final permitted relative charge  (0x803610 is the protection-only min)
   |
   v  read by the torque structure / throttle path at 0x42AA0C, 0x42AA60,
      0x435B48, 0x0E8B10 and by the measuring handlers 0x03A8E4 / 0x03A920
```

**`0x80201E` is the fuel-system fault byte** (written by the rail-pressure
diagnosis at 0x0F3148 / 0x0F3264 / 0x0F33B0). Its bit 5 is built at
0x0F3338-0x0F3350 as **`bit5 = bit2 | bit6`**, i.e. a summary "the rail
pressure value cannot be trusted". Bit 4 is the sensor fault that makes
`%HDRPIST` fall back to the substitute value 0x80201A (§6).

**So the window-derived charge limit is a limp-home limit: it is inactive
(0xFFFF) whenever the rail-pressure path is healthy.** With the shipped
`0x7FD28F = 200`, `0x7FD290 = 67` it evaluates to
`413 * 13 * 32768 / k_nmot`, which is 0xFFFF below about 1300 min^-1 and
falls to roughly 30 % of the 65024 ceiling at 6000 min^-1 — a genuine
limp-home characteristic.

---

## 11. Logging variables

| Variable | RAM | Unit | Why |
|---|---|---|---|
| `prist` rail pressure actual | 0x8031DA | 0.005 bar | VCDS id **500**; the controller input |
| `prsoll` rail pressure setpoint | 0x8031F4 | 0.005 bar | VCDS id **501**; what a flex-fuel raise moves |
| `prdiff` | 0x8031CA | 0.005 bar | VCDS id **1691**; "the pump cannot follow" shows up here |
| HDR sum output | 0x8031CC | — | VCDS id **516** |
| HDR volume contribution | 0x8031D2 | — | VCDS id **1687** (format 0x63) |
| arbitrated charge limit | 0x80235A | rl | VCDS id **2051**; carries the window limit when a fault arms it |
| unlimited setpoint | 0x8031F0 | 0.005 bar | map output before `KLPRMAX` and the rate limiter |
| rate-limited setpoint | 0x8031EE | 0.005 bar | shows when the pump volume limits the ramp |
| spare pump volume | 0x8031F6 | — | zero == pump at its limit |
| MSV volume after the limit | 0x80316E | — | pinned at 5000 == pump saturated |
| **`dwi`** injection duration as angle | **0x803088** | 3/128 degCA | the window quantity; only writer 0x41BB7C |
| **`wbho1s`** start of injection | **0x80307E** | 3/128 degCA | window margin = `0x80307E - 0x803088 - 2144` |
| required margin | 0x7FD290 | 0.75 degCA | 67 = 50.25 degCA today |
| latest start | 0x7FD28F | 0.75 degCA | 200 = 360.0 degCA today |
| `ti_sum` | 0x8030C4 | 1 us | VCDS group 002.3 (B6) |
| HDR enable bits | 0x7FD2F4 | bits | integrator enable / anti-windup |
| fuel fault byte | 0x80201E | bits | bit 4 sensor, bit 5 "pressure untrustworthy" |

`dwi` and `wbho1s` have **no** measuring id at all and need the DDLI logger or
a RAM read; no stock VCDS group carries them next to the pressures.

---

## 12. Recommended targets for the flex-fuel work

### 12.1 Raising the rail pressure

**Preferred: the setpoint maps, not the code.** `KFPRSOLHOM` at **0x5D5324**
is the map the engine runs on in every normal driving condition; it is a bare
8x8 u16 array with the shared axes at 0x5D5578 / 0x5D558A, and its top row is
19000 = 95 bar against a `KLPRMAX` ceiling of 22000 = 110 bar.

* **Headroom without touching anything else: +15 bar (19000 -> 22000).**
  That is +15.8 % pressure, i.e. `sqrt(1.158) = 7.6 %` more flow through the
  injector at the same `ti` (the `KLTIKRPR` curve is exactly `k/sqrt(dp)`,
  B6 §5.2) — useful, but far short of E85's +40 % fuel demand. **The rail
  raise is a mixture-preparation and duty-cycle measure, not a way to make
  the fuel mass; the fuel mass still has to come from `rk` (B6 §6).**
* To go above 110 bar, `KLPRMAX` (the six u16 at **0x5D5546**, all 22000) has
  to be raised too. **Do not** do that without evidence about the pump and
  the sensor: the sensor curve (§2c) saturates around 138 bar, and the pump
  volume limit (§3.4, §5.1) will simply rate-limit the setpoint instead of
  reaching it.
* An E-blend-dependent raise needs a *third* input to the map. The cheapest
  hook is **`KFPRSOLOFF` (0x5D5424)**, which is already summed into the
  homogeneous path (`CWPRSOL & 0x20` is set) and whose fade-out curve is the
  6-point table at 0x5D5552/0x5D5559. Re-purposing that additive path costs
  no code at all; re-purposing its temperature fade curve for E% costs one
  `lbz` redirect.

### 12.2 The real constraint is the pump, not the map

`0x8031F6` (spare pump volume) and `0x80316E` (MSV volume request, clamped at
`0x5D4BC6 = 5000`) are the two cells that decide whether a raised setpoint is
ever reached. E85 demands about 40 % more volume at the same pressure, and a
higher pressure demands more again. **Log `0x80316E` before and after any
E-blend run: if it sits at 5000, the pump is saturated and no map change will
help.**

### 12.3 The torque limiter

`docs/05_flexfuel_design.md` §3.6 assumes the ECU may "cut the throttle
unexpectedly" on a window exceedance. **It does not** (§9.4). If a torque
limit for the window is wanted, the clean insertion point is the min-chain at
**0x0C7CF8**, which already reduces `0x80235A` (measuring id 2051) from
`0x803070` and feeds the whole charge-limit arbitration — one extra `min()`
there propagates to the throttle exactly the way the stock protection limits
do, with no new path and no DTC.

### 12.4 Watch list

* `%HDR` anti-windup bit `0x7FD2D9 & 2` and the enable set `0x7FD2F4`: a
  setpoint the pump cannot reach parks the integrator and makes `prsoll`
  track `prist`, which looks like a working controller in a log while it is
  not.
* The rail-pressure DTC path (0x0F30xx-0x0F3Axx) sets `0x80201E`; its bit 5
  arms the limp-home charge limit of §10. A flex-fuel calibration that
  creates a persistent `prdiff` risks arming it.
* `0x5D3CDC = 2600` (**13.0 bar**) gates the window model through `0x7FEA48`;
  it is far below the operating range, so `0x7FEA48` is normally 1 and the
  angle clamp of §9.3 runs only through the `0x80201E & 0x20` term.
  **This needs one more reading before §9.3 is trusted in the field**
  (open item); it may mean the clamp is effectively inactive in normal
  operation, which would make the mixture-preparation argument of §9.5 the
  *only* window constraint.

---

## 13. Open items

| Question | Status |
|---|---|
| Absolute meaning of VAG display format 0x53 (would confirm 0.005 bar/LSB from outside the image) | open — one VCDS log of group 106 against a known rail pressure settles it |
| The period of the on-chip task at 0x45CAC4. B1 has it as 1000 ms (HYPOTHESIS); the entire rail-pressure controller, `%AWEA`'s angle maps and `rkti_pre` live in it, which a 1 Hz raster cannot support | **open, and it matters** — see §7 |
| `0x7FEA48` / `0x5D3CDC = 2600`: the sense of the 13 bar gate on the window model, and whether the angle clamp is live in normal operation | **SETTLED — see §14.** The gate is `prist > 13.0 bar`; in normal running the angle clamp, the driver cut-off and the charge limit are all disarmed |
| Percent scaling of the u8 `rl` (0x7FEF74) that indexes `KFWBHO1SW`; the axis tops out at 107 counts | open |
| Which FR name belongs to which `KFPRSOL*` variant (the mode bits of 0x7FB69A were not decoded) | HYPOTHESIS — the addresses and the selection logic are VERIFIED-STATIC, the names are guesses |
| The DTC number behind `0x80201E` bits 2/4/6 | open — the fault-path manager (0x4067FC family) was not followed |
| Stock WOT `ti` (needed to turn §9.5 into a hard margin) | open — one logged WOT pull with VCDS group 002 |

---

## 14. Correction to §9 and §10, same day: the window check is a low-pressure safety net

Found while chasing the `0x7FEA48` gate listed as an open item in §13. It does
not move any address; it changes **when** the window machinery is live, and it
adds the proof of the angle unit that §8 could only infer.

### 14.1 The angle unit is now proved outright

`esausg_output` (**`FUN_00409834`**, 0x409834-0x409C17) is the injection
output stage: it turns the per-cylinder angles `0x80309C[6]` / `0x8030A8[6]`
into TPU arguments and hands them to the driver through the function pointers
`0x7FCDC8` and `0x7FCDCC`. Three things fall out of it, all **VERIFIED-STATIC**:

1. Every angle difference is wrapped with
   `if (x < 0) x += 0x7800; else if (x > 0x77FF) x -= 0x7800;`
   — so **0x7800 = 30720 is one engine cycle = 720 degCA**, i.e.
   **1 LSB = 720/30720 = 3/128 = 0.0234375 degCA**, exactly as §8 derived.
2. The per-cylinder reference angles are the u16 array **`0x409CA0[6]`** =
   `3072, 8192, 13312, 18432, 23552, 28672` = **72, 192, 312, 432, 552,
   672 degCA** — six values spaced **exactly 120 degCA**, the firing interval
   of a six-cylinder engine. Nothing but 3/128 degCA per LSB produces that.
3. The conversion handed to the TPU is `* 0xF >> 6` = x 15/64, and
   `(3/128) x (64/15) = 0.1`, so the driver unit is **0.1 degCA** — the same
   TPU unit B7 found for the ignition output driver (which multiplies its
   0.75 degCA s8 by 15/2). The two internal units are a factor 32 apart, which
   is precisely the `* 0x20` that every `%AWEA` u8 map carries.

So §8's conclusion stands on evidence, not inference: **the angle LSB is
3/128 degCA and `ti` is 1 us per LSB.**

### 14.2 `0x7FD290` is a hardware cut-off angle, not just a comparison term

```
; --- esausg_output, per cylinder uVar2 ---
if (b_wbh_invalid(0x7FEA48) == 0) {
     cut = (u16 0x409CA0[cyl] - u8 0x7FD290 * 0x20) mod 0x7800
     0x7FB11C = (cut * 15) >> 6                       ; 0.1 degCA
     (*0x7FCDCC)(0x7FB11C, cyl)                       ; program the cut-off channel
}
...
(*0x7FCDC8)(b_wbh_invalid == 0, start_angle, ..., cyl)   ; first argument = enable
```

The injection therefore has a **hard end angle in the driver**, at
`cylinder reference - 67 * 0.75 degCA = reference - 50.25 degCA`
(cylinder 0: 72 - 50.25 = 21.75 degCA in the absolute frame). When it is
armed, an over-long injection is **cut**, and fuel is lost.

### 14.3 …and it is armed only below 13 bar of rail pressure

```
00454884  ...
0045488C  lhz   r31,0x31EA(r13)      ; prist_w
00454894  lhz   r10,0x3CDC(r10)      ; PRWBHMX @ 0x5D3CDC = 2600 = 13.0 bar
0045489C  cmpw  r10,r31
004548A0  bge   0x004548B0           ; prist <= 13 bar -> compute the margin
004548A4  li    r12,1
004548A8  stb   r12,-0x15A8(r13)     ; 0x7FEA48 = 1 and SKIP
```

`0x7FEA48 = (prist > 13.0 bar)`, so in any normally running engine
(`KFPRSOLHOM` never goes below 35 bar) it is **1**. Consequences:

* `esausg_output` does **not** program the cut-off channel and passes
  `enable = 0` (§14.2);
* `awea_ti_to_angle`'s test is
  `(0x7FEA48 == 0 && 0x7FE91F != 0) || (0x80201E & 0x20)`
  (0x41BB28-0x41BB48), so with `0x7FEA48 = 1` the **angle clamp of §9.3 runs
  only when the fuel-system fault bit is set**;
* `0x803070`, the charge limit of §10, is `0xFFFF` unless that same fault bit
  is set.

**All three interventions share one arming condition class: something is
wrong with the rail pressure.** That is physically coherent — injector flow
goes as `sqrt(dp)`, so at low rail pressure `ti` explodes and an injection
really can overrun the window; at normal pressure the `KFWBHO1SW` calibration
guarantees the fit and the runtime net is switched off.

### 14.4 What this changes for the flex-fuel work

* **On E85 at normal rail pressure nothing in the ECU enforces the injection
  window.** The +40 % `ti` will simply be injected, with the start angle
  walking earlier as `KFWBHO1SW` dictates. There is no clamp, no flag, no
  charge limit and no DTC to trip — and no protection either. The margin
  computed in §9.5 is a *calibration* margin we have to respect ourselves.
* **The dangerous case is a pump that cannot follow.** If a raised setpoint
  plus E85's higher volume demand ever lets `prist` fall below **13.0 bar**
  (`PRWBHMX`, 0x5D3CDC) — a stall, a hot restart, a saturated pump at WOT —
  then *all* of it arms at once: the driver cut-off at 50.25 degCA, the angle
  clamp, and (with the fault bit) a charge limit that falls to about 30 % at
  6000 min^-1. The failure mode is a sudden, simultaneous fuel cut and torque
  drop, not a gentle limit.
* **So the acceptance signal is `prist` staying above 13 bar, and the
  quantity to watch is `0x80316E` pinned at `VMSVMX` = 5000** (§12.2) — the
  early warning that the pump is out of volume.
* The §12.3 recommendation is unchanged and now better motivated: if we want a
  torque limit that follows the *window* rather than a *fault*, we have to add
  it, and 0x0C7CF8 is still the clean place.

---

## 15. Verification: the Python model — VERIFIED-DYNAMIC

`emu/models/window.py` is a bit-exact model of the angle-synchronous half of
`%AWEA`, reading every calibration value out of the image rather than
hard-coding it (so it follows a re-calibrated binary):

```bash
./.venv/bin/python -m emu.models.window            # the window terms and a sweep
./.venv/bin/python -m emu.models.window --verify   # compare against the ECU code
./.venv/bin/python -m unittest tests.test_window_model -v
```

`--verify` runs the real `awea_ti_to_angle` (0x41B9C0) and the `k_nmot` block
(0x41B9A4) in the A5 Unicorn harness over 7 engine speeds x 11 start angles x
13 injection times, plus the `k_nmot` edge cases, and compares `dwi`
(0x803088) and the resulting start-of-injection angle (0x80307E) bit for bit:

```
all 1011 cases match
```

The dynamic-correction block that precedes the check is made deterministic by
setting `0x80306A = 0`: `|0| < u16 @ 0x5C713E` (= 427) makes `0x7FEA46` true,
which clears the latch `0x7FEA45`, so the start angle passes through unchanged
and only the window clamp is exercised.

The sweep it prints is the §9.5 table computed rather than estimated —
`max_ti`, the injection time at which the clamp engages:

```
  rpm   wbho1s     max ti
 2000    330.0    23314 us
 4000    330.0    11657 us
 6000    330.0     7770 us      <- the high-speed, high-load corner of KFWBHO1SW
 6000    270.0     6104 us
 6000    210.0     4437 us      <- KFWBHO1SW's minimum, but those cells are at
                                   low speed and low load, where ti is small
```

`tests/test_window_model.py` additionally asserts, straight from the dump,
that both window curves are flat (67 and 200 counts), that the six
per-cylinder references 0x409CA0 are spaced exactly 120 degCA (§14.1), that
`dwi = (ti * k_nmot) >> 13` reproduces `angle = t * rpm * 6e-6` to better than
0.1 % when `ti` is 1 us, that the clamp leaves the start angle untouched below
the trigger, and that it never writes past the 360.0 degCA cap.
