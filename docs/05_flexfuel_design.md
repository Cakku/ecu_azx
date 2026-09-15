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
| `KFZW/KFZW2` blend and the final `zw` output; `dwkrz` | map addresses confirmed by xref and by a bench edit — **xref half done 2026-09-15 (B7, §3.4 note): no blend exists, `KFZW` 0x5C75FE, `KFZWOP` 0x5CA3F1, `dwkrz` 0x7FCE57-5C, insertion point 0x41D40C. The bench edit is still open.** |
| Start/afterstart maps and `tmot` | same |
| Rail setpoint maps and `ti` window limits | same |
| `vkKraQu` presence (fuel-quality variant byte) | present/absent decided; if present, its consumers listed |
| EEPROM block handler | write path understood (Phase 5) |
| Security access algorithm (KWP 0x27) | logger authenticates |
