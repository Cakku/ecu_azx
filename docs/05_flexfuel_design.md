# Flex-fuel design

Status (2026-09-24): **designed, reverse-engineered and implemented; bench
and car evidence pending.** Every ECU-side insertion point below is resolved
to an address (VERIFIED-STATIC, with the emulator runs the sections cite) and
implemented in `patches/ff_fuel`; what is still missing is the hardware half
of every issue — the RAM snapshots of #23, the flashes of #26/#27/#32 and the
calibration on the car (#33-#36, #40). The order of the bench day is
[`08_bench_playbook.md`](08_bench_playbook.md); the procedures are
`patches/ff_fuel/test/`. Nothing on a bench or a car has confirmed any of
this yet.

## 1. Architecture

```
GM/Continental flex-fuel sensor ──50-150 Hz / 1-5 ms──▶ Pico 2 (capture, plausibility)
        │                                                  │ CAN 500 kbit/s, 10 Hz
   in the fuel FEED line                                   ▼
                                              powertrain CAN (behind the J533 gateway)
                                                           │
                                              MED9.1.1: RX slot 15 ─▶ ff_rx (validate, filter)
                                                           │
                          ┌──────────────┬─────────────────┼──────────────┬──────────────┐
                          ▼              ▼                 ▼              ▼              ▼
                    fuel factor    ignition offset   start enrichment  rail-pressure   diagnostics
                    (rk × F(E))   (+dzw_E, added)   (ksta × f_st,     adder (+prail,  (blocks 111/108/
                                                     zwstt + dzw_st)   inside ceiling)  69/109, PID 0x52)
```

MED9.1 has no native flex-fuel function (unlike Simos18 or MED17.1.1), so
all of this is custom code, in one patch (`patches/ff_fuel`) with one state
block and one calibration block. Of the three insertion patterns proven on
other Bosch ECUs, only the first applies to this software: **scale the fuel
mass** (woj's ME7.9.10 `rkte` hook; here `rk` at RAM 0x803038, §3.3).
Blending double maps the way the ECU blends `KFZW/KFZW2` (prj) has no vehicle
here — this dataset has a single `KFZW`, so the ignition term is **added**
(§3.4) — and the `vkKraQu` fuel-quality variant byte the 1K8907115F community
patches drive **does not exist in this software** (`re/findings/variants.md`).

Every feature added after the fuel MVP ships **disabled** — an
`ff_<feature>_enable` byte at 0 *and* a neutral table under it — and its E0
bit-identity is proven in the emulator (§6), so the flashable file behaves
like the fuel-only MVP until a human turns one byte on
(`01_project_plan.md` §7, decision of 2026-09-17).

## 2. Sensor and Pico

Sensor: GM/Continental 13577379 (3/8" barbs) or 13577394; open-collector
square wave, needs a 2.2-3.5 kOhm pull-up to 5 V (1 k/10 k do not work),
12 V supply, sensor ground. Frequency 50 Hz = 0 % .. 150 Hz = 100 % ethanol
(linear); low-pulse width 1 ms = -40 C .. 5 ms = 125 C fuel temperature
(`T = 41.5 * ms - 81.25`). Fault codes: 180-190 Hz contaminated fuel or
internal fault, below 50 Hz or no signal = fault. Pump "E85" typically reads
~75-81 % (which is why the fuel factor at a sensor reading of 78 % is the
familiar 1.50, §3.3). Mount in the **feed** line; return-line mounting traps
air and produces spurious readings (multiple reports).

Pico firmware requirements (issue #29; replaces the pot demo in
`pico_can_sender/`, whose toolchain is `03_tooling.md` §6.1):

- Edge-timestamp capture on the sensor input (both edges), 4-sample ring
  average of period and low time, 250 ms update, debounce 0.25 ms.
- Plausibility: frequency window 45-155 Hz valid; 155-200 Hz "contaminated";
  outside or no edges for 500 ms "fault". Rate limit of the reported E% is
  not done on the Pico (the ECU does it) but the raw and the averaged value
  are both sent.
- Frame (Zeitronix ECA-2 CAN compatible), default id **0x0EC**, DLC 8, 10 Hz:

| Byte | Content |
|---|---|
| 0 | ethanol % (0-100), averaged |
| 1 | fuel temperature C + 40 |
| 2 | raw frequency / 2 (Hz) |
| 3 | rolling counter (0-255) |
| 4-5 | reserved 0 |
| 6 | firmware version |
| 7 | status: 0 OK, 1 sensor fault, 2 contaminated fuel, 3 not ready |

Bytes 0, 1 and 7 match the Zeitronix layout so an ECA-2 CAN can be dropped
in; bytes 2-6 are ours. The id must be confirmed free on **our** powertrain
CAN with `pi_can_setup/find_active_ids.py` (#30; 0x0EC is not in the ECU's RX
or TX tables; the ICAN DBC in `data/` is a different bus). The frame is
injected on the powertrain CAN directly (the gateway does not forward
unknown ids from the OBD port). `data/ethanol_node.dbc` describes it;
`logging/ethanol_frame_send.py` is the simulated node, with `--fault-after`,
`--stall`, `--implausible`, `--not-ready` and `--e-ramp` for the fault matrix.

Bench: a signal generator (or a second Pico) drives the sensor input 40-200 Hz
to test every status path before the real sensor is wired.

## 3. ECU side

### 3.1 Reception

VERIFIED-STATIC (brief B2, #12; `re/findings/can.md`), implemented by D1.

Each `tbl_can_rx` slot (0x2BC90, 21 entries) owns one TouCAN message buffer
filtered on an exact 11-bit id (RXGMSK = 0xFFE00000 on all three modules).
`can_init_mb(slot)` at **0x135750** arms the buffer once at start-up; a
periodic task calls `can_rx_poll(slot)` at **0x4379C8**, which copies the 8
data bytes into `can_rx_shadow` and clears the IFLAG bit. There is no
interrupt and no "new data" flag in the driver: **the return value of
`can_rx_poll` is the new-data flag** — the received DLC (8 for our frame)
when a frame arrived since the previous call, 0x40 when nothing arrived. So
no dispatcher hook is needed; the patch takes one of the four unused slots
(id 0x7FF): **slot 15, TouCAN C, message buffer 6.**

What the patch does:

1. The id word at **0x2BD8C** is changed from `00 00 07 FF` to `00 00 00 EC`
   (a `data` entry with `"calibration_edit": true` in `patch.json`; the block
   with descriptor 0x0A0010, 0x020000-0x02FFFF, is re-checksummed). The rest
   of the row stays: the DLC byte is never read, and word 0x2BD88 must stay
   0x00000004 so the payload is not byte-swapped.
2. **No mask register changes.** RXGMSK/RX14MSK/RX15MSK stay as they are;
   0x0EC matches no other buffer on either module.
3. `can_init_mb(15)` is called from the patch's own state-block
   initialisation, so `can_rx_arm_all` (0x12E570) is not edited. Without it
   the buffer stays CODE = NOT ACTIVE and receives nothing.
4. The 10 ms raster hook (§8) calls `can_rx_poll(15)` and treats a return
   value of 8 as "fresh frame".
5. **The 8 data bytes appear at RAM 0x803F9C..0x803FA3** (`can_rx_buf_spare0`),
   in wire order; the id is echoed at 0x803F98, and the patch compares that
   echo against `ff_can_id` before believing a frame. The other spares: slot
   16 → 0x803FA8 (id word 0x2BD9C), slot 19 → 0x803FCC (0x2BDCC), slot 20 →
   0x803FD8 (0x2BDDC).
6. Timeout handling is the patch's (§3.2): the stock supervision (0x429D44 /
   0x42A0A0) is per-slot calibrated and has no entry for a new slot.
7. No interrupt and no transmit collision: module C's IMASK only covers
   buffers 13-15, and every transmit object lives on module B buffers
   0x0B-0x0D.

**One open bench check (#22, `logging/sessions/can_bc_check.json`).** All four
spare slots are on TouCAN module **C** (0x707800), while the ECU transmits
only on module **B** (0x707400). Both run at 500 kbit/s with identical timing
and C is receive-only, so the working assumption (HYPOTHESIS) is that B and C
hang on the same Antrieb-CAN pair. Confirm before wiring the Pico node: with
the engine running, signals fed by module-C frames (id 0x050 slot 10, id
0x0C2 slot 9) *and* by module-B frames (id 0x1A0 slot 1) must all be live. If
C is a separate bus, the node must be wired to that bus — module B has no
free message buffer.

### 3.2 Validation, filtering, state

Implemented in `patches/ff_fuel/src/ff_fuel.c`, specified by
`emu/models/flexfuel.py` and compared with it tick by tick in
`tests/test_ff_fuel_patch.py` (brief D1, #32/#37). The producer runs once per
activation of the **10 ms** raster (§8; `ff_tick_ms` = 10 is itself a
calibration value, so moving the hook to another raster is one byte, not a
rebuild, and every time constant in FFCAL001 is physical).

```
inputs:  E_raw, T_fuel, status, counter (frame bytes 0, 1, 7, 3), id echo, age (activations since last good frame)
state:   E_filt (0..100 %, 1/16 % + a 1/1024-count sub-count), mode, hold_ticks, e_key

a frame is BAD if:  id echo != ff_can_id, or E_raw > 100, or status in {1 fault, 3 not ready},
                    or the counter has not changed for ff_stall_max (3) received frames
                    -> latched in frame_bad until the next good frame
if age > ff_timeout_ms (1000) or frame_bad:   mode = FAULT
elif status == 2 (contaminated):             mode = HOLD      # keep E_filt, do not follow E_raw
else:                                        mode = OK
     E_filt += (E_raw - E_filt) * ff_tick_ms / ff_filter_tau_ms   # 10 / 3000 = 1/300 per activation, tau 3 s
     |dE/dt| <= ff_slew_pct_s (2 %/s)                              # tank blending cannot be faster
F = ff_F_curve(E_filt), clamped to [1024, 2048] in code             # 1/1024
```

| Quantity | Format | Note |
|---|---|---|
| `E_filt` | u16, **1/16 %**, 0..1600 | one curve breakpoint every 6.25 % = 100 counts, so `index = E_filt / 100`, `frac = E_filt % 100`, integer only |
| `E_frac` | u16, **1/1024 of one `E_filt` count** | the sub-count: at 10 ms the 2 %/s slew limit is 0.02 % = 0.32 counts of 1/16 % per activation, which integer arithmetic would truncate to zero and freeze the filter. `E_filt`+`E_frac` is one 26-bit value in 1/16384 %; `E_filt` alone is what the curve, the logger and everything else read |
| `F` | u16, **1/1024**, clamped to [1024, 2048] | `rk = min((rk * F) >> 10, 0xFFFF)` (§3.3) |
| time | activations of the periodic hook | `ff_tick_ms` converts every calibration value; the conversion is in the C, not in `ffcal001.py` |

**Why `frame_bad` is a latch.** The bad-frame conditions have to hold between
frames too: the Pico sends at 10 Hz while the raster runs at 100 Hz, so nine
activations out of ten see no frame at all, and evaluating the conditions only
on the activation that carries the bad frame let the mode fall straight back
to OK on the next one. (Found by the tick-by-tick comparison with the model,
not by review.)

**Modes** (`ff_state.mode`, published in measuring block 111 field 4):
0 INIT, 1 OK, 2 HOLD, 3 FAULT, 4 OFF, 5 OVERRIDE. `ff_mode` in FFCAL001 selects
0 off (F = 1.000, no CAN traffic at all), 1 normal (shipped), 2 bench override
(`ff_e_override` % is used as E). A fourth safe state needs no calibration: an
FFCAL001 whose magic, version, length or checksum does not check out forces
mode OFF.

**Fail-safe rule (asymmetric, as commercial systems do it).** On FAULT the
**fuel** factor is held for `ff_hold_s` = 60 s and then ramped, at the slew
limit, toward `e_key` — the E% restored from EEPROM at power-up (§3.8), E0 if
none — so a lean-out is impossible during a transient dropout; but the
**ignition**, **start-advance** and **rail** terms drop to zero on the
activation the mode leaves OK/HOLD/OVERRIDE, because advance and pressure are
the dangerous direction (§3.4-§3.6, issue #37). The hold timer counts
activations including the one it is armed on, so `ff_hold_ticks` reads 5999
immediately after the FAULT entry and the held window is exactly 6000
activations = 60.00 s. HOLD keeps computing from the frozen `E_filt`, so the
other features freeze rather than drop.

**Power-up is FAULT at E0 with F = 1024** until the EEPROM restore seeds
`E_filt` and `e_key` (§3.8) or the first good frame arrives.

### 3.3 Fuel factor

Stoichiometry: gasoline 14.7, ethanol 9.0 by mass. For volume fraction `v`
(the sensor reports volume %), with densities 0.745 (gasoline) and 0.789
(ethanol) kg/L, the mass fraction is `w = 0.789 v / (0.789 v + 0.745 (1-v))`,
the blend stoich AFR is `1 / (w/9.0 + (1-w)/14.7)`, and the required fuel
**mass** factor is `F_m = 14.7 / AFR_blend`: **1.5429 at v = 0.85, 1.6333 at
E100** (14.7/9.0), and 1.500 at **v = 0.78** — the "~75-81 %" that pumps sell
as E85 (§2). The curve is indexed by the **sensor's** reading, the true volume
fraction, so its point at 85 % carries 1.5429, and a tank of pump "E85" lands
the sensor near 78 % and the factor near 1.50. Both are asserted in
`tests/test_flexfuel_model.py::TestFuelMassFormula`. Field values quoted by
tuners (×1.35-1.50 at E85) span this range, which is why the factor is a
**calibratable 1D curve over E%** (`ff_F_curve`, 17 points every 6.25 %,
1/1024 resolution), initialised from the formula and trimmed with the
wideband.

Shipped curve (Q10): 1024, 1067, 1109, 1151, 1193, 1235, 1276, 1317, 1358,
1398, 1438, 1478, 1517, 1557, 1595, 1634, 1673. `patches/ff_fuel/ffcal001.py`
builds it through `emu.models.flexfuel.fuel_mass_factor`, so the calibration,
the patch and the model cannot drift apart, and it refuses a block whose first
point is not 1024, whose curve is not monotonic, or which exceeds the 2048
ceiling the code clamps to.

**The insertion point** (brief B6, #14; `re/findings/injection.md`; model
`emu/models/injection.py`, `tests/test_injection_model.py`):

* **The injector constant is mass-based, not volumetric.** `KRKATE` (u16
  scalar, **0x5D3DBC = 3858**) is multiplied by `KLTIKRPR(dp)` (0x5C72F8),
  whose twelve points obey `value × sqrt(dp) = const` to 1 % — the Bernoulli
  orifice law, i.e. time per unit **mass** at a given pressure. So the factor
  above is applied unchanged; there is no density ratio to divide by.
* **`rk` at RAM 0x803038** has exactly one writer (`sth r6,0x3048(r13)` at
  0x41ADD4, in `gk_rk`) and seven readers, all inside `rksplit` (0x41C3A0).
  Everything the conversion sees — homogeneous, both split modes and the
  start injection — is derived from that one cell.
* **Hook site: 0x42247C**, the `bl 0x41C3A0` (`4B FF 9F 25`) inside the
  engine-synchronous task `task_segment_a` (0x4223B0, ERCOSEK TCB 5, id 40,
  prio 0x0A). Its neighbours are argument-less `bl`s, so r3-r12 are dead; the
  stub scales 0x803038 and ends with `ba 0x41C3A0`. One word.
* **Fixed point:** `rk` is u16 with no implicit fraction, so
  `rk = min((rk × F_q10) >> 10, 0xFFFF)`. At F = 1024 the stub takes an early
  return and **does not write `rk` at all** — 20 instructions per injection
  segment — which is what makes E0 bit-identical (asserted by the tests).
* **Bench alternative with no code at all (#45):** `KRKATE` has exactly one
  reference in the whole image, so multiplying the scalar at 0x5D3DBC fuels a
  fixed blend for a first drive (E85: 3858 → 5787), then `checksum.py fix`.
* **The `%UFRKTI` worry does not bind here.** Nothing outside the injection
  chain reads `rk` or `ti`; the monitoring-shaped function `FUN_00455C60`
  (%TEB purge) reads the **pre-`ZGST`** bank values
  0x803030/0x803032/0x803034/0x80303A, upstream of the hook. The corollary is
  that after the patch the level-2 path no longer monitors the fuel that is
  actually injected.
* **The limit to watch is the injection window, not a `ti` maximum.** `rk2ti`
  clamps only from below (`TIMINP` = 900 at 0x5C7328). The ceiling is applied
  in the angle domain by `awea_ti_to_angle` (0x41B9C0) on
  `dwi = (ti × k_nmot) >> 13` at RAM **0x803088** — the signal §3.6 logs.

Do not use the lambda adaptation (fra/frau/frao) as the correction path: the
35-50 % change exceeds its limits and sets DTCs, it cannot act in open-loop
phases (cranking, warm-up before sensor light-off, WOT enrichment) which is
where E85 goes lean, and it cannot inform ignition.

#### How `F(E)` composes with the stock lambda request (brief H1, #46; the desk half of #32 / #33)

Evidence: `re/findings/calibration_names.md` §12 (VERIFIED-STATIC unless a
line says otherwise). This settles a question G4 raised: the stock ECU **does**
have a λ < 1 request path on the fuel side, and it is calibrated.

**1. What the stock ECU asks for.** From start end on, `gk_rk` divides each
bank's fuel mass by the lambda setpoint `lamsbg_w` 0x80304A / `lamsbg2_w`
0x803048, the output of FR `%LAMKO` (`eta_coordinator` 0x442C14), whenever
0x7FEA33 is set. In warm part-load running it is 1.000. It drops below 1.000
for:

| requester | condition | minimum on this dataset | fuel ×(1/λ) |
|---|---|---|---|
| full load `lamfa_w` (`cand_LAMFA` 0x5D34D8) | charge request `rlsol_req` > 100 % (105 % from 3760 rpm, 110 % everywhere) | 0.891 at ≥ 110 %, 6520 rpm | 1.122 |
| component protection `lambts_w` (`cand_KFLBTS` 0x5C6636 / 0x5C66F6) | `%ATM` model ≥ 875 °C, high speed and load | 0.820 / 0.836 at 7000 rpm | 1.220 |
| predicted protection (0x5C68A2) | strong ignition retard with hot exhaust | 0.730 | 1.370 |
| after-start `lamnswl_w` (`KFLANS` 0x5C6F36) | start below about +10 °C `tmst` | 0.801 at −30 °C | 1.248 |
| catalyst clear-out, diagnoses | brief, below 4000 rpm / OBD tests | 0.950 … 0.750 | ≤ 1.33 |
| `%LAMKO` rich limit (0x5C54E0) | bounds all of them | **0.700** | **1.429** |

**2. The composition is multiplicative, and `F(E)` is downstream of the
division.** The `rk` hook at 0x42247C scales RAM 0x803038, which `gk_rk`
writes after the division, `fr`, `fra`, `frm`, the purge subtraction and
`ZGST`. So the injected fuel is

    rk_injected ∝ base × (1 / λ_stock) × fr × frm × F(E)

and nothing in between clamps the product except `rk` ≤ 0xFFFF; the ceiling
that matters is the injection window (§3.6). The lambda controller's own
setpoint 0x802CDE is formed by `lam_ist_from_rk` from the mass *before* the
division and its own division by `lamsbg_w` (`injection.md` §9), i.e.
**upstream of `F(E)`**: the loop targets λ_stock and sees only the error of
`F(E)`, which is what this section always wanted.

**3. It is also the physically right composition.** λ is defined against the
stoichiometric AFR of the fuel being burnt. The stock `rk` at λ_stock is the
*gasoline* mass for that λ; multiplying by `F(E)` = AFR_gasoline / AFR_blend
turns it into the *blend* mass for the same λ. So with a correct `F(E)` the
engine runs λ_stock on any blend: no double counting, no missing term, and the
stock enrichment keeps its meaning (full-load power mixture, component
cooling) on E85. **`F(E)` therefore multiplies `rk` unconditionally**; skipping
it while `lamsbg_w` < 1 would run λ_stock × F(E) — at E85 full load
0.891 × 1.543 = **1.37**, lean, exactly where the stock software asked for
cooling fuel.

**4. What it means for the injection-window margin (#33).** The fuel the
window has to carry, relative to a stoichiometric gasoline injection at the
same charge, is `F(E) / λ_stock`:

| blend (shipped `ff_F_curve`) | λ 1.000 | 0.891 (full load) | 0.820 (KFLBTS) | 0.730 (predicted) | 0.700 (floor) |
|---|---|---|---|---|---|
| E0, F = 1.000 | 1.00 | 1.12 | 1.22 | 1.37 | 1.43 |
| E50, F = 1.326 | 1.33 | 1.49 | 1.62 | 1.82 | 1.89 |
| E85, F = 1.543 | 1.54 | **1.73** | **1.88** | **2.11** | **2.20** |
| E100, F = 1.633 | 1.63 | 1.83 | 1.99 | 2.24 | 2.33 |

§3.6 puts the hard clamp at about **7.8 ms of `ti` at 6000 rpm, +73 % over an
assumed 4.5 ms stock WOT injection**. If that 4.5 ms is a *logged* WOT value
it already contains the stock full-load term, and E85 needs ×1.54 of it
(6.9 ms, about 12 % reserve). If it is a λ = 1 figure, E85 at full load needs
×1.73 — **at the clamp** — and component protection (×1.88) or the predicted
protection (×2.11) is **beyond** it. Either way the stock enrichment is the
term the #33 rule "not above E50 until `ti` and rail margins are confirmed"
did not contain. It gains three conditions:

* **judge the margin at the lowest `lamsbg_w` a pull produces**, not at
  λ = 1: log ids **43 / 44** (`lamsbg_w`, `lamsbg2_w`), **376** (`lamfa_w`),
  **377 / 1555** (`lambts_w`) and the status byte **410** (which requester is
  active) — `logging/sessions/tuning_checklist.json` carries the
  lambda-request group — with
  `ff_fuel`'s `win_margin_min` (group 109) on every WOT pull;
* **at E50, make one pull long and hot enough to reach component protection**
  (id 410 bit 6, id 377 below 1.000), or show from the model temperatures
  (DDLI, `tuning_checklist.json`) that this car never reaches 875 °C — before
  any step above E50;
* **treat the 0.730 / 0.700 cases as transients of a strong ignition retard**:
  on E85 the knock retard that triggers them should be rarer, but the window
  must still not saturate silently there (`awea_ti_to_angle` sets no flag,
  §3.6) — so a pull that ends in heavy retard is a stop, not a data point.

**5. Two decisions this leaves to the human**, each a separate design with its
own argument:

* whether to **reduce the stock enrichment itself on ethanol** — scale
  (1/λ_stock − 1) towards 0 with E — on the grounds that ethanol's charge
  cooling and lower exhaust temperature need less protection fuel. That would
  be a new `ff_<feature>` with an enable byte defaulting to 0, neutral tables,
  and its own bench evidence (a thermocouple or at least the model against
  the car), because the `%ATM` model is calibrated for gasoline: it keys on
  `lamsbg_w` and the ignition efficiency, not on the fuel, so on E85 it
  predicts the same temperatures and component protection fires at the same
  modelled 875 °C whatever the real exhaust does. Conservative (extra fuel,
  less window), never dangerous;
* whether the window margin at E85 × 0.820 (or × 0.730) is **acceptable or
  needs a limit** — the torque-limit hook of §3.6 at 0x0C7CF8 is the place.

Two questions of #46 stay open: what raises `rlsol_req` above 100 %, and the
non-homogeneous fuelling modes.

#### The tester adaptation channels trim the fuel under the flex factor (F4, G2, G7, H2)

VERIFIED-STATIC: `re/findings/calibration_names.md` §10.1, `re/findings/eeprom.md`
§5 and §11, `re/symbols.csv` rows `kwp_adaptation_service` 0x038708,
`adaptation_restore_all` 0x12E3F8, `adap_ch10_fgru` 0x7FD066.

The KWP "Anpassung" service `kwp_adaptation_service` (0x038708) defines
**twelve tester channels**. Each is clamped between two calibration bytes,
committed to **EEP_CONF block 8** (sub-function 0x83) and restored from it at
every power-up by `adaptation_restore_all` (0x12E3F8; channel *k* is block 8
payload +(k+1), the 17 slots cover +2..+18, §3.8). Three of them multiply the
fuel **upstream of the `rk` hook**, so `F(E)` multiplies whatever they hold:

| channel | RAM | multiplies | limits → factor | default |
|---|---|---|---|---|
| **4** | 0x7FD065 | the running mixture, in `mixture_running_build` 0x419DA4 | 64…141 → **0.50 … 1.10** | 128 = 1.0 |
| **8** | 0x7FD067 | `ksta`, the start quantity (0x41A5EC, 0x41A780) | 64…141 → **0.50 … 1.10** | 128 = 1.0 |
| **10** | 0x7FD066 | `fgru_trim`, a factor on `rk` in `gk_rk` | 26…179 → **0.797 … 1.094** | 128 = 1.0 |

(Channel 5, 0x7FD069, 64…141, also feeds the 0x7FD264 mixture factor per the
same table; it is not counted as a fuel trim here.)

**Who can move them on this dataset.** Not a workshop tester: the service's
three access words at 0x5CF004/08/0C are all 0x40, which admits **channel 7
only** — channel 1 is refused with NRC 0x33 and a channel-0 "reset all"
changes **no** channel (`xxd -s 0x1CF004`; emulated, G7). The routine that
does rewrite every block-8 channel byte to its default and commits is the
stock **reset-all 0x038D64**, called from 0x0D10D0 after a fault clear when
block 11 payload +11 bit 0 (mirror 0x7FA02B) is set — and that bit is set
**only by routine 0xC5** (immobiliser / component-protection adaptation,
handler 0x087494 in the programming stack), with 0x7FEB59 set at boot on the
programming magic 0x7F8020 == 0xAABFFB11 or an identity mismatch (H2, #47,
emulated end to end). So: a bare OBD download does not reset the fuel trims;
a `31 C5` / `33 C5` session does, on the next boot; a plain DTC clear never
does.

For the flex strategy:

* **Calibrate with all three at 128**, and read them
  (`logging/sessions/adaptation_channels.json`, or sub-function 0x81) before
  trimming `ff_F_curve` on the wideband. Record the values with the
  calibration.
* **After any programming session, read them again** before blaming the
  sensor or the curve for a mixture shift. A shift that is the same at every
  blend points here, not at `F(E)`.
* They are *not* a flex-fuel lever. The +10 % upward range is far short of
  E85's +54 %, and `F(E)` stays the only fuel correction that follows the
  blend.

### 3.4 Ignition

An ethanol advance `dzw_e` is **added** to the base ignition angle after the
base map and its deltas and before the bank offsets, the knock retard and the
output clamps, so knock control and every limit still act on top:

    dzw_e = clamp(round(f_zw(E) × dzw_E(nmot, rl) / 256), ± min(ff_dzw_max, 16))

with `f_zw(E)` a 1D curve (0 at E0, saturating at E50 where MBT is usually
reached, per prj) and `dzw_E` an 8 × 8 additive map in FFCAL001 that ships
**all zero**. `KFZWOP` (the torque model's optimum) and the knock-control
references are **not** shifted. Knock retard (`dwkrz`) staying at zero across
blends is the acceptance signal.

**The chain** (brief B7, #15; `re/findings/ignition.md`):

* **There is no `KFZW/KFZW2` blend in our software.** The FR's double-map
  interpolation by the inlet-cam factor (`SY_NWS`) and the `KFZWLB1/2` swirl
  pair are compiled out; the dataset has exactly one base map, **`KFZW` at
  0x5C75FE** (16 nmot rows × 12 rl cols, s8), read by `zwgru_kfzw_lookup`
  (0x41D334) through the axis blocks 0x5C7736 (nmot) and 0x5C7758 (rl).
* **Insertion point: the word at 0x41D40C** inside `zwgru_build` (0x41D38C),
  `add r3,r3,r10`, replaced by a `bl` to a stub that re-does the add and adds
  `dzw_e`. It sits after the base map and all base deltas and before the bank
  offsets, the **knock retard** (`zwbas_per_bank`, 0x41D10C) and `zwmin` / the
  −54..+58.5 ° output clamp (`zwout_select`, 0x41D464). Context: **ERCOSEK task
  id 41** (TCB entry 6 at 0x47870C, entry point 0x4224BC), once per ignition
  event. Only `r3` and `r10` are live at that word; LR is already saved on the
  caller's stack. Ten instructions.
* **Format**: the whole chain is **s8 at 0.75 °CA per LSB** — proven by the
  output driver `zw_output_driver` (0x41CD9C), which multiplies by 15/2 to
  hand the TPU stage 0.1 ° units. So +1 ° = +1.333 counts; the offset is
  calibrated in 0.75 ° steps.
* **`KFZWOP` is at 0x5CA3F1** (16 nmot × 11 rl, s8, axes 0x5CA3D6 / 0x5CA3E6),
  read by `kfzwop_lookup` (0x436B90) and summed into `zwopt` (0x802665) by
  `zwopt_build` (0x423344). `KFZWOP − KFZW` is the ECU's own estimate of the
  advance the base map gives up to knock: 1.5 ° at 1800 rpm / 35 % rising to
  ~9 ° at 4500 rpm / 90 % — a physical upper bound for `dzw_E`.
* **A stock term shares that budget** (F4, `calibration_names.md` §10.5;
  `ignition.md` §14): `zwdelta_load` (RAM **0x7FD338**, s8, 0.75 °CA/LSB,
  written by `FUN_00459334`) is added in `zwbas_per_bank` at 0x41D120 — after
  `zwgru` (and so after `dzw_e`) and before the knock retard and the
  ZWMIN/ZWOUT clamp. It is `zwdelta_7FD338_weight_map` 0x5D5F81 (0 below 47 %
  charge) × `zwdelta_7FD338_map` 0x5D5FFB (−6.0 … +2.25 °CA over nmot and
  `tans`) + `zwdelta_7FD338_add_map` 0x5D6075 (−3.75 … +7.5 °CA over
  `tmot_filt` and rl, **largest cold at load**). The `KFZWOP − KFZW` headroom
  **does not include this term**: on a cold engine at load part of it is
  already used before the ethanol offset is added. Calibrate `ff_dzw_map`
  warm, read the headroom minus the live 0x7FD338 (DDLI,
  `logging/sessions/tuning_checklist.json`), and keep the acceptance signals
  through the warm-up too.
* **`dwkrz`** (the acceptance signal) is the six-byte array at
  **0x7FCE57-0x7FCE5C**, written by `krreg_knock_retard` (0x416D6C) and shown
  in VCDS groups 020-024. There is also a stock "bad fuel" detector
  (`zwgru_low_octane_detect`, 0x0F436C) that latches on the *mean* retard and
  applies `KFDZK` (0x5D597E); with E85 it must never latch, so its latch bits
  0x7FD31B bits 0/1 are the second acceptance signal.

**The implementation** (brief E1, #34): `patches/ff_fuel/src/ff_ign.c` (the
producer, in the 10 ms tick) and `ff_zw_hook` in `src/hooks.S` (the consumer),
specified by `emu/models/flexfuel.py`, `tests/test_ff_ign_patch.py`; bench and
road procedure `patches/ff_fuel/test/procedure_e1.md`.

| Quantity | Format | Where |
|---|---|---|
| `f_zw(E)` | u8, **1/256**, 17 points over E 0..100 step 6.25 % | `ff_fzw_curve`, FFCAL001 +0x42 |
| `dzw_E(nmot, rl)` | **8 × 8 s8, 0.75 °CA per count** | `ff_dzw_map`, FFCAL001 +0x54 |
| its axes | 8 × u16 in `nmot_w` / `rl_w` units | FFCAL001 +0xE8 / +0xF8 |
| `dzw_e` (the result) | **s8, 0.75 °CA**, positive = advance | `ff_state` +0x29 |
| the ceiling | `ff_dzw_max` = 8 counts = 6.00 °CA, code-clamped to 16 | FFCAL001 +0xE7 |

* **The axes are the stock ones.** `ff_dzw_nmot_axis` is every other
  breakpoint of `KFZW`'s nmot axis 0x5C7736 and `ff_dzw_rl_axis` is eight of
  the twelve of its rl axis 0x5C7758, so a cell lines up with a `KFZW` row and
  column and the `KFZWOP − KFZW` budget can be read against it directly. On
  that grid the budget is **0 or negative in 26 of the 64 cells** (at low load
  `KFZW` is already at or past the modelled optimum) and 8-14 counts at
  72-104 % load above 2900 rpm; the table is `procedure_e1.md` §B4.
* **The map is read with `rl_w` (0x7FEFB2)**, the KFZW column-axis input
  (`lhz -0x103E(r13)` at 0x41D358), not with `rl_for_fuel` 0x7FED38 (the
  charge `gk_rk` uses, selected from `rl_w` at 0x418A3C). Both cells are in
  `logging/sessions/ff_fuel.json` so a log can tell them apart.
* **`f_zw` saturates at 255/256, not at 1** (256 is not a u8): the shipped
  curve ramps from 0 at E0 to **255 at E50** and is flat above; 0.996 instead
  of 1 is 0.003 counts at whole 0.75 ° counts and disappears in the rounding.
* **No new RAM; the offset lives in the checksummed core** of `ff_state`
  (+0x29 `dzw_e`, +0x2A `fzw_q8`): they are control values written only by
  the periodic tick, so a corrupted `dzw_e` makes the next activation
  re-initialise the block rather than leaving a stale advance in the ignition
  path. The consumer checks only the block's magic, as `ff_rk_scale` does;
  what bounds it is the producing clamp, the stock s8 clamp at 0x41D410 and
  the −54..+58.5 ° output clamp.
* **The #37 rule is structural.** `ff_zw_update()` runs from `ff_finish()` on
  *every* path out of `ff_tick()`, so the activation on which the mode leaves
  OK/HOLD/OVERRIDE is the activation on which `dzw_e` is 0 — no hold, no
  ramp — while the fuel factor keeps its 60 s hold in the same activation.
  HOLD freezes the offset rather than dropping it (fault matrix step 6,
  `procedure_e1.md` §B2).
* **It ships disabled, three times over:** `ff_zw_enable` = 0, `ff_dzw_map`
  all zero, and `f_zw(E0)` = 0 (`ffcal001.py` refuses a curve that does not
  start at 0). The tests run task 41's `bl 0x41D38C` to completion on the
  stock and the patched image, with the feature off and with it on at the
  neutral map, and find `zwgru` bit-identical and no SRAM byte moved outside
  the patch's own block.
* **Diagnostics: VCDS measuring block 108** (TKMWL ids 2192-2195) — `f_zw` in
  %, `dzw_e` in °CA, the worst of the six `dwkrz` bytes, and `0x7FD31B & 3`.
  The last two are the two acceptance signals, so the whole calibration
  criterion is readable in one group.

**Open (the road half of #34):** every cell of `ff_dzw_map` is 0 and only a
car with real fuel can fill them in. `procedure_e1.md` §B3 is the recipe.

### 3.5 Start and warm-up

Ethanol needs roughly twice the cranking fuel around 10 C and barely ignites
below ~10 C without heating. **There is no separate after-start (`fnsk`) or
warm-up (`fwlk`) fuel factor in this software** (brief B8, `re/findings/start.md`
§4.1): every multiplicative term on `rk` is accounted for, and once the start
has ended the mixture comes from the torque/λ cascade (0x803020, Q12) and the
ECU holds λ = 1 instead of enriching, heating the catalyst with ignition
retard (the after-start λ request of §3.3, `KFLANS`, is the one exception and
it is inside `lamsbg_w`). So the design scales the **cranking quantity** by
`f_st(E, tmst)` and adds a small **start advance**, and lets the stock decay
of the start quantity over injections carry the ethanol correction into the
after-start; a warm-running ethanol correction, if one is ever wanted, is the
§3.3 `rk` hook gated on `tmst`, not a second map. This is the hardest part to
calibrate (reports agree), so it comes after the fuel factor is proven warm.

**The chain** (brief B8, #16; `re/findings/start.md`; model and bit-exact
emulator check `emu/start_model.py`, `tests/test_start_model.py`):

* **Cranking (VERIFIED-DYNAMIC in the emulator).** `%ESSTT` is `FUN_0041a268`
  (0x41A268) with the high-pressure twin `FUN_0041a690`. It publishes
  **`ksta × kstaa` at RAM 0x80302C, u16 with 1024 = 1.0**, which `gk_rk`
  (0x41AA48) multiplies at 0x41AA88 while the start has not ended; outside the
  start the module forces it to exactly 0x400. Maps: **`KFKSTT` 0x5C6E24**
  (12 `tmst` × 2 `prist`, u16, 24.8× at −32 °C down to 1.85× at +100 °C),
  **`KFWKSTT` 0x5C6C7C** (12 `tmst` × 14 injections-since-start, u8 128 = 1.0 —
  this *is* the after-start decay, and it is over injections, not seconds),
  **`KFWKSTN` 0x5C6C50** (off, all 255). **Insertion point S1: the `sth` at
  0x41A680 and 0x41A808** (the two twins publish from different registers,
  r31 and r3), Q10, saturate 0xFFFF; 0x400 = 1.0 is bit-identical at E0.
* **`tmst` is RAM 0x8021F6**, the coolant temperature latched at start;
  `tmot` is RAM 0x8021EF and `tmot_w` 0x802228. **Scaling for both: 0.75 °C
  per LSB, offset −48 °C**, proved by the start-map axes.
* **Start ignition (VERIFIED-DYNAMIC in the emulator).** While `B_stend` is
  clear the whole per-bank angle is replaced by **`zwstt` at RAM 0x802096**
  (s8, 0.75 °CA per LSB) in `zwbas_per_bank` (0x41D10C) — `zwgru`, the bank
  offsets *and the knock retard* are bypassed. `zwstt` is built by
  **`FUN_00431294` (0x431294)** from **`KFZWSTT` 0x5C7B64** (8
  ignitions-since-start × 8 `tmst`). **Insertion point Z1: the `stb` at
  0x431384.** Because knock control is inactive here, any ethanol advance is
  kept small and restricted to `tmst` below about 40 °C.
* The engine-state variables the patch needs: `B_st` 0x7FE91D, start end
  0x7FE920, **`B_stend` 0x7FE921** (and its segment-task copy 0x7FECCA),
  "engine not running" 0x7FEAD0, after-start timer 0x8011D8 (u16),
  injections-since-start 0x7FD269, ignitions-since-start 0x7FCE14.
* **The low-pressure `%ESSTT` does not return on its `B_stend` early-out**: it
  sets r31 = 0x400 and branches to 0x41A680, the hooked store, so that word
  runs on every activation of the segment task for as long as the engine
  runs. **Both S1 stubs therefore test `B_stend` 0x7FE921 themselves** and
  take the untouched path when it is set; without that gate the patch would
  have multiplied the ECU's explicit "no start enrichment" by `f_st` at every
  operating point (`start.md` §9.3 has the five instructions; this corrected
  `start.md` §3/§7).

**The implementation** (brief E2, #35; `re/findings/start.md` §9,
`patches/ff_fuel/README.md`, `patches/ff_fuel/test/procedure_e2.md`,
`tests/test_ff_start_patch.py`):

* `f_st(E, tmst)` scales `ksta_adapted` 0x80302C at **0x41A680** *and*
  **0x41A808**, and an ethanol advance `zwst_add` is added to `zwstt` 0x802096
  at **0x431384**. All three words are in the on-chip flash.
* **The producer is the same 10 ms tick**, from `ff_finish()`. It writes two
  fields in the state block, `fst_q10` (u16 Q10) and `zwst_add` (s8 counts of
  0.75 °CA).
* **`ff_fst_map`**: 6 ethanol rows × 6 `tmst` columns, Q10, shipped all 1024.
  Its `tmst` axis is six of the twelve `KFWKSTT` breakpoints (−30, −15, 0,
  +20.25, +39.75, +90 °C = counts 24, 44, 64, 91, 117, 184) so every cell lines
  up with a stock row, and its E axis is 0, 20, 40, 60, **85**, 100 %. **Row 0
  is the E0 row and `ffcal001.py` refuses a block whose row 0 is not exactly
  1024** — the start counterpart of `F(0) = 1024`. `ff_fzwst_curve` (6 × s8,
  shipped 0) borrows the same ethanol axis.
* **The #37 asymmetry, both halves in one function.** `fst_q10` is computed
  wherever `ff_tick()` computes `f_q10` from `e_filt` (OK, HOLD, **FAULT**,
  OVERRIDE), so it inherits the 60 s hold and the decay with no rule of its
  own; `zwst_add` is **0 on the activation the mode leaves OK/HOLD/OVERRIDE**,
  and additionally 0 at and above `ff_zwst_tmax` (117 counts = 39.75 °C). The
  start is where that asymmetry matters most, because `zwbas_per_bank`
  bypasses the knock retard while `B_stend` is clear.
* **Ceilings:** `ff_fst_max` 2048, code clamp `FF_FST_HARD_MAX` = 2560 because
  `(2560 × 100) >> 10 = 250` is the largest value measuring block 69 field 1
  can carry (formula 0x21, A = 100), so there is no `f_st` the patch can
  produce that a tester cannot see; `ff_zwst_max` 4 counts (3.00 °CA), code
  clamp `FF_ZWST_HARD_MAX` = 8 counts = 6.00 °CA — the clamp that matters most
  in the whole patch, because the knock retard is bypassed during the start.
* **Diagnostics: VCDS measuring block 69** (ids 2188-2191): `f_st` as a
  percent, the applied advance in °CA, `tmst` in whole °C, and `ksta_adapted`
  as a **raw count** — a percent byte would saturate at the stock 22.8× alone
  (`measuring_vars.md` §8.5).
* Both halves ship **disabled** (`ff_st_enable` = 0, `ff_zwst_enable` = 0)
  with neutral tables.

### 3.6 Rail pressure and injection window

E85 lengthens `ti` by 35-50 % at equal rail pressure. The rail-pressure
setpoint can be raised **inside the stock ceiling** by a blend on E%, which
buys atomisation and injector duty, and the injection window is watched with
diagnostics the stock ECU does not have; a torque limit on window overrun, if
one is ever wanted, has to be added — the stock ECU has none. The facts that
shaped this (brief B9, #17; `re/findings/rail.md`):

* **The pressure scale is 1 LSB = 0.005 bar and the stock ceiling is 110 bar.**
  `KLPRMAX` (six u16 at **0x5D5546**, all **22000**) is the hard setpoint
  ceiling, exactly 110.0 bar; the map the engine actually runs on,
  **`KFPRSOLHOM` at 0x5D5324** (8 × 8 u16, rows = nmot axis 0x5D558A, columns =
  load axis 0x5D5578), goes **35 bar at idle to 95 bar at high speed and
  load**. So there is **+15 bar of headroom inside the stock ceiling**,
  `sqrt(1.158)` = **7.6 % more injector flow** — useful for atomisation and
  duty, nowhere near E85's +40 % fuel demand. The rail raise supports the fuel
  factor of §3.3; it cannot replace it.
* **The ECU does *not* cut the throttle when the injection window is
  exceeded.** `awea_ti_to_angle` (0x41B9C0) compares `wbho1s (0x80307E) −
  dwi (0x803088)` against `0x7FD290 × 32` (**67 counts = 50.25 °CA**, a flat
  curve in this dataset) and, when the injection does not fit, **advances the
  start of injection** to `dwi + 50.25 °CA`, capped at 360.0 °CA. It sets **no
  flag**, raises **no DTC**, removes **no fuel** — and saturates silently at
  the cap. The one torque-domain path that exists (`0x803070` → `0x80235A`,
  VCDS id 2051, → `0x80360E` → throttle) is armed **only** by the fuel-system
  fault bit `0x80201E & 0x20`, a limp-home limit for a broken rail-pressure
  sensor, not a window limiter. If we want that limit we have to add it; the
  clean place is the existing min-chain at **0x0C7CF8**, which already reduces
  `0x80235A` from `0x803070` and propagates to the throttle like every stock
  protection limit. Its design note is `procedure_e5.md` §6; it is out of
  scope until bench data exist (`rail.md` §12.3, §14.4).
* **Even that clamp is disarmed on a healthy rail.**
  `0x7FEA48 = (prist > PRWBHMX 0x5D3CDC = 2600 = 13.0 bar)` gates all three
  interventions: the `awea` angle clamp, the charge limit, and a **hard
  injection cut-off angle** that `esausg_output` (0x409834) otherwise programs
  into the injector TPU driver at `cylinder reference − 50.25 °CA`. Since
  `KFPRSOLHOM` never asks for less than 35 bar, **in normal running none of it
  is armed: the injection window is enforced by calibration only.** The
  failure mode to design against: if a raised setpoint plus E85's higher
  volume demand ever lets `prist` fall below 13 bar, all three arm at once and
  the driver gets a simultaneous fuel cut and torque drop. **The acceptance
  signal for any rail raise is `prist` staying above 13 bar, and the early
  warning is `0x80316E` pinned at `VMSVMX` = 5000** (`rail.md` §14).
* **What the numbers say about the window.** `ti` is **1 µs per LSB** (from
  `k_nmot = (nmot_w × 34360) >> 16` and the 3/128 °CA angle LSB). At the worst
  `KFWBHO1SW` cell the hard clamp sits at **7.8 ms of `ti` at 6000 min⁻¹**,
  about **+73 %** over an assumed 4.5 ms stock WOT injection, so E85 (+40 %)
  has roughly 20 % reserve against the hard clamp at λ = 1 — see §3.3 for how
  the stock full-load and component-protection enrichment eats that reserve.
  What degrades first is not the clamp but mixture preparation, as the start
  of injection is pushed towards and past intake-valve opening.
* **The binding constraint is the pump, not the map.** The setpoint is
  rate-limited by the spare pump volume — `0x8031F6 = (VHDPMX 0x5D559C = 25120
  − vhdp_dem 0x803212) × 0.1` sets how fast `prsoll` may climb — and `%AMSV`
  clamps the delivery request at **`VMSVMX` 0x5D4BC6 = 5000** (`0x80316E`).
  **`0x80316E` pinned at 5000 means the pump is saturated and no map change
  will help.** Going above 110 bar would need `KLPRMAX` raised too and is not
  to be attempted blind: the sensor curve (0x5D518A) saturates near 138 bar,
  and the pump will not follow. The code-free calibration hook for an
  E-dependent raise, `KFPRSOLOFF` (0x5D5424, additive 8 × 8, 0..5000 =
  0..+25 bar, already summed into the homogeneous path since `CWPRSOL`
  0x5D521E bit 5 is set, weighted by the 6-point curve 0x5D5559), is listed
  in #45 as a bench edit.

**Log list:** `prist` 0x8031DA (VCDS id 500), `prsoll` 0x8031F4 (id 501),
`prdiff` 0x8031CA (id 1691), `0x80316E` (MSV volume after the limit),
`0x8031F6` (spare pump volume), `0x80235A` (id 2051, the charge limit), and —
via DDLI, since neither has a measuring id — **`dwi` 0x803088** and
**`wbho1s` 0x80307E**, whose margin is `0x80307E − 0x803088 − 0x7FD290 × 32`.

**The implementation** (brief E5, #36): an E-dependent setpoint adder *inside*
the stock ceiling, plus the diagnostics that say whether the pump follows.

```
10 ms tick            ff_rail_update()  in src/ff_rail.c, from ff_finish()
                        prail_add = clamp(interp17(ff_prail_curve, e_filt),
                                          0, min(ff_prail_max, 6000))
                      plus, unconditionally, the three window statistics

per 20 ms activation  ff_prail_hook  in src/hooks.S, 12 instructions
                        prsoll_raw = min(map_output + prail_add, 0xFFFF)
```

* **Where it lands, and why that is the whole safety argument.** The hook is
  the `sth r30,0x3200(r13)` at **0x45845C**, the single store of `prsoll_raw`
  0x8031F0 inside `hdrpsol_main` (0x45822C, from set A's 20 ms task 0x45CAC4)
  — after the six-map bank of `rail.md` §3.2 and **before** the `PRSOLMN`
  floor (7000 = 35.0 bar), the **`KLPRMAX` ceiling (22000 = 110.0 bar)** and
  the pump-volume rate limiter. All three still bind, because the clamp
  *re-reads the cell from RAM* four instructions later (`lhz r30,0x3200(r13)`
  at 0x458480) instead of re-using the register the store came from. **No
  calibration of this patch can put more pressure in the rail than the stock
  ECU already allows itself.** All six map-selection paths reach that word;
  the one path that must not be touched — `0x7FD04D & 1`, "hold the previous
  setpoint" — branches *past* it (`bne 0x458460`), so unlike §3.5's 0x41A680
  the stub needs no gate (`rail.md` §12.1 has the disassembly and the
  dead-register proof).
* **What it is for.** `ff_prail_max` ships at exactly 3000 = the whole 15 bar
  headroom, the code ceiling `FF_PRAIL_HARD_MAX` is 6000 = 30.0 bar (twice the
  headroom; anything above it is a calibration that lies, because the stock
  `KLPRMAX` clamp eats it), and `ff_prail_curve` (17 points on the ethanol
  grid, 0.005 bar) ships **all 0**. E85's +40 % fuel **mass** comes from `rk`
  and the F curve, not from here.
* **The #37 rule, rail flavour.** `prail_add` is **0 on the activation the
  mode leaves OK/HOLD/OVERRIDE** — no hold, no ramp, the same side of the line
  as the ignition blend and the start advance: a stale-rich mixture is safe, a
  stale *advance* or a stale *pressure demand* is not.
* **The diagnostics run even with the adder disabled**, because they observe
  stock cells, over a **tumbling** window of `ff_diag_window_ms` (1000 ms)
  with continuous publication (a sliding minimum would need a ring buffer of
  up to 6553 samples): `win_margin_min` = the worst
  `wbho1s − dwi − 0x7FD290 × 32` (the required margin is read from the RAM
  cell `awea_angles` writes, not from the literal 2144 = 67 × 32 this dataset
  happens to hold); `prist_min` = the worst `prist` (below `PRWBHMX` = 13.0 bar
  the driver cut-off, the injection-angle clamp and the fault charge limit arm
  **together**); `msv_sat_ticks` = activations with `0x80316E` at `VMSVMX`
  ("the pump is out of volume"). `diag_ticks` in the state block says where in
  the window a sample sits. All three plus the adder are **VCDS measuring
  block 109** (`21 6D`, ids 2184-2187), with the cross-checked pressure
  formula 0x53 for the two bar fields (`measuring_vars.md` §7.3, §8.6). Before
  E5 the injection window could not be watched on a car at all: `dwi` and
  `wbho1s` have no stock measuring id (`rail.md` §11).
* Ships **disabled** (`ff_prail_enable` = 0) with the neutral curve.

### 3.7 Diagnostics

The patch publishes its state where a stock tester can read it. No DTC is
raised by the patch; a fault only switches the mode (§3.2), and the mode is
visible.

**VCDS measuring block 111** (brief D2, #39; `21 6F` over KWP;
`re/findings/measuring_vars.md` §8, `tools/measuring_vars.py --free`):

| Field | Value | Measuring id | Formula | Reads |
|---|---|---|---|---|
| 1 | `E_filt`, whole % | **2196** | 0x21, A = 100 → the value is B | `ff_diag_e_pct` |
| 2 | `F`, % (100 = 1.000) | **2197** | 0x21, A = 100 | `ff_diag_f_pct` |
| 3 | `T_fuel`, °C | **2198** | 0x05, A = 10 → `B − 100` (CROSS-CHECKED, §7.1) | `ff_diag_t_degc` |
| 4 | `256 × persist_state + mode` | **2199** | 0x36, a plain count | `ff_state.mode`, `persist_state` |

The ids are the **last four entries of `tbl_measuring_vars`** (0x0A78A8, one
contiguous 16-byte edit written as `u32_syms`, `06_patch_pipeline.md` §1.1);
all four pointed at the "not available" stub and no stock group named them.
Group 111 and its `+0x7F` echo 238 were both empty, so the whole 25-byte
answer to `21 6F` belongs to the patch. The three later features took the
equally free groups **108** (ignition, ids 2192-2195, §3.4), **69** (start,
ids 2188-2191, §3.5) and **109** (rail and window, ids 2184-2187, §3.6);
`07_workflow.md` §4.4 shows them read with the simulator.

**OBD mode 01 PID 0x52** (briefs F6 and G1, #39; the transport, H3, #48;
`re/findings/obd.md`; `tests/test_ff_obd_patch.py`). The generic-OBD stack is
in this image: J1979 modes 0x01-0x09 are nine entries of the 28-entry KWP
dispatch table at 0x2B820, gated to internal sessions 4 and 6, mode 01 is
`obd_mode01_h1` (0x5D0F4), and a PID needs a byte in the dense class table at
0x0A39B4 (index = PID) **and** an entry in one of five (record-pointer, PID,
support-mask) lists in calibration 0x5C5D24-0x5C5E1B. All five lists were
exactly full (20 + 8 + 3 + 6 + 4 = 41 entries, each loop bound an immediate
in stock code), so PID 0x52 is not the "table word + handler" shape block 111
used. What the patch does:

* **seven instruction words** in `obd_pid_support_build` / `obd_pid_read`
  (external flash, block 0x058000-0x05FFFF) point the three-entry B2 list at
  a relocated four-entry copy, and `tbl_obd_pid_class[0x52]` becomes **0x02**.
  The copy is not in calibration — every 0xFF run in the r2 window is a live
  map cell or the segment header — but in the blank flash block
  0x160000-0x16FFFF, reached by `lis` / `addis r2` / `addis r13`, one
  instruction each. No hook word was added.
* **The gate is `ff_pid52_enable`** (FFCAL001 +0x14A, ships **0**). The patch
  writes the record `{A, valid}` at `ff_state` +0x4C every 10 ms, with
  `A = round(E_filt × 255 / 100 %)` and `valid = ff_pid52_enable && cal_ok`;
  the stock builder turns `valid` into the `01 40` support bit (the bitmaps
  are RAM, 0x801215-0x801220, rebuilt by `obd_pid_support_build` 0x5CBE8 once
  per new diagnostic connection) and the stock reader answers `41 52 A`. With
  the byte at 0 the image is **observably identical to stock on mode 01**
  (bitmaps and every PID answer, proven in the emulator against all 41 stock
  PIDs) though not byte-identical — the seven words and the list are always
  there.
* **The transport.** A request reaches the OBD handler only on the
  **functional** id 0x7DF (MB15 → `obd_func_rx_ind` 0x0B5534 /
  `isotp_rx_indication` 0x1420A0 → dispatcher; `kwp_conn_open` writes 0x33 to
  0x7F804B at 0x13F1E4, i.e. internal session 6); a **physical 0x7E0**
  request is received and never answered (gate `li r3,0` at 0x2C29C). The
  answer goes out on 0x7E8 (`isotp_transmit` 0x1429BC, `0N` + 0x00 padding),
  silence is the normal "no", and the connection times out 5.00 s after the
  last answer — so after enabling `ff_pid52_enable`, wait more than 5 s
  without a request before reading `01 40`, because it is the next request
  that opens a connection and rebuilds the bitmap. `logging/obd_client.py`
  speaks it; `ecu_sim.py --obd-can` runs the firmware's own ISR, ISO-TP and
  connection code, and the rehearsal covers the route. Never exercised on
  hardware (`08_bench_playbook.md` step 7 item 4).

Measuring block 111 is the primary display; PID 0x52 is the optional half of
#39 and enabling it is a calibration change, so a flash of its own.

### 3.8 Persistence (E% across power loss, #38)

`E_filt` is stored in the SPI EEPROM through the ECU's own block manager, so
a cold start after a battery disconnect uses the right cranking fuel. Route
decided by brief B4 (#18), implemented by D2, corrected by E4 and G7;
`re/findings/eeprom.md`, `tools/eeprom_map.py`, `patches/ff_fuel/src/ff_diag.c`
(`ff_persist_init`, `ff_persist_tick`), bench procedure
`patches/ff_fuel/test/procedure_d2.md` part B.

**The store: EEP_CONF block 8, payload +19, one byte** (VERIFIED-STATIC for
everything except the factory contents of the byte, which `08_bench_playbook.md`
step 3f reads before the first `ff_fuel` flash).

* The SPI EEPROM is a 2 KB **M95160-class** part on **PCS0** of the QSMCM
  QSPI (0x705000), SPI mode 0, 8 bits per transfer, SCK ≈ 1.25 MHz
  (`eeprom_spi_config` 0x085888, `eeprom_write_byte` 0x085A8C,
  `eeprom_read_bytes` 0x085BC0). Its layout is a **32-record table at file
  0xB2FF0** (`tbl_eep_conf`), 12 bytes per record, driven by the block
  manager `nvm_block_request` (0x06131C); it covers 0x000-0x7FF with **no gap
  and no spare block**, so a 33rd block is not an option.
* **Block 8** lives at EEPROM 0x1C0 with a **duplicate copy at 0x1E0**, is 32
  bytes (one page, so a commit is a single ~5 ms page write) and mirrors to
  RAM **0x7F9F80**. Its payload:

  | payload | holds | who writes it |
  |---|---|---|
  | +0, +1 | the `{block id, version}` stamp `08 01` | the manager. `nvm_read_all_blocks` (0x06227C) compares the first halfword of each block against its record in the flash default table (0x060458-0x060470) and on a mismatch **discards the block and reloads the defaults** |
  | +2 … +18 | the **17 tester adaptation channels**, channel *k* at +(k+1) (§3.3); defaults `00 80 80 80 80 00 00 80 00 80 80 FF` then 0 | `adaptation_restore_all` 0x12E3F8 reads them at power-up; the stock reset-all 0x038D64 rewrites them after a fault clear when block 11 +11 bit 0 is set (routine 0xC5 only, H2); the tester service reaches channel 7 only on this dataset |
  | **+19** | **the ethanol percent** (`ff_persist_offset` = 19) | `ff_fuel` |
  | +20 … +28 | free (default 0x00) | nobody |
  | +29 | the manager's ReplV byte (`flags = 0x03F5`); it moves 0xFF → 0x00 → 0x01 on the first commit | the manager; the patch never writes it |
  | +30, +31 | the block checksum | the manager |

  Block 8's default record (file 0xB32DC):
  `08 01 00 80 80 80 80 00 00 80 00 80 80 FF` followed by 0x00 × 18 — so a
  stamp or checksum failure reloads **0x00** at +19, which the patch reads as
  E0, the same as an unwritten store.
* **The exclusion set for +19..+28** (G7, `eeprom.md` §5, VERIFIED-STATIC): the
  flash default record writes 0x00 there; of the 87 constant-offset
  `nvm_block_request` sites the only block-8 one stages 1 byte at +14
  (channel 13); the service-0x83 commit stages `n + 2` for n = 0..16 and
  queues `(8, 2, 17, …)` → +2..+18; `nvm_read_all_blocks` compares payload +0
  only, so a non-default +19 is kept; the raw SPI writers behind the manager
  address 0x274 and 0x288-0x2B8 (blocks 10 and 11) and nothing in
  0x1C0-0x1FF; there is no direct store into the mirror 0x7F9F80-0x7F9F9F. So
  **no stock path writes block 8 +19..+28** except the manager's whole-record
  copies, which carry the byte through unchanged or replace it with the
  default 0x00. Emulator proofs: `tests/test_ff_diag_patch.py`
  (`TestPersistOffsetOffTheChannels`: the real `adaptation_restore_all` over
  a block carrying the E% at +19 moves no channel byte; the reset-all
  0x038D64 rewrites +2..+18 and leaves +19; `TestPersistenceThroughTheDeviceAt19`:
  the end-to-end path through the QSPI device model).
* **Write path — stock code only.** Stage
  `nvm_block_request(8, 19, 1, 0, &e_pct, 0)` (returns 2), then commit
  `nvm_block_request(8, 0, 0, 0, 0, &handle)` (returns 1) and poll the handle.
  The read-back is `nvm_block_request(8, 0, 1, **1**, &dst, 0)` — mode 1;
  with mode 0 the identical argument list is the *stage* shape and overwrites
  the mirror with whatever the destination buffer held (the shape is chosen
  by a 16-entry table at 0x6199C indexed by
  `8×(len≠0) + 4×(handle≠0) + 2×(buf≠0) + mode`, `eeprom.md` §8.1). The
  "handle" is a **9-byte record, not a word**, and the manager keeps a
  *pointer* to it in a 4-slot queue, so it lives in the patch's own RAM
  (`ff_nvm_req`, 0x7FFB54); `+8` is the status: 1 queued, **2 done**, 0x80
  device failure, 0x82 checksum failure (§8.2-8.3). The commit path calls
  `nvm_checksum_generate` (0x061A48), which recomputes the block checksum —
  the **16-bit sum of payload bytes [0, len−2), stored bit-complemented as a
  big-endian u16 at offset len−2**, *not* the flash algorithm of
  `02_memory_map.md` §6 — and writes both copies. Hence the one hard rule:
  **never write the EEPROM through the raw SPI primitives**, or the patch has
  to maintain the checksum itself and will race the manager's mirror.
* **When it commits.** "Commit at key-off" is not available: block 8's one
  stock client never commits, the write-all-blocks routine has no resolvable
  trigger, and the synchronous-shutdown flag's two setters have no callers
  (`eeprom.md` §9). So the patch commits *while the engine runs*, at most one
  page write per `ff_persist_rate_s` (60 s) and only on a change of more than
  `ff_persist_hyst_pct` (5 %) in mode OK or HOLD. The stored value is then at
  most one minute old and does not depend on an orderly shutdown — strictly
  better than a key-off flush for the case #38 cares about, a battery
  disconnect. M95160 endurance is ~1e6 cycles (COMMUNITY, ST datasheet).
* **The restore seeds both `e_filt` and `e_key`** (§3.2). Seeding only the
  decay target would leave the first 50 s of a cold start on the E0 fuel
  factor with an E85 tank — lean, the dangerous direction. A store that reads
  0xFF or above 100 is ignored and the patch starts at E0. `persist_state`
  (0 idle, 1 staged, 2 committing, 3 done, error) is the high byte of
  measuring block 111 field 4.
* **`ff_persist_enable` ships as 1** (ruling, Carlo, 2026-09-24): persistence
  is part of the D2 fuel-path design, and the ships-disabled rule of
  2026-09-17 applies to the features added after it. The bench step is a
  *read* — block 8 payload +19..+28 on the real ECU before the first `ff_fuel`
  flash (`08_bench_playbook.md` step 3f, stop line S12; `eeprom.md` §5
  predicts 0x00) — not a rebuild with the store off; if the bytes are anything
  else, build with `ff_persist_enable` = 0 (`procedure_d2.md` §B4) and report.

**How the offset got to +19.** D2 shipped `ff_persist_offset` = 0. E4, with
the QSPI device model and a run of the real `nvm_read_all_blocks`, found that
+0/+1 are the block stamp: the E% was written on top of the block id, every
cold start threw the block away, the mirror byte read back `0x08`, and
`ff_persist_init()` accepted it as a plausible **8 %** — #38 did not work and
nothing said so (`eeprom.md` §10.5, §10.6). The offset moved to 2, one
calibration byte, which is the argument for having put the block, the offset
and the rate limit in FFCAL001 in the first place. G2 then found (F4's
`adaptation_restore_all` loop, descriptor bytes `08 02` at 0x0A3AD8, `PTR[0]`
= 0x7FD06B) that +2..+18 are the 17 adaptation channels, so +2 was channel
1's slot: benign for the restore (the only reader of 0x7FD06B passes it as
the *value* of `clamp(value, 0, 0)` at 0x410ACC, so the result is 0 whatever
the byte holds) but zeroed by the stock reset-all — an E85 tank read as E0,
the lean direction, the same pattern as the 8 % bug. G7 moved the store to
+19 (a default value, not a layout change; FFCAL001 stays v5) and built the
exclusion set above.

**Routes that are not used.**

* **External SRAM as battery-backed RAM: refuted** (C2, #23). 0x800000-0x807FFF
  is ordinary `.bss`: `ram_clear_block` (0x06D8F8), called from `app_init`
  (0x04CCD4), zeroes 0x800004-0x80498F at every cold start, and
  0x804990-0x807FFF is the flash driver's programming copy; only the four
  bytes the boot sizing probe saves survive (`ram.md` §3, `eeprom.md` §6).
* **EEP_CONF block 24** (EEPROM 0x620, 255 bytes, single copy, an 8-page
  ~40 ms write) is not a fallback either: the stock fault-clear path commits
  it (`kwp14_clear_state` 0x7FB718, G5) and a 61-byte access at a computed
  offset reaches into it (`eeprom.md` §5).
* **`vkKraQu`**, the fuel-quality variant byte the 1K8907115F/L community
  patches manipulate, **does not exist in this software**
  (`re/findings/variants.md`); there is no stock variant byte to reuse for a
  map-set switch and no coding bit the fuelling path reads.

## 4. New calibration data — FFCAL001

All new parameters live in one block at **0x5E2510** (file 0x1E2510) inside
the calibration checksum block 0x5E0000-0x5EFFFF, in the free space that is
all 0xFF in the stock file (`02_memory_map.md` §5). `patches/ff_fuel/ffcal001.py`
builds it from `ffcal001.json` and is the authority on the layout;
`patches/ff_fuel/src/ff_state.h` carries the same offsets and
`tests/test_flexfuel_model.py` asserts the two agree. Header `FFCAL001` +
version u16 + length u16; the last two bytes are a 16-bit checksum, the
bit-complement of the byte sum of `[0, length−2)`. `ff_cal_ok()` accepts
**only the current version**, so an older block flashed under a newer blob
reads as corrupt and forces mode OFF (`F = 1024`, no CAN, every offset 0, every
factor 1.0) — the safe direction.

**Version 5, 334 bytes (`LENGTH = 0x014E`), shipped values:**

| Off | Name | Type | Shipped | Unit / meaning |
|---|---|---|---|---|
| +00 | magic | 8 B | `FFCAL001` | |
| +08 | `ff_cal_version` | u16 | 5 | |
| +0A | `ff_cal_length` | u16 | 334 | bytes, checksum included |
| +0C | `ff_can_id` | u16 | 0x0EC | documentation and a sanity check: the receive id really lives in the flash edit at 0x2BD8C; the id echo at 0x803F98 is compared against this |
| +0E | `ff_timeout_ms` | u16 | 1000 | no good frame for this long → FAULT |
| +10 | `ff_hold_s` | u16 | 60 | FAULT: hold the last F this long before decaying |
| +12 | `ff_filter_tau_ms` | u16 | 3000 | first-order time constant of the E filter |
| +14 | `ff_slew_pct_s` | u16 | 2 | maximum \|dE/dt\|; clamped to 1..100 in code |
| +16 | `ff_tick_ms` | u16 | 10 | real period of the periodic hook (§8) |
| +18 | `ff_mode` | u8 | 1 | 0 off (F = 1.000, no CAN), 1 normal, 2 bench override |
| +19 | `ff_e_override` | u8 | 0 | ethanol % used in mode 2 |
| +1A | `ff_stall_max` | u8 | 3 | frames with an unchanged counter → FAULT |
| +1B | `ff_persist_enable` | u8 | **1** | store E% in EEPROM (§3.8) |
| +1C | `ff_persist_hyst_pct` | u8 | 5 | minimum E% change before a commit |
| +1D | `ff_persist_block` | u8 | 8 | EEP_CONF block |
| +1E | `ff_persist_offset` | u8 | **19** | payload offset inside that block |
| +1F | `ff_persist_rate_s` | u8 | 60 | minimum seconds between two commits |
| +20 | `ff_F_curve[17]` | u16 | formula (§3.3) | 1/1024 over E 0..100 step 6.25 % |
| +42 | `ff_fzw_curve[17]` | u8 | ramp 0 → 255 at E50, flat above | 1/256 |
| +54 | `ff_dzw_map[8][8]` | s8 | **0** | 0.75 °CA; rows nmot, columns rl |
| +94 | `ff_fst_map[6][6]` | u16 | **1024** | 1/1024; rows E, columns `tmst` |
| +DC | `ff_prail_add[8]` | u8 | 0 | v1 reservation, **superseded by `ff_prail_curve`**, unread, kept so nothing moves |
| +E6 | `ff_zw_enable` | u8 | **0** | 1 applies the ignition blend |
| +E7 | `ff_dzw_max` | u8 | 8 | 0.75 °CA counts; code clamps to 16 |
| +E8 | `ff_dzw_nmot_axis[8]` | u16 | every other `KFZW` row breakpoint | `nmot_w`, 520…6520 rpm |
| +F8 | `ff_dzw_rl_axis[8]` | u16 | eight of `KFZW`'s twelve columns | `rl_w`, 10.2…103.9 % |
| +108 | `ff_st_enable` | u8 | **0** | 1 applies `f_st(E, tmst)` |
| +109 | `ff_zwst_enable` | u8 | **0** | 1 applies the start advance |
| +10A | `ff_fst_max` | u16 | 2048 | Q10 ceiling; code clamps to 2560 |
| +10C | `ff_zwst_max` | u8 | 4 | 0.75 °CA counts; code clamps to 8 |
| +10D | `ff_zwst_tmax` | u8 | 117 | `tmst` count = 39.75 °C; no start advance at or above |
| +10E | `ff_fst_e_axis[6]` | u8 | 0, 20, 40, 60, 85, 100 | % — the map's rows |
| +114 | `ff_fst_tmst_axis[6]` | u8 | 24, 44, 64, 91, 117, 184 | `tmst` counts (−30 … +90 °C) — the columns |
| +11A | `ff_fzwst_curve[6]` | s8 | **0** | 0.75 °CA, on the E axis above |
| +122 | `ff_prail_enable` | u8 | **0** | 1 applies the rail adder |
| +123 | `ff_prail_rsv` | u8 | 0 | reserved; keeps the two words below 2-byte aligned |
| +124 | `ff_prail_max` | u16 | 3000 | 0.005 bar = 15.0 bar, the headroom under `KLPRMAX`; code clamps to 6000 |
| +126 | `ff_diag_window_ms` | u16 | 1000 | the tumbling diagnostic window |
| +128 | `ff_prail_curve[17]` | u16 | **0** | 0.005 bar over E 0..100 step 6.25 % |
| +14A | `ff_pid52_enable` | u8 | **0** | 1 answers OBD mode 01 PID 0x52 |
| +14B | `ff_obd_rsv` | u8 | 0 | reserved; keeps the checksum 2-byte aligned |
| +14C | checksum | u16 | | |

`ffcal001.py` refuses to *build* a block that would be unsafe, rather than
leaving it to the ECU: `ff_F_curve[0] ≠ 1024` or non-monotonic or above 2048;
`ff_fzw_curve[0] ≠ 0` or non-monotonic; an axis that is not strictly
increasing (the breakpoint search assumes it); `ff_dzw_max` above the code
ceiling; an `ff_fst_map` whose row 0 is not exactly 1024, any cell below 1024
(`f_st` may only enrich) or above 2560; `ff_fst_max` outside 1024..2560;
`ff_zwst_max` or `ff_fzwst_curve` above 8 counts or `ff_fzwst_curve[0] ≠ 0`;
`ff_prail_curve[0] ≠ 0` or non-monotonic (more ethanol may not mean less
pressure), any point or `ff_prail_max` above 6000, `ff_diag_window_ms` = 0;
a non-zero reserved byte; `ff_pid52_enable` not 0 or 1.

**The rule for changing it** (`docs/agent_briefs/00_common_rules.md`,
`07_workflow.md` §2.4): **changes append and nothing moves.** Every version so
far added its parameters where the previous checksum used to be and moved the
checksum to the new end, so every earlier offset is exactly where it was:

| Version | Bytes | Brief, date | Appended |
|---|---|---|---|
| v1 | 232 | D1, 2026-09-16 | the header, the §3.2 parameters (`ff_filter_k` from the first draft became `ff_filter_tau_ms`, and `ff_tick_ms` was added, so every time constant is physical), `ff_mode` / `ff_e_override` / `ff_stall_max`, the five persistence parameters, `ff_F_curve`, and reservations for `ff_fzw_curve`, `ff_dzw_map`, `ff_fst_map`, `ff_prail_add` with neutral values |
| v2 | 266 | E1, 2026-09-17 | `ff_zw_enable`, `ff_dzw_max`, the two `ff_dzw_map` axes; `ff_fzw_curve` and `ff_dzw_map` became live |
| v3 | 290 | E2, 2026-09-17 | `ff_st_enable`, `ff_zwst_enable`, `ff_fst_max`, `ff_zwst_max`, `ff_zwst_tmax`, the two `ff_fst_map` axes, `ff_fzwst_curve`; `ff_fst_map` became live |
| v4 | 332 | E5, 2026-09-17 | `ff_prail_enable`, `ff_prail_rsv`, `ff_prail_max`, `ff_diag_window_ms`, `ff_prail_curve` (the v1 `ff_prail_add` was the wrong shape — eight u8 in 0.1 MPa with no axis — and stays in place unread) |
| v5 | 334 | G1, 2026-09-23 | `ff_pid52_enable`, `ff_obd_rsv` |

Default-value changes do not bump the version: `ff_persist_enable` 0 → 1 (D2,
2026-09-16), `ff_persist_offset` 0 → 2 (E4, 2026-09-17) → 19 (G7, 2026-09-24).

Descriptor rows for the TunerPro definition are `patches/ff_fuel/ffcal001_rows.csv`
(in `re/calibration_draft.csv`'s columns); the two maps go into the XDF as
`map_2d` with their own breakpoints, and `ff_fzwst_curve` borrows the ethanol
axis. Every FFCAL001 cell reads 255 in the stock file.

## 5. RAM

`struct ff_state` at **0x7FFB00, 80 bytes**, plus `ff_persist_buf` (0x7FFB50)
and the 9-byte NVM request record `ff_nvm_req` (0x7FFB54): **96 bytes** of the
256-byte patch block `06_patch_pipeline.md` §3 allocates. The block starts
with a magic word, a length and a checksum over the *core* — the control
values written only by the periodic tick (`e_filt`, `f_q10`, `dzw_e`,
`fst_q10`, `zwst_add`, `prail_add`, …) — and the first activation that finds
the checksum wrong re-initialises the block, so a corrupted control value
never lingers; the *annex* (values other writers or pure diagnostics touch)
is outside the checksum. The hook stubs check only the magic. The layout,
offset by offset, is `patches/ff_fuel/README.md` "The RAM block"; the
core/annex split is explained in `src/ff_state.h` and must stay true when
the struct grows (append past the current end, never insert).

The block is VERIFIED-STATIC free (no instruction in the image names any byte
of 0x7FF770-0x7FFFEB, `re/findings/ram.md`); the six runtime snapshots of #23
(`08_bench_playbook.md` step 4) make it VERIFIED-DYNAMIC, and nothing is
flashed before them.

## 6. Verification plan

| Step | Where | Pass criterion | Status |
|---|---|---|---|
| Unit tests of filter, fail-safe state machine, curve interpolation, and every hook | emulator | matches the Python model bit for bit; the hooked stock code is bit-identical with each feature disabled and with it enabled at neutral calibration, and no SRAM byte moves outside the state block | **done**, `tests/test_ff_*.py` (`07_workflow.md` §2.5) |
| Every bench procedure against the simulated ECU, Pico and EEPROM | `logging/bench_rehearsal.py --fresh-eeprom` | every graded check passes | **done**, 84/84 on `main` 4fcff77; re-run on the day |
| Pico status paths | signal generator | correct status/E% for 40, 49, 50, 100, 150, 156, 185 Hz, no signal | open (#29) |
| RX slot proof | bench ECU + logger | frame bytes appear at 0x803F9C..0x803FA3 and `ff_mode` leaves FAULT | open (#22; `procedure.md` §2-§3) |
| E0 equivalence | bench (engine-off) then car | logs (lambda, ti, fra, zw) identical to stock within noise over the same scenario, `logcmp --align-on` | open (#32; `procedure.md` §4, `07_workflow.md` §5) |
| Fault matrix | bench, then car at idle and driving | each of the six injected faults: fuel factor held then decayed, ignition/start/rail terms to zero, recovery on replug | open (#37; `procedure.md` §5) |
| Persistence | bench, then car | a distinctive E% survives a power cut and seeds the first activations | open (#38; `procedure_d2.md` part B, after the block-8 read of S12) |
| Blend steps E20, E50, E85 | car, wideband | `fra/frau` within ±5 % of 1.0 after adaptation reset; no lean event at WOT; `dwkrz` unchanged; the window margin judged at the lowest `lamsbg_w` of the pull (§3.3); rail follows setpoint, `prist` > 13 bar | open (#33) |
| Ignition, start, rail — one at a time | car | `procedure_e1.md`, `procedure_e2.md`, `procedure_e5.md` | open (#34-#36) |
| Cold start series | car, over a season | start time and afterstart lambda at 20, 10, 0, −10 C on E85 comparable to gasoline | open (#40) |

## 7. RE targets and their state

| Target | Done when | State |
|---|---|---|
| Powertrain TouCAN module and the RX slot → RAM mapping for the table at 0x2BC90 | writing a frame to a free slot's id on the bench makes its bytes appear at a known RAM address | static: slot 15 → 0x803F9C (§3.1); the bench half is #22 |
| Periodic task / hook point | a counter patch increments at the raster rate | static: the 10 ms rasters of both task sets, 0x432940 and 0x12067C (§8); the counter (Flash 1, #27) must rise at **100/s**; set A live is confirmed by row A of the decision table |
| Fuel mass → injection quantity multiplication (KRKATE path) and its variables | logged `ti` reproduces the decompiled formula for logged inputs | static: `rk` 0x803038, hook 0x42247C, `ti` 1 µs/LSB (§3.3, §3.6); the logged check is a #44 row |
| Lambda adaptation variables (`fra`, `frau`, `frao`, `rkat`) and the lambda request | found in the measuring-variable table and logged | static: `lamsbg_w` 0x80304A and its requesters (§3.3, H1); `tuning_checklist.json` logs them |
| `KFZW/KFZW2` blend and the final `zw` output; `dwkrz` | map addresses confirmed by xref and by a bench edit | static: no blend exists, `KFZW` 0x5C75FE, `KFZWOP` 0x5CA3F1, `dwkrz` 0x7FCE57-5C, insertion point 0x41D40C (§3.4). **The bench edit is #45** |
| Start/afterstart maps and `tmot` | same | static: `KFKSTT`, `KFWKSTT`, `KFZWSTT`, `tmst` 0x8021F6 (§3.5); bench edit #45 |
| Rail setpoint maps and `ti` window limits | same | static: `KFPRSOLHOM`, `KLPRMAX`, `KFPRSOLOFF`, `KLWBHO1SMX` (§3.6); bench edit #45 |
| `vkKraQu` presence (fuel-quality variant byte) | present/absent decided | **absent** (`variants.md`) |
| EEPROM block handler | write path understood | done (§3.8) |
| Security access algorithm (KWP 0x27) | logger authenticates | done, VERIFIED-DYNAMIC by emulation (`kwp.md`); the logger needs it only for RequestUpload |

## 8. The scheduler rasters

`re/findings/scheduler.md` §11-§13 settle the ERCOSEK periods (brief C4, #44):
the tick unit an earlier pass used was five times too coarse and the
activation chain was not known, so **every raster is ten times faster than
the stock symbol names suggest**. The names are kept because five findings
files and the issue tracker use them — **the names are wrong, the addresses
are right**:

| Raster | Task set A | Task set B |
|---|---|---|
| 1 ms | 0x4240C8 | 0x11EBF4 (`task_10ms`) |
| 2 ms | 0x424900 | 0x11EC34 (`task_20ms`) |
| 5 ms | 0x424AF8 | 0x11EC58 |
| **10 ms** | **0x4328E4** (`task_100ms_int`) | **0x1205A0** (`task_100ms`) |
| **20 ms** | **0x45CAC4** (`task_1000ms_int`) | **0x120FAC** (`task_1000ms`) |
| 50 / 100 / 200 / 1000 ms | ids 25 / 18 / 22 / 17 | ids 37 / 31 / 34 / 30 |

What this means for the patch:

* **The periodic hook runs at 100 activations per second**, once per 10 ms
  raster, in whichever task set is live. `patches/ff_fuel` and
  `patches/ff_counter` hook the 10 ms raster of **both** sets — set A at
  **0x432940** (in `task_100ms_int` 0x4328E4, on-chip) and set B at
  **0x12067C** (in `task_100ms` 0x1205A0, external) — and record which one
  ran. A one-minute bench run of the counter gives ~6000 counts, not ~600.
* **Task set A is the live set** (VERIFIED-STATIC, brief E1, `scheduler.md`
  §11.8): set A is installed by `os_init`, and 0x11DA64 would switch to set B
  only if the byte 0x7FEB5E were non-zero, which the engine cannot run with.
  A hook in set B alone would never execute. The bench confirms it in one
  10-second read of five RAM counters (`wave_b_confirm.json`, `docs/08` step
  3c: 0x7FD754 +100/s, 0x7FD75C +1000/s, the three set-B cells frozen) and by
  Flash 1 row A.
* **`can_rx_poll(15)` from the 10 ms raster** polls ten times faster than the
  Pico's 100 ms frame rate; the freshness and age logic of §3.2 gets ten times
  more samples than the first draft budgeted.
* **The filter and the slew limit are expressed per activation** through
  `ff_tick_ms` (§3.2); a 3 s time constant is 1/300 per 10 ms tick, and the
  2 %/s slew is 0.02 % per tick, which is why `E_filt` carries a sub-count.
* **`rksplit` at 0x42247C is unaffected**: that hook is in the
  engine-synchronous task 0x4223B0, not in a time raster. The ignition hook
  (task 41) and the start hooks (segment tasks) are engine-synchronous too;
  the rail hook is in set A's **20 ms** task 0x45CAC4, so the ethanol term
  reaches `prsoll_raw` within one 20 ms period.
* Task 20 is never activated (80 ActivateTask sites resolved, H4) and
  0x477B48 is a deferred `ChainTask(self)`; neither affects the hooks.
