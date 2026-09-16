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

### 3.8 Persistence (Phase 5)
Store `E_filt` in EEPROM via the ECU's own EEPROM block handler or in
battery-backed RAM if the external SRAM is permanently powered (to be
verified), so a cold start after a battery disconnect uses the right cranking
fuel.

> **2026-09-15 — agent B4, issue #18: the persistence route is decided.**
> Evidence and full derivation: `re/findings/eeprom.md`. Reproduce the layout
> with `python3 tools/eeprom_map.py data/passat_azx_ori.bin --clients`.
>
> **Primary route — EEP_CONF block 8, payload offset +0, one byte.
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
