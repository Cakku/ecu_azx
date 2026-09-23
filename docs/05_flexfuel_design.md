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

#### Added 2026-09-15 (brief B2, issue #12) — slot mechanism resolved, VERIFIED-STATIC

Full derivation and evidence: `re/findings/can.md`. The slot mechanism is not
awkward; the fallback (hooking the RX dispatcher) is not needed.

How reception actually works: each `tbl_can_rx` slot owns one TouCAN message
buffer filtered on an exact 11-bit id (RXGMSK = 0xFFE00000 on all three
modules). `can_init_mb(slot)` at **0x135750** arms the buffer once at start-up;
a periodic task calls `can_rx_poll(slot)` at **0x4379C8**, which copies the 8
data bytes into `can_rx_shadow` and clears the IFLAG bit. There is no
interrupt and no "new data" flag in the driver: **the return value of
`can_rx_poll` is the new-data flag** — it is the received DLC (8 for our frame)
when a frame arrived since the previous call, and 0x40 when nothing arrived.

Steps to give 0x0EC a slot (target: slot 15, TouCAN **C**, message buffer 6):

1. Patch the id word at **0x2BD8C** from `00 00 07 FF` to `00 00 00 EC`
   (2 bytes change). Leave the rest of the row alone — the DLC byte is never
   read and word 0x2BD88 must stay 0x00000004 so the payload is not
   byte-swapped.
2. `python3 tools/checksum.py fix` — 0x2BD8C sits in the block with descriptor
   0x0A0010 (0x020000-0x02FFFF). Then `verify` must say ALL OK (65 blocks).
3. **No mask register changes.** RXGMSK/RX14MSK/RX15MSK stay as they are;
   0x0EC matches no other buffer on either module.
4. Call `can_init_mb(15)` once from init (`li r3,15; bl 0x135750`), e.g.
   appended to `can_rx_arm_all` at 0x12E570 or from `ff_rx`'s own init.
   Without this the buffer stays CODE = NOT ACTIVE and receives nothing.
5. `ff_rx` calls `can_rx_poll(15)` (`li r3,15; bl 0x4379C8`) every 10-100 ms
   and treats a return value of 8 as "fresh frame".
6. **The 8 data bytes appear at RAM 0x803F9C..0x803FA3** (symbol
   `can_rx_buf_spare0`), in wire order; the id is echoed at 0x803F98.
   The other spares: slot 16 -> 0x803FA8 (id word 0x2BD9C), slot 19 ->
   0x803FCC (0x2BDCC), slot 20 -> 0x803FD8 (0x2BDDC).
7. Timeout handling stays in `ff_rx` per §3.2 — the stock supervision
   (0x429D44 / 0x42A0A0) is per-slot calibrated and has no entry for a new
   slot.
8. No interrupt and no transmit collision: module C's IMASK only covers
   buffers 13-15, and every transmit object lives on module B buffers
   0x0B-0x0D.

**One open bench check before Phase 2 (#22).** All four spare slots are on
TouCAN module **C** (0x707800), while the ECU transmits only on module **B**
(0x707400). Both run at 500 kbit/s with identical timing and C is
receive-only, so the working assumption (HYPOTHESIS) is that B and C hang on
the same Antrieb-CAN pair. Confirm before wiring the Pico node: with the
engine running, check that signals fed by module-C frames (id 0x050 slot 10,
id 0x0C2 slot 9) *and* by module-B frames (id 0x1A0 slot 1) are all live.
If C turns out to be a separate bus, the node must be wired to that bus
instead — module B has no free message buffer.

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

#### Added 2026-09-16 (brief D1, issues #32/#37) — implemented in `patches/ff_fuel`

The whole of this section is now code: `patches/ff_fuel/src/ff_fuel.c`,
specified by `emu/models/flexfuel.py` and compared with it tick by tick in
`tests/test_ff_fuel_patch.py`. What was implemented, and where it deviates.

**Fixed point.**

| Quantity | Format | Note |
|---|---|---|
| `E_filt` | u16, **1/16 %**, 0..1600 | one curve breakpoint every 6.25 % = 100 counts, so `index = E_filt / 100`, `frac = E_filt % 100`, integer only |
| `E_frac` | u16, **1/1024 of one `E_filt` count** | **new, a deviation** — see below |
| `F` | u16, **1/1024**, clamped to [1024, 2048] **in code** | `rk = min((rk * F) >> 10, 0xFFFF)` |
| time | activations of the periodic hook; every calibration value is physical | see below |

**Deviation 1 — the sub-count `E_frac`.** §8 requires the 2 %/s slew limit to
be expressed per activation, which at 10 ms is 0.02 % = **0.32 counts of
1/16 %**. In integer arithmetic that truncates to zero and freezes the filter
completely. `E_filt` therefore carries a companion `E_frac`, and the pair is one
26-bit value in 1/16384 %. `E_filt` alone is what the curve, the logger and
everything else read, exactly as this section specifies.

**Deviation 2 — the filter gain is a time constant, not a shift.** This section
says `K = ff_filter_k` (1/32 per tick) and §8 corrects it to ≈1/320 at 10 ms.
FFCAL001 instead stores **`ff_filter_tau_ms` = 3000 ms**, and the patch computes
`K = ff_tick_ms / ff_filter_tau_ms` = 1/300 per activation. Likewise
`ff_slew_pct_s` stays 2 %/s and the patch computes the per-activation step.
`ff_tick_ms` (10 ms, from `scheduler.md` §11) is itself a calibration value, so
**moving the hook to another raster is one byte, not a rebuild**, and a
calibration written for a 100 ms raster still behaves as its author intended.
The conversion is in the C, not in `ffcal001.py`.

**Deviation 3 — `frame_bad` is a latch.** The conditions listed above ("status
in {fault, not ready}", "counter unchanged for 3 received frames") are
*conditions*, and a condition has to hold between frames too: the Pico sends at
10 Hz while the raster runs at 100 Hz, so nine activations out of ten see no
frame at all. Evaluating them only on the activation that carries the bad frame
made the mode fall straight back to OK on the next one. The rejection is
therefore latched in `ff_state.frame_bad` and cleared only by the next good
frame. (Found by the tick-by-tick comparison with the model, not by review.)

**Additions this section did not ask for, all in the safe direction:**

* the received id echo at 0x803F98 is compared against `ff_can_id`, so a frame
  arriving on the wrong slot cannot be believed;
* `E_raw > 100 %` is rejected as implausible;
* `ff_mode` 0 (off) and 2 (bench override) from §4, plus a fourth safe state:
  an FFCAL001 whose magic, version, length or checksum does not check out forces
  mode 0, i.e. `F = 1024` and no CAN traffic at all;
* `can_init_mb(15)` is called from the patch's own state-block initialisation
  (`can.md` §7 step 4), so `can_rx_arm_all` is not edited.

**Power-up is FAULT at E0 with F = 1024**, as specified; `e_key` (the decay
target) is 0 until brief D2 loads it from EEPROM block 8.

**The hold timer counts activations, including the one it is armed on**, so
`ff_hold_ticks` reads 5999 immediately after the FAULT entry and the held window
is exactly 6000 activations = 60.00 s.

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

#### Added 2026-09-15 (brief B6, issue #14) — the insertion point is resolved, VERIFIED-STATIC

Full derivation and evidence: `re/findings/injection.md`. Model and
regression test: `emu/models/injection.py`, `tests/test_injection_model.py`.

* **The injector constant is mass-based, not volumetric.** `KRKATE` (u16
  scalar, **0x5D3DBC = 3858**) is multiplied by `KLTIKRPR(dp)` (0x5C72F8),
  whose twelve points obey `value x sqrt(dp) = const` to 1 % — the Bernoulli
  orifice law, i.e. time per unit **mass** at a given pressure. So the factor
  of §3.3 is applied unchanged (1.50 at E85); **do not** divide by the
  density ratio.
* **Insertion point: `rk` at RAM 0x803038**, which has exactly one writer
  (`sth r6,0x3048(r13)` at 0x41ADD4, in `gk_rk`) and seven readers, all inside
  `rksplit` (0x41C3A0). Everything the conversion sees — homogeneous, both
  split modes and the start injection — is derived from that one cell.
* **Hook site: 0x42247C**, the `bl 0x41C3A0` (`4B FF 9F 25`) inside the
  engine-synchronous task `task_segment_a` (0x4223B0, ERCOSEK TCB 5, id 40,
  prio 0x0A). Its neighbours are argument-less `bl`s, so r3-r12 are dead
  exactly as at B1's 100 ms hook; the stub scales 0x803038 and ends with
  `b 0x41C3A0`. One word; then `tools/checksum.py fix`.
* **Fixed point:** `rk` is u16 with no implicit fraction, so
  `rk = min((rk * F_q10) >> 10, 0xFFFF)` and the §4 `ff_F_curve` in 1/1024 is
  the right format; `F = 1024` is bit-identical to stock (asserted by the
  test).
* **Bench alternative with no code at all:** `KRKATE` has exactly one
  reference in the whole image, so multiplying the scalar at 0x5D3DBC fuels a
  fixed blend for a first drive (E85: 3858 -> 5787), then `checksum.py fix`.
* **The `%UFRKTI` worry does not bind here.** Nothing outside the injection
  chain reads `rk` or `ti`; the monitoring-shaped function `FUN_00455C60`
  reads the **pre-`ZGST`** bank values 0x803030/0x803032/0x803034/0x80303A,
  which the hook is downstream of. The corollary is that after the patch the
  level-2 path no longer monitors the fuel that is actually injected.
* **The limit to watch is the injection window, not a `ti` maximum.** `rk2ti`
  clamps only from below (`TIMINP` = 900 at 0x5C7328). The ceiling is applied
  in the angle domain by `awea_ti_to_angle` (0x41B9C0) on
  `dwi = (ti * k_nmot) >> 13` at RAM **0x803088** — that is the signal to log
  per §3.6.

#### Added 2026-09-16 (brief D1, issue #32) — the curve is built, and "1.50 at E85" is a pump figure

`patches/ff_fuel/ffcal001.py` builds `ff_F_curve` from the formula above through
`emu.models.flexfuel.fuel_mass_factor`, so the calibration, the patch and the
model cannot drift apart. **F(0) = 1024 exactly**, which is what makes E0
bit-identical (`ffcal001.py` refuses to build a block whose first point is not
1024, or whose curve is not monotonic, or which exceeds the 2048 ceiling the
patch clamps to).

**Correction to the parenthesis "(1.50 at E85, 1.63 at E100)".** The E100 figure
is right — 14.7/9.0 = 1.6333 — but the formula in this section gives **1.5429**
at a volume fraction of 0.85, not 1.50. 1.500 is the value at **v = 0.78**,
which is exactly the "~75-81 %" that §2 quotes for what pumps sell as E85. The
two numbers are about different things: the curve is indexed by the **sensor's**
reading, which is the true volume fraction, so its point at 85 % must carry
1.5429 and a tank of pump "E85" will simply land the sensor near 78 % and the
factor near 1.50. Both are now asserted in
`tests/test_flexfuel_model.py::TestFuelMassFormula`.

Shipped curve (Q10): 1024, 1067, 1109, 1151, 1193, 1235, 1276, 1317, 1358,
1398, 1438, 1478, 1517, 1557, 1595, 1634, 1673.

The insertion point and the fixed point of the B6 note below are implemented
unchanged: one word at 0x42247C, `rk = min((rk * F_q10) >> 10, 0xFFFF)` on RAM
0x803038, and at F = 1024 the stub takes an early return and **does not write
`rk` at all** — 20 instructions per injection segment.

#### Hazard, added 2026-09-23 (brief G2, doc debt of #38/#41) — three tester adaptation channels trim the fuel under the flex factor, and a workshop reset moves them

VERIFIED-STATIC by F4. The evidence is in `re/findings/calibration_names.md`
§10.1 (the channel table, the service, the restore) and the `re/symbols.csv`
rows `kwp_adaptation_service` 0x038708, `adaptation_restore_all` 0x12E3F8 and
`adap_ch10_fgru` 0x7FD066. G2 moves the design consequence here; nothing is
newly derived.

The KWP "Anpassung" service `kwp_adaptation_service` (0x038708) exposes
**twelve tester-writable channels**. Each is clamped between two calibration
bytes, committed to **EEP_CONF block 8** (sub-function 0x83) and restored
from it at every power-up by `adaptation_restore_all` (0x12E3F8). Three of
them multiply the fuel **upstream of the `rk` hook**, so the flex factor
`F(E)` multiplies whatever they hold:

| channel | RAM | multiplies | limits → factor | default |
|---|---|---|---|---|
| **4** | 0x7FD065 | the running mixture, in `mixture_running_build` 0x419DA4 | 64…141 → **0.50 … 1.10** | 128 = 1.0 |
| **8** | 0x7FD067 | `ksta`, the start quantity (0x41A5EC, 0x41A780) | 64…141 → **0.50 … 1.10** | 128 = 1.0 |
| **10** | 0x7FD066 | `fgru_trim`, a factor on `rk` in `gk_rk` | 26…179 → **0.797 … 1.094** | 128 = 1.0 |

(Channel 5, 0x7FD069, 64…141, also feeds the 0x7FD264 mixture factor per the
same table. F4 did not count it as a fuel trim, and neither does this note.)

**The hazard.** Sub-function 0x82 with channel 0 **restores every channel to
its default**. A workshop "basic setting" or adaptation reset therefore puts
all three back to 128 without any warning to the driver. If a flex-fuel
calibration was trimmed on the wideband with any of them away from 128, the
reset moves the mixture out from under `F(E)` by up to the table's range
(−50 % … +10 % on the running mixture). The E85 fuel curve then carries an
error nobody put into FFCAL001. The reverse also happens: a workshop that
*sets* a channel (to fix a lean code on petrol, say) changes an E85 tune it
knows nothing about. For the flex strategy this means:

* **Calibrate with all three at 128**, and read them (sub-function 0x81)
  before trimming `ff_F_curve` on the wideband. Record the values with the
  calibration.
* **After any workshop visit, read them again** before blaming the sensor or
  the curve for a mixture shift. A shift that is the same at every blend
  points here, not at `F(E)`.
* They are *not* a flex-fuel lever. The ±10 % upward range is far short of
  E85's +54 %, and `F(E)` stays the only fuel correction that follows the blend.

> **Caveat (2026-09-23, G2) — HYPOTHESIS, from G4.** F4's §10.2 concluded that
> there is no stock enrichment on the fuel path and that these channels are
> "the only stock lever". G4 (`calibration_names.md` §11.8) found that
> `gk_rk` also divides `rk` by 0x80304A, very probably the lambda setpoint
> `lamsbg_w` built by `eta_coordinator` 0x442C18, whenever 0x7FEA33 is set.
> So a stock λ < 1 enrichment path may exist. Whether any calibrated input
> ever asks for it is **not traced**. Until it is, do not read "no stock
> enrichment to lean out" as settled in this document.

The persistence side of the same block (the ethanol store shares EEP_CONF
block 8 with these channels) is in §3.8, note of 2026-09-23.

### 3.4 Ignition
Blend factor `f_zw(E)` from a 1D curve (0 at E0, 1 at about E40-50 where
MBT is usually reached, per prj) applied as
`zw = zw_gas + f_zw * dzw_E(nmot, rl)` where `dzw_E` is a new additive map
in our calibration block (start with +0..+2 deg, up to +6 at knock-limited
high-load cells, calibrated on the dyno/road with knock retard logging).
Do **not** shift `KFZWOP` (torque model optimum) or the knock control
references. Knock retard (`dwkrz`) staying at zero across blends is the
acceptance signal.

#### Added 2026-09-15 (brief B7, issue #15) — chain resolved, VERIFIED-STATIC

Full derivation and evidence: `re/findings/ignition.md`.

* **There is no `KFZW/KFZW2` blend in our software.** The FR's double-map
  interpolation by the inlet-cam factor (`SY_NWS`) and the `KFZWLB1/2` swirl
  pair are compiled out; the dataset has exactly one base map, **`KFZW` at
  0x5C75FE** (16 nmot rows x 12 rl cols, s8), read by `zwgru_kfzw_lookup`
  (0x41D334) through the axis blocks 0x5C7736 (nmot) and 0x5C7758 (rl). The
  "blend two maps the way the ECU already does" pattern of §1 therefore has
  no vehicle here — the offset must be an **added term**.
* **Insertion point: the word at 0x41D40C** inside `zwgru_build`
  (`FUN_0041d38c`, 0x41D38C), replacing `add r3,r3,r10` with a `bl` to a
  patch that re-does that add and then adds the offset. It sits after the
  base map and all base deltas and before the bank offsets, the **knock
  retard** (`zwbas_per_bank`, 0x41D10C) and `zwmin` / the -54..+58.5 ° output
  clamp (`zwout_select`, 0x41D464), so knock control and every limit still
  act on top. Context: **ERCOSEK task id 41** (TCB entry 6 at 0x47870C,
  entry point 0x4224BC). Only `r3` and `r10` are live at that word; LR is
  already saved on the caller's stack.
* **Format**: the whole chain is **s8 at 0.75 °CA per LSB** — proven by the
  output driver `zw_output_driver` (0x41CD9C), which multiplies by 15/2 to
  hand the TPU stage 0.1 ° units. So +1 ° = +1.333 counts; calibrate the
  offset in 0.75 ° steps (the §3.4 range +0..+6 ° is +0..+8 counts).
* **`KFZWOP` is at 0x5CA3F1** (16 nmot x 11 rl, s8, axes 0x5CA3D6 / 0x5CA3E6),
  read by `kfzwop_lookup` (0x436B90) and summed into `zwopt` (0x802665) by
  `zwopt_build` (0x423344). It is **not** shifted, as this section requires.
  Useful side effect: `KFZWOP - KFZW` is the ECU's own estimate of the
  advance the base map gives up to knock, and it is 1.5 ° at 1800 rpm / 35 %
  rising to ~9 ° at 4500 rpm / 90 % — a physical upper bound for `dzw_E`.
* **`dwkrz`** (the acceptance signal) is the six-byte array at
  **0x7FCE57-0x7FCE5C**, written by `krreg_knock_retard` (0x416D6C) and shown
  in VCDS groups 020-024. There is also a stock "bad fuel" detector
  (`zwgru_low_octane_detect`, 0x0F436C) that latches on the *mean* retard and
  applies `KFDZK` (0x5D597E); with E85 it must never latch, so its latch bits
  0x7FD31B bits 0/1 are a second acceptance signal.

#### Added 2026-09-17 (brief E1, issue #34) — implemented in `patches/ff_fuel`

This section is now code: `patches/ff_fuel/src/ff_ign.c` (the producer) and
the hand-written `ff_zw_hook` in `patches/ff_fuel/src/hooks.S` (the consumer),
specified by `emu/models/flexfuel.py` and compared with it tick by tick in
`tests/test_ff_ign_patch.py` (51 tests). The bench and road procedure is
`patches/ff_fuel/test/procedure_e1.md`.

**Formats, all decided by the chain this sits in.**

| Quantity | Format | Where |
|---|---|---|
| `f_zw(E)` | u8, **1/256**, 17 points over E 0..100 step 6.25 % | `ff_fzw_curve`, FFCAL001 +0x42 |
| `dzw_E(nmot, rl)` | **8 × 8 s8, 0.75 °CA per count** | `ff_dzw_map`, FFCAL001 +0x54 |
| its axes | 8 × u16 in `nmot_w` / `rl_w` units | FFCAL001 +0xE8 / +0xF8 |
| `dzw_e` (the result) | **s8, 0.75 °CA**, positive = advance | `ff_state` +0x29 |
| the ceiling | `ff_dzw_max` = 8 counts = 6.00 °CA, code-clamped to 16 | FFCAL001 +0xE7 |

`dzw_e = clamp(round(f_zw × dzw_E / 256), ±min(ff_dzw_max, 16))`, computed in
the 10 ms tick and consumed by ten instructions at 0x41D40C.

**The axes are the stock ones.** `ff_dzw_nmot_axis` is every other breakpoint
of `KFZW`'s nmot axis 0x5C7736 and `ff_dzw_rl_axis` is eight of the twelve of
its rl axis 0x5C7758, so a cell lines up with a `KFZW` row and column and the
`KFZWOP - KFZW` budget can be read against it directly. This section proposed
"+0..+2 °, up to +6 at knock-limited high-load cells"; evaluated on that grid
the ECU's own budget is **0 or negative in 26 of the 64 cells** (at low load
`KFZW` is already at or past the modelled optimum, so there is nothing to win
there) and 8-14 counts at 72-104 % load above 2900 rpm. The table is in
`procedure_e1.md` §B4.

**Deviation 1 — `f_zw` saturates at 255/256, not at 1.** The declared format
is u8 × 1/256, and 256 is not a u8. The shipped curve is a ramp from 0 at E0
to **255 at E50**, flat above; 255/256 = 0.996, i.e. 0.4 % low, which at whole
0.75 ° counts is 0.003 counts and disappears in the rounding.

**Deviation 2 — the map is read with `rl_w` (0x7FEFB2), not 0x7FED38.** Brief
E1's text named 0x7FED38, which `re/symbols.csv` calls `rl_for_fuel`: the
relative charge `gk_rk` uses, *selected from* `rl_w` at 0x418A3C
(`start.md` §3.3). `rl_w` **is** the KFZW column-axis input (`lhz -0x103E(r13)`
at 0x41D358, `ignition.md` §2), and aligning `ff_dzw_map`'s columns with
`KFZW`'s is the whole reason for reusing its breakpoints. Both cells are in
`logging/sessions/ff_fuel.json` so a log can tell them apart.

**Deviation 3 — no new RAM, and the offset lives in the checksummed core.**
`dzw_e` and `fzw_q8` took the two fields D1 reserved at `ff_state` +0x29 and
+0x2A. They are *control* values written only by the periodic tick, so the
core is where they belong: a corrupted `dzw_e` makes the next activation
re-initialise the block rather than leaving a stale advance in the ignition
path. The consumer checks only the block's magic, exactly as `ff_rk_scale`
does; what bounds it is the producing clamp, the stock s8 clamp at 0x41D410
and the -54..+58.5 ° output clamp.

**The #37 rule is structural, not a branch.** `ff_zw_update()` is called from
`ff_finish()`, which runs on *every* path out of `ff_tick()`, so the activation
on which the mode leaves OK/HOLD/OVERRIDE is already the activation on which
`dzw_e` is 0 — no hold, no ramp, while the fuel factor keeps its 60 s hold in
the same activation. HOLD keeps computing from the frozen `e_filt`, so the
offset freezes rather than dropping; that distinction is step 6 of the fault
matrix in `procedure_e1.md` §B2.

**It ships disabled, three times over:** `ff_zw_enable` = 0, `ff_dzw_map` all
zero, and `f_zw(E0)` = 0 (`ffcal001.py` refuses a curve that does not start at
0). `tests/test_ff_ign_patch.py` runs task 41's `bl 0x41D38C` to completion on
the stock and the patched image, with the feature off and with it on at the
neutral map, and finds `zwgru` bit-identical and no SRAM byte moved outside
the patch's own block.

**Diagnostics: VCDS measuring block 108** (TKMWL ids 2192-2195) — `f_zw` in %,
`dzw_e` in °CA, the worst of the six `dwkrz` bytes, and `0x7FD31B & 3`. The
last two are the two acceptance signals this section names, so the whole
calibration criterion is readable in one group.

**Still open (the road half of #34):** every cell of `ff_dzw_map` is 0 and only
a car with real fuel can fill them in. `procedure_e1.md` §B3 is the recipe.

> **2026-09-23 — brief G2 (doc debt of #34): the ethanol advance shares its
> budget with a stock term.** VERIFIED-STATIC by F4 (`re/findings/calibration_names.md`
> §10.5). Moved into `re/findings/ignition.md` §14, which has the evidence;
> nothing here is new. `zwdelta_load` (RAM **0x7FD338**, s8, 0.75 °CA/LSB,
> written by `FUN_00459334`) is added in `zwbas_per_bank` 0x41D10C at
> 0x41D120. That is after `zwgru` (and so after `dzw_e`) and before the knock
> retard and the ZWMIN/ZWOUT clamp. It is `zwdelta_7FD338_weight_map` 0x5D5F81
> (0 below 47 % charge) × `zwdelta_7FD338_map` 0x5D5FFB (−6.0 … +2.25 °CA over
> nmot and `tans`) + `zwdelta_7FD338_add_map` 0x5D6075 (−3.75 … +7.5 °CA over
> `tmot_filt` and rl, **largest cold at load**).
> The `KFZWOP − KFZW` headroom this section and `procedure_e1.md` §B4 read
> against `ff_dzw_map` **does not include this term**. On a cold engine at
> load, part of that headroom is already used before the ethanol offset is
> added. Calibrate `ff_dzw_map` warm, read the headroom minus the live
> 0x7FD338 (DDLI, `logging/sessions/tuning_checklist.json`), and keep
> `dwkrz` / 0x7FD31B & 3 as the acceptance signals through the warm-up too.

### 3.5 Start and warm-up
Ethanol needs roughly twice the cranking fuel around 10 C and barely ignites
below ~10 C without heating. Scale the start quantity and the afterstart /
warm-up enrichment by `f_st(E, tmot)` (2D, small) with generous values at low
`tmot`; this is the hardest part to calibrate (reports agree), so it comes
after the fuel factor is proven warm.

#### Added 2026-09-15 (brief B8, issue #16) — the start path is resolved, and the "afterstart / warm-up enrichment" of this section does not exist as a fuel factor

Full derivation and evidence: `re/findings/start.md`. Model and bit-exact
emulator check: `emu/start_model.py`, `tests/test_start_model.py` (14 tests).

* **Cranking (VERIFIED-DYNAMIC).** `%ESSTT` is `FUN_0041a268` (0x41A268) with
  the high-pressure twin `FUN_0041a690`. It publishes **`ksta * kstaa` at RAM
  0x80302C, u16 with 1024 = 1.0**, which `gk_rk` (0x41AA48) multiplies at
  0x41AA88 while the start has not ended; outside the start the module forces
  it to exactly 0x400. Maps: **`KFKSTT` 0x5C6E24** (12 `tmst` x 2 `prist`,
  u16, 24.8x at -32 °C down to 1.85x at +100 °C), **`KFWKSTT` 0x5C6C7C**
  (12 `tmst` x 14 injections-since-start, u8 128 = 1.0 — this *is* the
  after-start decay, and it is over injections, not seconds), **`KFWKSTN`
  0x5C6C50** (off, all 255).
  **Insertion point S1 for `f_st(E, tmst)`: the `sth` at 0x41A680 and
  0x41A808**, Q10, saturate 0xFFFF; 0x400 = 1.0 is bit-identical at E0.
* **`tmst` is RAM 0x8021F6**, the coolant temperature latched at start;
  `tmot` is RAM 0x8021EF (A3's `cand_mw_tmot`, confirmed) and `tmot_w`
  0x802228. **Scaling for both: 0.75 °C per LSB, offset -48 °C**, proved by
  the start-map axes. So the `tmot` axis of the new 2-D `f_st(E, tmst)` map
  should reuse the `KFWKSTT` breakpoints
  (-30, -24.75, -20.25, -15, -6.75, 0, 15, 20.25, 27.75, 39.75, 60, 90 °C).
* **Correction to this section: there is no separate after-start (`fnsk`) or
  warm-up (`fwlk`) factor on the fuel path.** Every multiplicative term on
  `rk` is accounted for (`re/findings/start.md` §4.1); once the start has
  ended the mixture comes from the torque/λ cascade (0x803020, Q12) and the
  ECU holds λ = 1 instead of enriching, heating the catalyst with ignition
  retard. So "scale the start quantity *and* the afterstart / warm-up
  enrichment" becomes: scale the start quantity at S1, and — if a warm-running
  ethanol correction is still wanted — use B6's `rk` hook at 0x42247C gated on
  `tmst`, not a second enrichment map.
* **Start ignition (VERIFIED-DYNAMIC).** While `B_stend` is clear the whole
  per-bank angle is replaced by **`zwstt` at RAM 0x802096** (s8, 0.75 °CA per
  LSB) in `zwbas_per_bank` (0x41D10C) — `zwgru`, the bank offsets *and the
  knock retard* are bypassed. `zwstt` is built by **`FUN_00431294`
  (0x431294)** from **`KFZWSTT` 0x5C7B64** (8 ignitions-since-start x 8
  `tmst`). **Insertion point Z1: the `stb` at 0x431384.** Because knock
  control is inactive here, keep any ethanol advance small (+2…+4 °, i.e.
  +3…+5 counts) and restrict it to `tmst` below about 40 °C.
* The engine-state variables the patch needs: `B_st` 0x7FE91D, start end
  0x7FE920, **`B_stend` 0x7FE921** (and its segment-task copy 0x7FECCA),
  "engine not running" 0x7FEAD0, after-start timer 0x8011D8 (u16),
  injections-since-start 0x7FD269, ignitions-since-start 0x7FCE14.

#### Added 2026-09-17 (brief E2, issue #35) — implemented in `patches/ff_fuel`

Full derivation of the sites, their tasks and their register liveness:
`re/findings/start.md` §9. Implementation, limits and costs:
`patches/ff_fuel/README.md`. Bench and calibration recipe:
`patches/ff_fuel/test/procedure_e2.md`. Tests:
`tests/test_ff_start_patch.py` (61).

* **Both levers of this section are taken.** `f_st(E, tmst)` scales
  `ksta_adapted` 0x80302C at **0x41A680** *and* **0x41A808** (the low- and
  high-pressure `%ESSTT` twins publish it from **different registers**, r31 and
  r3), and an ethanol advance is added to `zwstt` 0x802096 at **0x431384**.
  All three words are in the **on-chip** flash, which takes the patch to six
  on-chip words out of seven.
* **The producer is the same 10 ms tick**, called from `ff_finish()` so it runs
  on every path out of `ff_tick()`. It writes two new fields in the state
  block, `fst_q10` (u16 Q10) and `zwst_add` (s8 counts of 0.75 °CA).
* **Correction to this section's "afterstart / warm-up enrichment".** The 2015
  B8 note already said there is no `fnsk`/`fwlk` factor; E2 adds that there is
  no need for one either. `f_st` multiplies the *cranking* quantity, and the
  stock `KFWKSTT` decay over injections-since-start is what carries it into the
  after-start — so the ethanol correction decays with the stock map instead of
  needing a second one.
* **`ff_fst_map` is now live.** 6 ethanol rows × 6 `tmst` columns, Q10, shipped
  all 1024. Its `tmst` axis is six of the twelve `KFWKSTT` breakpoints
  (−30, −15, 0, +20.25, +39.75, +90 °C) so every cell lines up with a stock
  row, and its E axis is 0, 20, 40, 60, **85**, 100 %. **Row 0 is the E0 row
  and `ffcal001.py` refuses to build a block whose row 0 is not exactly 1024** —
  the start counterpart of `F(0) = 1024`.
* **The #37 asymmetry, both halves in one function.** `fst_q10` is computed
  wherever `ff_tick()` computes `f_q10` from `e_filt` (OK, HOLD, **FAULT**,
  OVERRIDE), so it inherits the 60 s hold and the decay with no rule of its
  own; `zwst_add` is **0 on the activation the mode leaves OK/HOLD/OVERRIDE**,
  and additionally 0 at and above `ff_zwst_tmax`. The start is where that
  asymmetry matters most, because `zwbas_per_bank` **bypasses the knock
  retard** while `B_stend` is clear — nothing downstream takes a stale advance
  back.
* **A correction to `start.md` §3/§7 that changed the code.** The low-pressure
  `%ESSTT` does **not** return on its `B_stend` early-out: it sets r31 = 0x400
  and branches to 0x41A680, the hooked store. So that word runs on every
  activation of the segment task for as long as the engine runs. **Both S1
  stubs therefore test `B_stend` 0x7FE921 themselves** and take the untouched
  path when it is set; without that gate the patch would have multiplied the
  ECU's explicit "no start enrichment" by `f_st` at every operating point.
  `start.md` §9.3 has the five instructions.
* **Four new values in VCDS measuring block 69** (ids 2188-2191): `f_st` as a
  percent, the applied advance in °CA, `tmst` in whole °C, and `ksta_adapted`
  as a **raw count** — a percent byte would saturate at the stock 22.8× alone
  (`measuring_vars.md` §8.5).
* Both features ship **disabled** with neutral tables, so the flashable file
  still behaves exactly like the E1 file.

### 3.6 Rail pressure and injection window
E85 lengthens `ti` by 35-50 % at equal rail pressure. Raise the rail
pressure setpoint maps at high load by a blend on E% (within the HPFP's
capability, stock ~11 MPa; tuners run 12.5-13.5 on the 2.0T) and monitor
`ti` against the injection window. If the window is exceeded, limit torque
rather than let the ECU cut the throttle unexpectedly. Log HPFP duty and
setpoint-vs-actual before any WOT on E50+.

#### Added 2026-09-15 (brief B9, issue #17) — both halves resolved, with two corrections

Full derivation and evidence: `re/findings/rail.md`.

**Correction 1 — the pressure scale. `1 LSB = 0.005 bar`, and the stock
ceiling is 110 bar, not "~11 MPa capability".** `KLPRMAX` (the six u16 at
**0x5D5546**, all **22000**) is the hard setpoint ceiling and equals exactly
110.0 bar; the map the engine actually runs on, **`KFPRSOLHOM` at 0x5D5324**
(8x8 u16, rows = nmot axis 0x5D558A, columns = load axis 0x5D5578), goes
**35 bar at idle to 95 bar at high speed and load**. So there is
**+15 bar of headroom inside the stock ceiling**, which is `sqrt(1.158)` =
**7.6 % more injector flow** — useful for atomisation and duty, but nowhere
near E85's +40 % fuel demand. The rail raise supports the fuel factor of §3.2;
it cannot replace it. B6's 0.01 bar/LSB hypothesis is refuted (it would put
the stock map at 190 bar).

**Correction 2 — the ECU does *not* cut the throttle when the injection
window is exceeded.** `awea_ti_to_angle` (0x41B9C0) compares
`wbho1s (0x80307E) - dwi (0x803088)` against `0x7FD290 * 32`
(**67 counts = 50.25 degCA**, a flat curve in this dataset) and, when the
injection does not fit, **advances the start of injection** to
`dwi + 50.25 degCA`, capped at 360.0 degCA. It sets **no flag**, raises **no
DTC**, and removes **no fuel** — and it saturates silently at the cap. The one
torque-domain path that exists (`0x803070` -> `0x80235A`, VCDS measuring id
2051, -> `0x80360E` -> throttle) is armed **only** by the fuel-system fault
bit `0x80201E & 0x20`, i.e. it is a limp-home limit for a broken rail-pressure
sensor, not a window limiter. So the §3.6 requirement "limit torque rather
than let the ECU cut the throttle" is about a behaviour that does not exist;
if we want that limit, we have to add it. The clean place is the existing
min-chain at **0x0C7CF8**, which already reduces `0x80235A` from `0x803070`
and propagates to the throttle exactly like every stock protection limit.

**Correction 2b — and even that clamp is disarmed on a healthy rail.**
`0x7FEA48 = (prist > PRWBHMX 0x5D3CDC = 2600 = 13.0 bar)`, and it gates all
three interventions: the `awea` angle clamp, the charge limit, and a **hard
injection cut-off angle** that `esausg_output` (0x409834) otherwise programs
into the injector TPU driver at `cylinder reference - 50.25 degCA`. Since
`KFPRSOLHOM` never asks for less than 35 bar, **in normal running none of it
is armed: the injection window is enforced by calibration only.** The flip
side is the failure mode to design against — if a raised setpoint plus E85's
higher volume demand ever lets `prist` fall below 13 bar, all three arm at
once and the driver gets a simultaneous fuel cut and torque drop. **The
acceptance signal for any rail raise is therefore `prist` staying above
13 bar, and the early warning is `0x80316E` pinned at `VMSVMX` = 5000.**
(`re/findings/rail.md` §14.)

**What the numbers say about the window.** `ti` is **1 us per LSB** (proved in
`rail.md` §8 from `k_nmot = (nmot_w * 34360) >> 16` and the 3/128 degCA angle
LSB — this also closes the first open item of `re/findings/injection.md` §11).
At the worst `KFWBHO1SW` cell the hard clamp sits at **7.8 ms of `ti` at
6000 min^-1**, about **+73 %** over an assumed 4.5 ms stock WOT injection.
**E85 (+40 %) therefore has roughly 20 % reserve against the hard clamp**;
E100 plus a WOT enrichment does reach it. What degrades first is not the clamp
but mixture preparation, because the start of injection is pushed towards and
past intake-valve opening.

**What to raise, and what will actually stop us.**

* **Raise `KFPRSOLHOM` (0x5D5324)** towards 22000 in the high-load rows. For an
  E-blend-dependent raise, the code-free hook is **`KFPRSOLOFF` (0x5D5424)**,
  an additive 8x8 map (0..5000 = 0..+25 bar) that is **already summed into the
  homogeneous path** (`CWPRSOL` 0x5D521E bit 5 is set) and weighted by the
  6-point curve 0x5D5559; re-pointing that weight curve at E% costs one `lbz`.
* **Going above 110 bar needs `KLPRMAX` (0x5D5546) raised too**, and should not
  be attempted blind: the sensor curve (0x5D518A) saturates near 138 bar, and
  the pump will not follow (below).
* **The binding constraint is the pump, not the map.** The setpoint is
  **rate-limited by the spare pump volume**: `0x8031F6 = (VHDPMX 0x5D559C =
  25120 - vhdp_dem 0x803212) * 0.1` sets how fast `prsoll` may climb, and
  `%AMSV` clamps the delivery request at **`VMSVMX` 0x5D4BC6 = 5000**
  (`0x80316E`). **`0x80316E` pinned at 5000 means the pump is saturated and no
  map change will help** — log it before and after any E-blend run.

**Log list (replaces "HPFP duty and setpoint-vs-actual"):** `prist` 0x8031DA
(VCDS id 500), `prsoll` 0x8031F4 (id 501), `prdiff` 0x8031CA (id 1691),
`0x80316E` (MSV volume after the limit), `0x8031F6` (spare pump volume),
`0x80235A` (id 2051, the charge limit that carries the window term), and —
via the DDLI logger, since neither has a measuring id — **`dwi` 0x803088** and
**`wbho1s` 0x80307E**, whose margin is `0x80307E - 0x803088 - 2144`.

#### Added 2026-09-17 (brief E5, issue #36) — implemented in `patches/ff_fuel`

The **trimmed** half of #36: an E-dependent setpoint adder *inside* the stock
ceiling, plus the diagnostics that say whether the pump follows. The torque
limiter this section used to assume and any `KLPRMAX` raise are **out of
scope** until bench data exist (`re/findings/rail.md` §12.3, §14.4); the
design note for the limiter is `patches/ff_fuel/test/procedure_e5.md` §6.

```
10 ms tick            ff_rail_update()  in src/ff_rail.c, from ff_finish()
                        prail_add = clamp(interp17(ff_prail_curve, e_filt),
                                          0, min(ff_prail_max, 6000))
                      plus, unconditionally, the three window statistics

per 20 ms activation  ff_prail_hook  in src/hooks.S, 12 instructions
                        prsoll_raw = min(map_output + prail_add, 0xFFFF)
```

**Where it lands, and why that is the whole safety argument.** The hook is the
`sth r30,0x3200(r13)` at **0x45845C**, the single store of `prsoll_raw`
0x8031F0 inside `hdrpsol_main` — after the six-map bank of `rail.md` §3.2 and
**before** the `PRSOLMN` floor (7000 = 35.0 bar), the **`KLPRMAX` ceiling
(22000 = 110.0 bar)** and the pump-volume rate limiter of §3.3-§3.4. All three
still bind, because the clamp *re-reads the cell from RAM* four instructions
later (`lhz r30,0x3200(r13)` at 0x458480) instead of re-using the register the
store came from. **No calibration of this patch can put more pressure in the
rail than the stock ECU already allows itself** — which is what makes a code
hook acceptable here at all.

All six map-selection paths reach that word; the one path that must not be
touched — `0x7FD04D & 1`, "hold the previous setpoint" — branches *past* it
(`bne 0x458460`), so unlike E2's 0x41A680 the stub needs no gate.
`rail.md` §12.1 has the disassembly, the dead-register set and the proof that
r30 is a clean halfword on every path in.

**What it is for.** `KFPRSOLHOM` tops out at 19000 = 95 bar, so the headroom
inside the ceiling is **+15 bar** = +15.8 % pressure = **7.6 % more flow** at
the same `ti`. That is a mixture-preparation and injector-duty measure and
nothing more: E85's +40 % fuel **mass** comes from `rk` and the F curve of
§3.3 (`rail.md` §12.1). `ff_prail_max` therefore ships at exactly 3000 = the
whole headroom, and the code ceiling `FF_PRAIL_HARD_MAX` is 6000 = 30.0 bar.

**The #37 rule, rail flavour.** `prail_add` is **0 on the activation the mode
leaves OK/HOLD/OVERRIDE** — no hold, no ramp, the same side of the line as the
ignition blend and the start advance. §3.4's sentence now covers three
features: a stale-rich mixture is safe, a stale *advance* or a stale
*pressure demand* is not.

**The diagnostics are the other half of the brief, and they run even with the
adder disabled**, because they observe stock cells:

| Field | What | Why |
|---|---|---|
| `win_margin_min` | `wbho1s − dwi − 0x7FD290 × 32`, the worst of the window | **`dwi` and `wbho1s` have no stock measuring id at all** (`rail.md` §11), so before E5 the injection window could not be watched on a car |
| `prist_min` | the worst `prist` of the window | below `PRWBHMX` = 2600 = **13.0 bar** the driver cut-off, the injection-angle clamp and the fault charge limit arm **together** (`rail.md` §14.3) |
| `msv_sat_ticks` | activations with `0x80316E` at `VMSVMX` | "the pump is out of volume", the early warning of §12.2 |

They are a **tumbling** window of `ff_diag_window_ms` (1000 ms) with continuous
publication, not a sliding minimum: a sliding one needs a ring buffer of up to
6553 samples and patch code does not get to allocate that. `diag_ticks` is in
the state block so a logger can see where in the window a sample sits. All
three, plus the adder, are **VCDS measuring block 109** (`21 6D`), ids
2184-2187, with the cross-checked pressure formula 0x53 for the two bar fields
(`re/findings/measuring_vars.md` §7.3, §8.6).

The required margin is read from the RAM cell `awea_angles` writes
(**0x7FD290**), not from the literal 2144 above: 2144 is 67 × 32 and 67 is
what `KLWBHO1SMX` happens to hold in *this* dataset.

### 3.7 Diagnostics
Expose `E_filt`, `T_fuel`, `status/mode`, `F`, `f_zw` in a spare measuring
block (VCDS-readable) or via the DDLI logger, and later as OBD PID 0x52 if the
OBD handler is extended. No DTC is raised by the patch in the MVP; a fault
only switches the mode.

> **2026-09-16 — brief D2, issue #39: implemented, and the slots are named.**
> `patches/ff_fuel` publishes four values in **VCDS measuring block 111**
> (`21 6F` over KWP). Evidence for every number:
> `re/findings/measuring_vars.md` §8, reproducible with
> `python3 tools/measuring_vars.py data/passat_azx_ori.bin --free`.
>
> | Field | Value | Measuring id | Formula | Reads |
> |---|---|---|---|---|
> | 1 | `E_filt`, whole % | **2196** | 0x21, A = 100 -> the value is B | `ff_diag_e_pct` |
> | 2 | `F`, % (100 = 1.000) | **2197** | 0x21, A = 100 | `ff_diag_f_pct` |
> | 3 | `T_fuel`, degC | **2198** | 0x05, A = 10 -> `B - 100` (CROSS-CHECKED, §7.1) | `ff_diag_t_degc` |
> | 4 | `256 * persist_state + mode` | **2199** | 0x36, a plain count | `ff_state.mode` |
>
> The ids are the **last four entries of `tbl_measuring_vars`** (0x0A78A8, one
> contiguous 16-byte edit); all four point at the "not available" stub today
> and no group names them. Group 111 and its `+0x7F` echo 238 are both empty,
> so the whole 25-byte answer to `21 6F` belongs to the patch. Groups **108**
> and **109** are equally free and are left for the ignition (§3.4) and rail
> (§3.6) blends.
>
> `f_zw` is **not** published: it does not exist yet. A handler that reads a
> reserved zero would be a field that lies. When §3.4 lands, take group 108.
>
> Still true: **no DTC is raised.** A fault only switches the mode, and the
> mode is field 4. OBD PID 0x52 is untouched.

> **2026-09-22 — brief F6, issue #39: the OBD half is located and *deferred*,
> not implemented.** Full evidence: `re/findings/obd.md`; regression cover:
> `tests/test_ff_obd_patch.py` (13 tests). Nothing in `patches/ff_fuel/**`
> changed and FFCAL001 stays at **v4 / 332 B**.
>
> The generic-OBD stack **is** in this image. J1979 modes 0x01-0x09 are nine
> more entries of the same 28-entry dispatch table at 0x2B820 that `kwp.md` §1
> describes, gated to internal sessions 4 and 6. Mode 01 is `obd_mode01_h1`
> (0x5D0F4); a PID needs a byte in the **dense** class table at 0x0A39B4
> (index = PID) **and** an entry in one of five (record-pointer, PID,
> support-mask) lists in calibration 0x5C5D24-0x5C5E1B.
>
> **All five lists are exactly full** — 20 + 8 + 3 + 6 + 4 = 41 entries, every
> slot used, two slack bytes in the whole block — and each loop bound is an
> immediate in stock code. So PID 0x52 is **not** the "table word + handler"
> shape D2 used for the TKMWL: the cheapest addition is 7 instruction words
> plus a relocated list, which brief F6 rules out for an optional feature.
> Emulated control in `tests/test_ff_obd_patch.py`: the class byte alone
> changes nothing, class byte **plus** a list slot makes the firmware answer
> `41 52 85` with no instruction changed.
>
> Two things this settles for whoever picks it up:
>
> * **The support bitmaps are RAM** (0x801215-0x801220), rebuilt by
>   `obd_pid_support_build` (0x5CBE8) from each record's *validity byte*. They
>   are not flash constants, so `ff_pid52_enable` would gate both the answer
>   and the advertised bit through **one RAM byte** — no calibration edit to
>   switch the PID on or off, and with the byte at 0 the image behaves exactly
>   as stock.
> * **The #39 exit criterion does not depend on this.** Measuring block 111
>   already publishes `E_filt`; PID 0x52 stays the optional half.

> **2026-09-23 — brief G1, issue #39: PID 0x52 is implemented, run-time
> gated.** The diagnostic is now **measuring block 111 _and_ OBD mode 01 PID
> 0x52**. Evidence: `re/findings/obd.md` §9; patch side:
> `patches/ff_fuel/README.md` "Stock-instruction edits (PID 0x52)"; proofs:
> `tests/test_ff_obd_patch.py` (43 tests).
>
> * **What changed in stock.** F6's option 3 on the three-entry **B2** list:
>   seven instruction words in `obd_pid_support_build` / `obd_pid_read`
>   (external flash, Bosch block 0x058000-0x05FFFF) point the list at a
>   relocated four-entry copy, and `tbl_obd_pid_class[0x52]` becomes **0x02**
>   (a group-B byte; F6's 0x82 was the group-A value of its control).
>   The list is **not** in calibration: every 0xFF run in the r2 window turned
>   out to be a live map cell or the segment header (`obd.md` §9.1), so it
>   sits in the blank flash block 0x160000-0x16FFFF, reached by `lis` /
>   `addis r2` / `addis r13`, one instruction each. No hook word was added;
>   the patch still has eight.
> * **The gate.** `ff_pid52_enable` (FFCAL001 **v5**, +0x14A, 334 B) ships
>   **0**. The patch writes the record `{A, valid}` at `ff_state` +0x4C every
>   10 ms, with `A = round(E_filt × 255 / 100 %)` and
>   `valid = ff_pid52_enable && cal_ok`; the stock builder turns `valid` into
>   the `01 40` support bit and the stock reader into `41 52 A`. With the byte
>   at 0 the image is **observably identical to stock on mode 01** (bitmaps
>   and every PID answer, proven in the emulator against all 41 stock PIDs)
>   but not byte-identical — the seven words and the list are always there.
> * Still true: **no DTC is raised**, and measuring block 111 is the primary
>   display. Open, bench-only: whether the car's scan-tool path reaches
>   internal session 6 (`obd.md` §8 item 4) and which walker runs the bitmap
>   builder (§8 item 2).

### 3.8 Persistence (Phase 5)
Store `E_filt` in EEPROM via the ECU's own EEPROM block handler or in
battery-backed RAM if the external SRAM is permanently powered (to be
verified), so a cold start after a battery disconnect uses the right cranking
fuel.

> **2026-09-15 — agent B4, issue #18: the persistence route is decided.**
> Evidence and full derivation: `re/findings/eeprom.md`. Reproduce the layout
> with `python3 tools/eeprom_map.py data/passat_azx_ori.bin --clients`.
>
> **Primary route — EEP_CONF block 8, payload offset +2, one byte.
> VERIFIED-STATIC for everything except the factory contents of that byte.**
>
> * The SPI EEPROM is a 2 KB **M95160-class** part on **PCS0** of the QSMCM
>   QSPI (0x705000), SPI mode 0, 8 bits per transfer, SCK ≈ 1.25 MHz
>   (`eeprom_spi_config` 0x085888, `eeprom_write_byte` 0x085A8C,
>   `eeprom_read_bytes` 0x085BC0).
> * Its layout is a **32-record table at file 0xB2FF0** (`tbl_eep_conf`), 12
>   bytes per record, driven by the block manager `nvm_block_request`
>   (0x06131C). The table covers 0x000-0x7FF with **no gap and no spare
>   block**, so a 33rd block is not an option.
> * **Block 8** lives at EEPROM 0x1C0 with a **duplicate copy at 0x1E0**, is
>   32 bytes (one page, so a commit is a single ~5 ms page write), mirrors to
>   RAM **0x7F9F80**, and has exactly one stock client — a single byte at
>   payload offset +14 (call site file 0x134380). Payload offsets **+0..+13**
>   and **+15..+28** are untouched by any constant-offset code path;
>   offset +29 belongs to the manager (`flags = 0x03F5`, bits 0-1 set) and
>   offsets +30..+31 are the checksum.
> * **Write path — use stock code only:**
>   ```c
>   u8 e_pct;                                  /* 0..100, or 0..255 for 0.5 % */
>   nvm_block_request(8, 0, 1, 0, &e_pct, 0);  /* stage into the mirror; returns 2 */
>   nvm_block_request(8, 0, 0, 0, 0, &handle); /* commit; returns 1, then poll handle */
>   ```
>   Read back with `nvm_block_request(8, 0, 1, 0, &dst, 0)`, or simply read
>   RAM 0x7F9F80 — `nvm_read_all_blocks` (0x062280) has already filled the
>   mirror and verified the checksum by the time the application runs.
> * **Checksum implications: none for the patch.** The commit path calls
>   `nvm_checksum_generate` (0x061A48), which recomputes the block checksum —
>   the **16-bit sum of payload bytes [0, len-2), stored bit-complemented as a
>   big-endian u16 at offset len-2** — and writes both copies. This is *not*
>   the flash block algorithm of `docs/02_memory_map.md` §6; do not point
>   `tools/checksum.py` at EEPROM images. `tools/eeprom_map.py --check` does
>   the EEPROM variant.
>   The corollary is the one hard rule: **never write the EEPROM through the
>   raw SPI primitives** (`eeprom_write_byte`/`eeprom_write_bytes`), or the
>   patch has to maintain the checksum itself and will race the manager's
>   mirror.
> * **Write frequency.** One page write per commit; M95160 endurance is
>   ~1e6 cycles (COMMUNITY, ST datasheet). Commit at key-off (or on a change
>   of more than the calibratable hysteresis), never cyclically.
>
> **Fallback A — EEP_CONF block 24** (EEPROM 0x620, 255 bytes, mirror
> 0x7FA2A0): 252 unused payload bytes, but a single copy and an 8-page
> (~40 ms) write. Use only if a bench read shows block 8 is occupied.
>
> **Fallback B / bench development — external SRAM.** The firmware treats
> 0x800000-0x807FFF as **retained across reset**: the boot sizing probe
> `ext_sram_probe` (0x011898) saves and restores every word it disturbs, and
> neither start-up copy clears the region (the `.data` image for it is empty,
> `src == dst == 0x800000` at 0x09E438 and 0x08A1AC). VERIFIED-STATIC for the
> code; **HYPOTHESIS** that the SRAM sits on KL30 — that is an electrical fact
> and must be measured before any state is trusted to it across a key cycle.
> Note also that a KWP flash-programming session copies code to 0x804800
> (`FUN_0008A12C`), so keep flex-fuel state below that.
>
> **Not applicable:** the `vkKraQu` fuel-quality variant byte the 1K8907115F/L
> community patches manipulate **does not exist in this software**
> (`re/findings/variants.md`). There is no stock variant byte to reuse for a
> map-set switch and no coding bit the fuelling path reads.

> **2026-09-16 — brief C2, issue #23: Fallback B is REFUTED.** The external
> SRAM is ordinary `.bss`: `ram_clear_block` (0x06D8F8), called from `app_init`
> (0x04CCD4), zeroes 0x800004-0x80498F at every cold start, and
> 0x804990-0x807FFF is the flash driver's programming copy
> (`re/findings/ram.md` §3, `re/findings/eeprom.md` §6). Only the four bytes
> the probe itself saves survive. **EEPROM block 8 is the only route**; do not
> revive the battery-backed-RAM branch.

> **2026-09-16 — brief D2, issue #38: implemented, with three corrections.**
> Code: `patches/ff_fuel/src/ff_diag.c` (`ff_persist_init`, `ff_persist_tick`).
> Calibration: `ff_persist_enable` = 1, block 8, offset 0, hysteresis 5 %,
> rate 60 s. Bench procedure: `patches/ff_fuel/test/procedure_d2.md` part B.
>
> 1. **The read-back call above has the wrong mode.** It is
>    `nvm_block_request(8, 0, 1, **1**, &dst, 0)`. With mode 0 the identical
>    argument list is the *stage* shape and overwrites the mirror with whatever
>    the destination buffer happened to contain. The shape is chosen by a
>    16-entry table at 0x6199C indexed by
>    `8*(len!=0) + 4*(handle!=0) + 2*(buf!=0) + mode`
>    (`re/findings/eeprom.md` §8.1).
> 2. **The "handle" is a 9-byte record, not a word**, and the manager keeps a
>    *pointer* to it in a 4-slot queue, so it must be stable storage — the
>    patch keeps it in its own RAM block at 0x7FFB44. `+8` is the status: 1
>    queued, **2 done**, 0x80 device failure, 0x82 checksum failure (§8.2-8.3).
> 3. **"Commit at key-off" is not available.** Block 8 has exactly one stock
>    client and it never commits; the write-all-blocks routine has no
>    resolvable trigger; the synchronous-shutdown flag's two setters have no
>    callers (`re/findings/eeprom.md` §9). So the patch commits *while the
>    engine runs*, rate-limited to one page write per minute and gated on a
>    5 % hysteresis and on mode OK/HOLD. The stored value is then at most one
>    minute old and does not depend on an orderly shutdown at all — which is
>    strictly better than a key-off flush for the case #38 cares about, a
>    battery disconnect.
>
> The restore seeds **both** `e_filt` and `e_key`. Seeding only the decay
> target would leave the first 50 s of a cold start on the E0 fuel factor with
> an E85 tank — lean, the dangerous direction. A store that reads back 0xFF or
> above 100 is ignored and the patch starts at E0.
>
> Still open, and the first thing part B of the procedure does: **nothing
> proves the factory leaves block 8 payload +0 at 0xFF.** `ff_persist_offset`
> and `ff_persist_block` are calibration bytes precisely so that a bench read
> can move the store without a rebuild.

#### Correction 2026-09-17 (brief E4, issue #38) — the offset was wrong, and silently so

`ff_persist_offset` shipped as **0** and has been changed to **2**.

Payload **+0 and +1 of every EEP_CONF block are a `{block id, version}`
stamp**. `nvm_read_all_blocks` (**0x06227C** — the entry is four bytes below
the 0x062280 this document and `eeprom.md` §3.5 quoted) compares the first
halfword of each block against that block's record in the flash default table
at 0x060458-0x060470, and on a mismatch it **discards the block and reloads
the defaults**. Block 8's default record is
`08 01 00 80 80 80 80 00 00 80 00 80 80 FF`.

So the ethanol percent was being written on top of the block id. Every cold
start threw the block away, the mirror byte the patch read back was `0x08`,
and `ff_persist_init()` accepted it as a perfectly plausible **8 %** — not the
`0xFF` that would have meant "nothing known". **#38 did not work, and nothing
said so.** It took a QSPI device model and a run of the real
`nvm_read_all_blocks` to see it (`re/findings/eeprom.md` §10.5, §10.6).

Three things follow:

* the free payload offsets for block 8 are **+2..+13**, not +0..+13, and the
  same correction applies to blocks 1, 3, 7, 11 and 12 in `eeprom.md` §5;
* payload **+29 moves** on the first commit (0xFF → 0x00 → 0x01): it is the
  manager's ReplV byte. The rule is "the patch never writes it", not "it never
  changes";
* the fix needed no code — one calibration byte — which is the argument for
  having put the block, the offset and the rate limit in FFCAL001 in the first
  place.

#### Hazard, added 2026-09-23 (brief G2, issue #38) — block 8 is also the adaptation-channel block, and payload +2 appears to be channel 1

The fuel side of this hazard is in §3.3, note of 2026-09-23. Here is the
persistence side. The inputs are VERIFIED-STATIC facts that are already on
record. Putting them together is G2's reading and is **not yet proved in the
emulator**, so the conclusion is tagged **HYPOTHESIS (conflict to resolve)**.

* F4 (`re/symbols.csv` `adaptation_restore_all` 0x12E3F8; `calibration_names.md`
  §10.1): at every power-up the loop runs
  `nvm_block_request(*(u8*)0x0A3AD8, n + *(u8*)0x0A3AD9, 1, 1, PTR[0x0A3ADC + 4n], 0)`
  for n = 0 … 0x10. The two descriptor bytes are **0x08 0x02**
  (`xxd -s 0x0A3AD8 -l 4 data/passat_azx_ori.bin` → `0802 0100`), and
  `PTR[0]` = **0x7FD06B**, adaptation **channel 1**. So channel *k* is block 8
  payload **+(k + 1)**, and the 17 channel slots cover **+2 … +18**.
* E4 (above): block 8's default record is
  `08 01 | 00 80 80 80 80 00 00 80 00 80 80 FF`. From +2 on, these are the
  channel defaults of §10.1's table in order (ch1 0, ch2-5 128, ch6 unimpl.,
  ch7 0, ch8 128, ch9 0, ch10 128, ch11 unimpl., ch12 255). The "one stock
  client at +14" of `eeprom.md` §4 is then channel 13's slot.
* D2/E4: `ff_persist_offset` = **2**, so the patch stages and commits the
  ethanol percent to block 8 **+2**.

If that reading holds, E4's "free payload offsets +2..+13" is wrong and the
ethanol store **shares its byte with adaptation channel 1**. Channel 1 is
0x7FD06B, limits 0/0, signed, read at 0x46B0BC, with no known meaning. Two
consequences to check before a bench flash of `ff_persist_enable` = 1:
(a) at power-up the stock restore copies the stored E % into 0x7FD06B, a
cell the calibration pins to 0; what 0x46B0BC does with it is not traced.
(b) a workshop adaptation reset (sub-function 0x82, channel 0) followed by a
commit may write 0 to the store. The patch accepts 0 as a valid E0, which is
the lean direction on an E85 tank. The same pattern as E4's 8 % bug.

**Not fixed here.** It needs `patches/ff_fuel/ffcal001.py` and
`re/findings/eeprom.md`, which G2 does not own. The check is an emulator run of
`adaptation_restore_all` against the QSPI device model of `eeprom.md` §10.5,
and then a choice of an offset outside +2 … +18 (block 8 has +19 … +28 left
before the manager's +29), or channel-free space in block 24 (Fallback A).
Filed for the integrator in `docs/agent_briefs/README.md` wave-G notes.

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

#### Added 2026-09-16 (brief D1, issue #32) — the block exists; two names and one unit changed

`patches/ff_fuel/ffcal001.py` builds it and is the authority on the layout;
`patches/ff_fuel/src/ff_state.h` carries the same offsets and
`tests/test_flexfuel_model.py` asserts the two agree. **FFCAL001 v1, 232 bytes
at 0x5E2510**, header `FFCAL001` + version u16 + length u16, and a 16-bit
checksum (bit-complement of the byte sum of `[0, length-2)`) in the last two
bytes. All four tables this section lists but the MVP does not read yet —
`ff_fzw_curve`, `ff_dzw_map`, `ff_fst_map`, `ff_prail_add` — are present with
neutral values, so a later patch **extends** the block instead of moving it.

Differences from the table above:

* **`ff_filter_k` (32) became `ff_filter_tau_ms` (3000 ms)** and a new
  `ff_tick_ms` (10 ms) was added, so every time constant in the block is
  physical and the raster period is a calibration value rather than a compiled
  constant (§3.2 note, deviation 2). `ff_timeout_ms`, `ff_hold_s` and `ff_slew`
  (as `ff_slew_pct_s`) keep their names, values and units.
* **Added:** `ff_mode` (0 off / 1 normal / 2 bench override, shipped 1) and
  `ff_e_override` from §3.2's design list, `ff_stall_max` (3 frames), and the
  five parameters brief **D2** needs — `ff_persist_enable` (0),
  `ff_persist_hyst_pct` (5), `ff_persist_block` (8), `ff_persist_offset` (0)
  and `ff_persist_rate_s` (60), the last four matching the EEPROM route of
  §3.8.
* `ff_can_id` stays, but it is documentation plus a sanity check: the receive id
  really lives in the flash edit at 0x2BD8C, and the patch compares the id echo
  at 0x803F98 against this value before believing a frame.

Descriptor rows for `re/calibration_draft.csv` are in
`patches/ff_fuel/ffcal001_rows.csv` (D1 must not write that file while brief D3
owns it); the integrator appends them and re-runs `tools/draft_to_xdf.py`.

#### Added 2026-09-17 (brief E1, issue #34) — FFCAL001 **v2**, 266 bytes

E1 **appended and moved nothing**. Everything up to +0xE5 is exactly where v1
put it; the four new parameters start at +0xE6, which is where v1's checksum
used to be, and the checksum followed the length to +0x108.

| Off | Name | Type | Shipped | Unit |
|---|---|---|---|---|
| +E6 | `ff_zw_enable` | u8 | **0** | 1 applies the ignition blend |
| +E7 | `ff_dzw_max` | u8 | 8 | 0.75 °CA counts; code clamps to 16 |
| +E8 | `ff_dzw_nmot_axis[8]` | u16 | KFZW rows | `nmot_w`, 520…6520 rpm |
| +F8 | `ff_dzw_rl_axis[8]` | u16 | KFZW cols | `rl_w`, 10.2…103.9 % |

and two tables this section listed as reserved are now live: `ff_fzw_curve`
(+0x42) carries the §3.4 ramp instead of zeros, and `ff_dzw_map` (+0x54) is
read by `ff_ign.c` — it stays **all zero**, which is what keeps the shipped
file inert even if `ff_zw_enable` is set to 1. `ff_fst_map` and `ff_prail_add`
are still reservations, for briefs E2 and E5.

**The version is now checked strictly.** `ff_cal_ok()` accepted **version 2
only** at the time of writing (E2 made it 3), so a v1 block flashed under a v2 blob reads as corrupt and forces
mode 0: `F = 1024`, no CAN, `dzw_e = 0`. That is the safe direction and it is
the rule every later version bump follows — E2 will make it 3, E5 4.

`ffcal001.py` refuses to *build* a block that would be unsafe, rather than
leaving it to the ECU: `ff_F_curve[0] != 1024`, `ff_fzw_curve[0] != 0`, either
curve non-monotonic, an axis that is not strictly increasing (the breakpoint
search assumes it), or an `ff_dzw_max` above the code ceiling.

`ff_dzw_map` also gains real **axes** in the descriptor rows, so it goes into
the XDF as a `map_2d` with its own breakpoints rather than as a bare
`map_2d_data` block.

#### Added 2026-09-17 (brief E2, issue #35) — FFCAL001 **v3**, 290 bytes

E2 **appended and moved nothing**, the same way E1 did. Everything up to
+0x107 is exactly where v1 and v2 put it; the eight new parameters start at
+0x108, which is where v2's checksum used to be, and the checksum followed the
length to +0x120.

| Off | Name | Type | Shipped | Unit |
|---|---|---|---|---|
| +108 | `ff_st_enable` | u8 | **0** | 1 applies `f_st(E, tmst)` |
| +109 | `ff_zwst_enable` | u8 | **0** | 1 applies the start advance |
| +10A | `ff_fst_max` | u16 | 2048 | Q10 ceiling; code clamps to 2560 |
| +10C | `ff_zwst_max` | u8 | 4 | 0.75 °CA counts; code clamps to 8 |
| +10D | `ff_zwst_tmax` | u8 | 117 | `tmst` count = 39.75 °C |
| +10E | `ff_fst_e_axis[6]` | u8 | 0, 20, 40, 60, 85, 100 | % — the map's rows |
| +114 | `ff_fst_tmst_axis[6]` | u8 | 24, 44, 64, 91, 117, 184 | `tmst` counts — the columns |
| +11A | `ff_fzwst_curve[6]` | s8 | **0** | 0.75 °CA, on the E axis above |

and the table this section listed as reserved since v1, **`ff_fst_map`
(+0x94)**, is now read by `src/ff_start.c`. It stays **all 1024**, which is
what keeps the shipped file inert even with `ff_st_enable` = 1.
`ff_prail_add` is the only reservation left, for brief E5.

**The version stays strict.** `ff_cal_ok()` accepts **version 3 only**, so a v1
or v2 block flashed under a v3 blob reads as corrupt and forces mode 0:
`F = 1024`, no CAN, `dzw_e = 0`, `fst_q10 = 1024` and `zwst_add = 0`. E5 makes
it 4.

`ffcal001.py` gained five more refusals: an `ff_fst_map` whose **row 0 is not
exactly 1024** (E0 would stop being bit-identical at both S1 sites), any cell
below 1024 (`f_st` may only enrich) or above the code ceiling 2560, an
`ff_fst_max` outside 1024..2560, an `ff_zwst_max` or `ff_fzwst_curve` above 8
counts, and an `ff_fzwst_curve[0]` that is not 0.

`ff_fst_map` also gains real **axes** in the descriptor rows, so like
`ff_dzw_map` it goes into the XDF as a `map_2d` with its own breakpoints;
`ff_fzwst_curve` borrows the same ethanol axis.

**The two code ceilings are chosen, not arbitrary.** `FF_FST_HARD_MAX` = 2560
because `(2560 × 100) >> 10 = 250` is the largest value measuring block 69
field 1 can carry (formula 0x21, A = 100), so there is no `f_st` the patch can
produce that a tester cannot see. `FF_ZWST_HARD_MAX` = 8 counts = 6.00 °CA,
twice the shipped ceiling — and it is the clamp that matters most in the whole
patch, because the knock retard is bypassed during the start.

#### Added 2026-09-17 (brief E5, issue #36) — FFCAL001 **v4**, 332 bytes

The fourth version, and the fourth time nothing moved: everything up to
+0x121 is exactly where v3 left it, and E5's parameters start at **+0x122**,
which is where v3's checksum used to be.

| Off | Type | Name | Shipped | Unit |
|---|---|---|---|---|
| +122 | u8 | `ff_prail_enable` | **0** | 1 applies the adder; 0 pins `prail_add` at 0 for ever |
| +123 | u8 | `ff_prail_rsv` | 0 | reserved; it is here only so the two words below stay 2-byte aligned |
| +124 | u16 | `ff_prail_max` | 3000 | 0.005 bar = **15.0 bar**, exactly the headroom `KLPRMAX` 22000 leaves above `KFPRSOLHOM`'s 19000 |
| +126 | u16 | `ff_diag_window_ms` | 1000 | ms of the diagnostic window |
| +128 | 17×u16 | `ff_prail_curve` | **0** | 0.005 bar over E 0..100 step 6.25 %, the same grid as `ff_F_curve` |
| +14A | u16 | crc | | |

`ffcal001.py` refuses to build a block with `ff_prail_curve[0] != 0` (E0 would
stop being bit-identical at 0x45845C), a non-monotonic curve (more ethanol may
not mean less pressure), any point or an `ff_prail_max` above
`FF_PRAIL_HARD_MAX` = 6000, an `ff_diag_window_ms` of 0, or a non-zero
`ff_prail_rsv`.

**The v1 reservation is superseded, not re-used.** `ff_prail_add` at +0xDC —
eight u8 in 0.1 MPa with no axis at all — stays in place, neutral and unread,
so that nothing in the block moves. It was the wrong shape: E5 needed
seventeen points on the ethanol grid the rest of the block already uses, in
the ECU's own 0.005 bar rather than in 0.1 MPa, so it appended a proper table.

**The code ceiling is arithmetic again.** `FF_PRAIL_HARD_MAX` = 6000 = 30.0 bar
is twice the shipped ceiling and twice the entire headroom the stock
calibration has, and anything above it is a calibration that lies: the stock
`KLPRMAX` clamp four instructions after the hooked store eats it.

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
| Periodic task table / hook point (100 ms) | a counter patch increments at 10 Hz — **corrected 2026-09-16 (C4, #44): at 100 Hz.** The hook site 0x12067C is in a **10 ms** raster, not a 100 ms one; see §8 |
| Fuel mass -> injection quantity multiplication (KRKATE path) and its variables (`rk`, `te`/`ti` per bank) | logged `ti` reproduces the decompiled formula for logged inputs |
| Lambda adaptation variables (`fra`, `frau`, `frao`, `rkat`) | found in the measuring-variable table and logged |
| `KFZW/KFZW2` blend and the final `zw` output; `dwkrz` | map addresses confirmed by xref and by a bench edit — **xref half done 2026-09-15 (B7, §3.4 note): no blend exists, `KFZW` 0x5C75FE, `KFZWOP` 0x5CA3F1, `dwkrz` 0x7FCE57-5C, insertion point 0x41D40C. The bench edit is still open.** |
| Start/afterstart maps and `tmot` | same |
| Rail setpoint maps and `ti` window limits | same |
| `vkKraQu` presence (fuel-quality variant byte) | present/absent decided; if present, its consumers listed |
| EEPROM block handler | write path understood (Phase 5) |
| Security access algorithm (KWP 0x27) | logger authenticates |

---

## 8. Added 2026-09-16 (C4, issue #44) — every raster in this document is 10x faster than assumed

`re/findings/scheduler.md` sections 11 and 12 settle the ERCOSEK periods.
The tick unit B1 used was five times too coarse and the activation chain was
not known, so the rasters are

| Raster | Task set A | Task set B |
|---|---|---|
| 1 ms | 0x4240C8 | 0x11EBF4 (`task_10ms`) |
| 2 ms | 0x424900 | 0x11EC34 (`task_20ms`) |
| 5 ms | 0x424AF8 | 0x11EC58 |
| **10 ms** | **0x4328E4** (`task_100ms_int`) | **0x1205A0** (`task_100ms`) |
| **20 ms** | **0x45CAC4** (`task_1000ms_int`) | **0x120FAC** (`task_1000ms`) |
| 50 / 100 / 200 / 1000 ms | ids 25 / 18 / 22 / 17 | ids 37 / 31 / 34 / 30 |

The symbol names are kept as they are, because five findings files and the
issue tracker use them; the **names are wrong, the addresses are right**.

What this changes here:

* **§2.3 / §7, the Flash-1 counter.** The hook at 0x12067C runs at **100
  calls per second**, not 10. A one-minute bench run gives ~6000 counts, not
  ~600. That is C1's `patches/ff_counter/test/procedure.md` §4 slope; the
  procedure is otherwise unaffected and now doubles as the *10 ms* period
  test. (And it must first establish which task set is live — see below.)
* **§2.2, `can_rx_poll`.** "Every 10-100 ms" from a raster task is still what
  is wanted, but the task named there polls at 10 ms, which is comfortably
  faster than the Pico's 100 ms frame rate. Nothing to change; the CAN
  freshness/age logic of §3.2 gets ten times more samples than budgeted.
* **§3.2, the filter.** `K ≈ 1/32 per 100 ms` was written for a 100 ms
  raster. If the filter is hooked at 0x12067C it runs every 10 ms, so the
  same K gives a **0.3 s** time constant instead of 3 s. Either use
  `K ≈ 1/320` (i.e. a shift of 8 or 9 with the rate limiter doing the rest)
  or drive the filter from a 100 ms raster (task set A id 18 / set B id 31)
  instead. The `2 %/s` slew limit must likewise be expressed per raster
  period, so 0.02 %/activation at 10 ms.
* **§5.2, `rksplit` at 0x42247C.** Unaffected: that hook is in the
  engine-synchronous task 0x4223B0, not in a time raster.
* **Rail (B6 options C/D, `rail.md` §7).** The `%HDR*` chain in 0x45CAC4 runs
  at **20 ms**, so the ethanol factor reaches `rkti_pre`/`frt` within one
  20 ms period. Every "unknown latency" note attached to that task is closed.

**Open, and it gates the counter test:** which of the two task sets runs with
the engine turning. Set A is installed by `os_init`; 0x11DA64 switches to set
B if the byte 0x7FEB5E is non-zero. If set A is live, a hook at 0x12067C
never executes at all. The bench log in `scheduler.md` §11.7 settles it in one
10-second read of five RAM counters.
