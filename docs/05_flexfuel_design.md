# Flex-fuel design

Status: design proposal. Every ECU-side insertion point below is an RE
target (section 7) and must reach VERIFIED-DYNAMIC before the corresponding
patch is written.

## 1. Architecture

```
GM/Continental flex-fuel sensor ──50-150 Hz / 1-5 ms──▶ Pico 2 (capture, plausibility)
        │                                                  │ CAN 500 kbit/s, 10 Hz
   in the fuel FEED line                                   ▼
                                              powertrain CAN (behind the J533 gateway)
                                                           │
                                              MED9.1.1: spare RX slot ─▶ ff_rx (validate, filter)
                                                           │
                          ┌──────────────┬─────────────────┼──────────────┬──────────────┐
                          ▼              ▼                 ▼              ▼              ▼
                    fuel factor    ignition blend    start/afterstart  rail-pressure   diagnostics
                    (mass -> te)   (KFZW offset)      enrichment       setpoint raise  (measuring block)
```

MED9.1 has no native flex-fuel function (unlike Simos18 or MED17.1.1), so
all of this is custom code. The proven insertion patterns on Bosch ECUs are:
scale the fuel-mass-to-time constant (woj's ME7.9.10 `rkte` hook), blend
double maps the way the ECU already blends `KFZW/KFZW2` (prj), and drive an
existing variant selector (`vkKraQu`, if present in our software).

## 2. Sensor and Pico

Sensor: GM/Continental 13577379 (3/8" barbs) or 13577394; open-collector
square wave, needs a 2.2-3.5 kOhm pull-up to 5 V (1 k/10 k do not work),
12 V supply, sensor ground. Frequency 50 Hz = 0 % .. 150 Hz = 100 % ethanol
(linear); low-pulse width 1 ms = -40 C .. 5 ms = 125 C fuel temperature
(`T = 41.5 * ms - 81.25`). Fault codes: 180-190 Hz contaminated fuel or
internal fault, below 50 Hz or no signal = fault. Pump "E85" typically reads
~75-81 %. Mount in the **feed** line; return-line mounting traps air and
produces spurious readings (multiple reports).

Pico firmware requirements (replaces the current pot demo in `pico_can_sender/`):

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
CAN with `pi_can_setup/find_active_ids.py` (0x0EC is not in the ECU's RX or
TX tables; the ICAN DBC in `data/` is a different bus). The frame is
injected on the powertrain CAN directly (the gateway does not forward
unknown ids from the OBD port).

Bench: a signal generator (or a second Pico) drives the sensor input 40-200 Hz
to test every status path before the real sensor is wired.

## 3. ECU side

### 3.1 Reception
The CAN receive table at 0x2BC90 has four unused slots (id 0x7FF). Plan:
re-use one slot for 0x0EC, find where its 8 data bytes land in RAM and where
the "new data" flag is set (Phase 1 target), and read them from `ff_rx`,
which runs from a periodic task (100 ms is enough). Alternative if the
slot mechanism is awkward: hook the generic RX dispatcher and intercept id
0x0EC before it is dropped.

### 3.2 Validation, filtering, state

```
inputs:  E_raw, T_fuel, status, counter, age (ms since last frame)
state:   E_filt (0..100, 1/16 % resolution), fault_timer, mode

if age > 1000 ms or status in {fault, not ready} or counter unchanged for 3 frames:
    mode = FAULT
elif status == contaminated:
    mode = HOLD                     # keep E_filt, do not follow E_raw
else:
    mode = OK; E_filt += (E_raw - E_filt) * K   (K ≈ 1/32 per 100 ms, ~3 s time constant)
    limit |dE/dt| to 2 %/s          # tank blending cannot be faster
```

Fail-safe rule (asymmetric, as commercial systems do it): on FAULT keep the
last plausible **fuel** factor for 60 s then decay it toward the value stored
at the last key-on (or E0 if none), so a lean-out is impossible during a
transient dropout; but drop the **ignition** and **rail** blends to the
gasoline map immediately, because advance is the dangerous direction.
Power-up: start from the persisted E% (Phase 5); until then from E0.

### 3.3 Fuel factor
Stoichiometry: gasoline 14.7, ethanol 9.0 by mass. For volume fraction `v`
(the sensor reports volume %), with densities 0.745 (gasoline) and 0.789
(ethanol) kg/L, the mass fraction is `w = 0.789 v / (0.789 v + 0.745 (1-v))`,
the blend stoich AFR is `1 / (w/9.0 + (1-w)/14.7)`, and the required fuel
**mass** factor is `F_m = 14.7 / AFR_blend` (1.50 at E85, 1.63 at E100).
Injection **time** in MED9 is derived from fuel mass through the injector
constant (KRKATE in the FR; the 2.0T community still calls it KRKTE) and the
rail-pressure correction, so the factor is applied to the fuel mass, not to
time directly, unless the RE shows the injector constant is volumetric, in
which case divide by the density ratio (1.43 at E85). Field values quoted by
tuners (x1.35-1.50 at E85) span exactly this range, which is why the factor
is a **calibratable 1D curve over E%** (17 points, 1/1024 resolution),
initialised from the formula and trimmed with the wideband.

Insertion point: the multiplication where relative fuel mass `rk` becomes the
injection quantity/time for the bank (exact variable name to be confirmed
from the FR module). The hook multiplies by `F(E_filt)` in fixed point.
`F(0) = 1.000` exactly, so at E0 the patched ECU is bit-identical in behaviour.

Do not use the lambda adaptation (fra/frau/frao) as the correction path: the
35-50 % change exceeds its limits and sets DTCs, it cannot act in open-loop
phases (cranking, warm-up before sensor light-off, WOT enrichment) which is
where E85 goes lean, and it cannot inform ignition.

### 3.4 Ignition
Blend factor `f_zw(E)` from a 1D curve (0 at E0, 1 at about E40-50 where
MBT is usually reached, per prj) applied as
`zw = zw_gas + f_zw * dzw_E(nmot, rl)` where `dzw_E` is a new additive map
in our calibration block (start with +0..+2 deg, up to +6 at knock-limited
high-load cells, calibrated on the dyno/road with knock retard logging).
Do **not** shift `KFZWOP` (torque model optimum) or the knock control
references. Knock retard (`dwkrz`) staying at zero across blends is the
acceptance signal.

### 3.5 Start and warm-up
Ethanol needs roughly twice the cranking fuel around 10 C and barely ignites
below ~10 C without heating. Scale the start quantity and the afterstart /
warm-up enrichment by `f_st(E, tmot)` (2D, small) with generous values at low
`tmot`; this is the hardest part to calibrate (reports agree), so it comes
after the fuel factor is proven warm.

### 3.6 Rail pressure and injection window
E85 lengthens `ti` by 35-50 % at equal rail pressure. Raise the rail
pressure setpoint maps at high load by a blend on E% (within the HPFP's
capability, stock ~11 MPa; tuners run 12.5-13.5 on the 2.0T) and monitor
`ti` against the injection window. If the window is exceeded, limit torque
rather than let the ECU cut the throttle unexpectedly. Log HPFP duty and
setpoint-vs-actual before any WOT on E50+.

### 3.7 Diagnostics
Expose `E_filt`, `T_fuel`, `status/mode`, `F`, `f_zw` in a spare measuring
block (VCDS-readable) or via the DDLI logger, and later as OBD PID 0x52 if the
OBD handler is extended. No DTC is raised by the patch in the MVP; a fault
only switches the mode.

### 3.8 Persistence (Phase 5)
Store `E_filt` in EEPROM via the ECU's own EEPROM block handler or in
battery-backed RAM if the external SRAM is permanently powered (to be
verified), so a cold start after a battery disconnect uses the right cranking
fuel.

## 4. New calibration data

All new parameters live in one block inside 0x5E2510-0x5EFFFF (all 0xFF
today, covered by the 0x5E0000-0x5EFFFF checksum) with a header
(`FFCAL001`, version, length) so the tooling can find it:

| Name | Type | Init |
|---|---|---|
| `ff_can_id` | u16 | 0x0EC |
| `ff_timeout_ms`, `ff_hold_s`, `ff_filter_k`, `ff_slew` | u16 | 1000, 60, 32, 2 |
| `ff_F_curve[17]` | u16 x 1/1024 vs E% 0..100 step 6.25 | formula |
| `ff_fzw_curve[17]` | u8 x 1/256 | 0 .. 1 |
| `ff_dzw_map[8][8]` | s8 x 0.75 deg | 0 |
| `ff_fst_map[6][6]` | u16 x 1/1024 | 1.0 |
| `ff_prail_add[8]` | u8 x 0.1 MPa | 0 |

## 5. RAM

Needs about 64 bytes: frame copy, timestamps, `E_filt`, mode, factors,
counters. Allocation only after the RAM survey (static references +
RequestUpload snapshots across several ignition cycles) proves a range unused.

## 6. Verification plan

| Step | Where | Pass criterion |
|---|---|---|
| Unit tests of filter, fail-safe state machine, curve interpolation | emulator | matches a Python model bit for bit |
| Pico status paths | signal generator | correct status/E% for 40, 49, 50, 100, 150, 156, 185 Hz, no signal |
| RX slot proof | bench ECU + logger | frame bytes appear in the identified RAM buffer |
| E0 equivalence | bench then car | logs (lambda, ti, fra, zw) identical to stock within noise over the same drive cycle |
| Blend steps E20, E50, E85 | car, wideband | `fra/frau` within ±5 % of 1.0 after adaptation reset; no lean event at WOT; `dwkrz` unchanged; `ti` inside window; rail follows setpoint |
| Fault injection | car, idle then driving | unplug sensor: fuel factor held, ignition back to gasoline, no misfire; replug: recovers |
| Cold start series | car, over a season | start time and afterstart lambda at 20, 10, 0, -10 C on E85 comparable to gasoline |

## 7. RE targets with acceptance criteria

| Target | Done when |
|---|---|
| Powertrain TouCAN module and the RX slot -> RAM mapping for the table at 0x2BC90 | writing a frame to a free slot's id on the bench makes its bytes appear at a known RAM address |
| Periodic task table / hook point (100 ms) | a counter patch increments at 10 Hz |
| Fuel mass -> injection quantity multiplication (KRKATE path) and its variables (`rk`, `te`/`ti` per bank) | logged `ti` reproduces the decompiled formula for logged inputs |
| Lambda adaptation variables (`fra`, `frau`, `frao`, `rkat`) | found in the measuring-variable table and logged |
| `KFZW/KFZW2` blend and the final `zw` output; `dwkrz` | map addresses confirmed by xref and by a bench edit |
| Start/afterstart maps and `tmot` | same |
| Rail setpoint maps and `ti` window limits | same |
| `vkKraQu` presence (fuel-quality variant byte) | present/absent decided; if present, its consumers listed |
| EEPROM block handler | write path understood (Phase 5) |
| Security access algorithm (KWP 0x27) | logger authenticates |
