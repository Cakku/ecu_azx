# Per modification type: which maps, which logs, which limits — draft 2 (issue #43)

Draft 1: agent E3, 2026-09-17, prose only. **Draft 2: agent F4, 2026-09-22** —
same content, restructured into the three columns issue #43 asks for, plus the
objects the naming passes added since.

**Everything here is HYPOTHESIS and a reading list, not a calibration
procedure.** Nothing in it has been tried on an engine. Issue #43 exists to
turn it into a procedure; that needs the bench spare and a car, not another
desk pass.

Read `re/README.md` ("Using it in TunerPro") first. Addresses are **CPU**
addresses; the XDF uses file offsets. Names are the ones in
`re/calibration_names.csv`; a `cand_` prefix means the Bosch label is a
candidate, not a fact, and a descriptive name (`mix_*`, `zwdelta_*`, `rl_*`,
`axis_*`) means no Bosch label is claimed at all. Nothing here overrides
`docs/04_re_guidelines.md` §6: work on a copy, fix the checksums, bindiff, and
nothing goes to the car that has not run on the bench spare.

## How to read the three columns

| column | what it holds |
|---|---|
| **maps** | the named object, its CPU address, and *what moves* when you change it |
| **logs** | how to watch it: a VCDS measuring id (and the group where a findings file has established one) from `re/measuring_vars.csv`, or a RAM cell to read over DDLI with `logging/med9log.py`, plus **the check to apply** to the number |
| **limits to watch** | the protection or clamp that bites first, with its address and its value in this dataset |

Two things the *logs* column depends on, both from
`re/findings/measuring_vars.md` §7:

* display formula **0x05 saturates at 143 °C** (B = 243). Any
  component-temperature check near the top of the range has to read the RAM
  cell over DDLI, not the measuring block.
* display formula **0x21's `A` is a tester-side normalisation**, so a VCDS
  relative-charge reading is about **3.8 % below** the number the ECU's own
  maps use. Compare logs with logs, not logs with map axes.

The DDLI session file for everything below is
**`logging/sessions/tuning_checklist.json`**:

```bash
python3 logging/med9log.py log --session logging/sessions/tuning_checklist.json \
        --bus gs_usb:0 --seconds 300 -o logs/2026-xx-xx_baseline.csv
python3 tools/logcmp.py logs/…_baseline.csv logs/…_after.csv
```

---

## 0. The five objects every modification touches

| maps | logs | limits to watch |
|---|---|---|
| `cand_KFMIRL` **0x5C9372** — charge setpoint from torque setpoint. More torque per unit of charge and this map is wrong in the same direction everywhere | `misol_w` id **8** (0x8035E6) and `rlsol_req` id **375** (0x803510) together: the ratio between them *is* this map. 32768 counts = 100 % (measuring handler 0x03C348, A = 0x80) | `cand_RLSOLMX` **0x5C8C0C** = 254 → **99.2 %**: the request is clipped there, the measurement is not |
| `cand_KFMIOP` **0x5CA252** — torque from charge, the inverse. The torque interface, the gearbox request and the level-2 monitor all read it | the same pair, driven from the other end | level-2 monitoring (`%UFRKC`, fr_index §7). Do not move `KFMIOP` without moving `KFMIRL` |
| `KFZW` **0x5C75FE** / `KFZWOP` **0x5CA3F1** — base ignition and the optimum-angle reference | ignition angle id **9** (0x7FEF87, **VCDS 003.4**) and the per-cylinder knock retard `dwkrz` **0x7FCE57[6]**, ids 88-93. Check: `dwkrz` must stay at 0 in steady state | `cand_KFZWMN` **0x5D5BCB** and the `%ZWMIN` family — they limit *retard*, so they do not protect against advance |
| `KFPRSOLHOM` **0x5D5324** — the rail-pressure setpoint in normal driving | `prsoll` id **501** (0x8031F4, **VCDS 231.2**) and `prist` id **500** (0x8031DA, **106.1**), both 0.005 bar/LSB. Check: `prist` tracks `prsoll` within a few bar in steady state | `KLPRMAX` **0x5D5546** = 22000 = **110.0 bar** and `PRSOLMN` **0x5D5572** = 7000 = 35.0 bar |
| `KFKHFM` **0x5C430D** + the eight `KFPU*` **0x5C43EF … 0x5C4A1D** — the air-mass signal correction | raw HFM **0x7FEF9E** and the corrected `cand_mw_ml` over DDLI; relative charge id **2** (0x7FEF74, **VCDS 002.2**). Check: the air mass must not ring at low speed and wide-open throttle | none — these are a *measurement* correction; the limits they can trip are everything downstream |

**New in draft 2, and it belongs at this level:** `zwdelta_7FD338_build`
0x459334 adds an ignition term on top of `zwgru` that no earlier document
mentioned — up to **+7.5 °CA** cold and **−6.0 °CA** hot, gated to above 47 %
relative charge (`calibration_names.md` §10.5). Log **0x7FD338** over DDLI
before and after any ignition change; it is not a measuring variable.

---

## 1. Intake (filter, pipe, manifold, throttle body)

| maps | logs | limits to watch |
|---|---|---|
| `KFKHFM` **0x5C430D** (`nmot`, `rl`), today all 128 = 1.0 — the map Bosch provides for exactly this change | raw HFM **0x7FEF9E** vs. `cand_mw_ml` over DDLI. Check: the ratio is the map; it should be flat in steady state | — |
| `KFPU` … `KFPUKL123` **0x5C43EF / 0x5C44D1 / 0x5C4859 / 0x5C45B3 / 0x5C4A1D / 0x5C4777 / 0x5C493B / 0x5C4695** — pulsation correction, today ±5 %. A freer intake makes pulsation *worse*. Check `KFPUKL123` first | raw HFM **0x7FEF9E** at low `nmot` and full throttle. Check: peak-to-peak of the raw signal against the baseline log | — |
| `cand_MLDKFHFM` **0x5C4AF0**, `cand_NPULSHFMMN` **0x5C4AE4** — the filter of the same signal. Leave alone unless a log shows ringing | same | — |
| `cand_KFMIRL` / `cand_KFMIOP` — more charge for the same throttle angle | ids **8** and **375** | `cand_RLSOLMX` **0x5C8C0C** = 99.2 % |
| — | **new:** `rl_rlsolreq_limit_curve` **0x5D9216** is a ceiling the ECU itself watches: 100.0 % below 4000 rpm, 105.0 % above. Log `rlsol_req` id **375** against it | it sets **0x7FD442 bit 0** when exceeded (DDLI); what that bit then does is not established |

---

## 2. Exhaust (manifold, cat, back pressure)

| maps | logs | limits to watch |
|---|---|---|
| `KFZW` **0x5C75FE** and `KFZWOP` **0x5CA3F1** — less residual gas moves the knock limit *and* the optimum angle. Both, not one | ignition id **9** (VCDS 003.4) and `dwkrz` **0x7FCE57[6]** (ids 88-93). Check: retard must not grow after the change | `cand_KFZWMN` **0x5D5BCB** (`%ZWMIN`) — see §5 |
| `cand_KFPSSRM` **0x5D1CBA** and the rest of `FUN_000E06F8` — the manifold/residual-gas chain. **Read before you touch:** its value unit is still open (`calibration_names.md` §9.6, §10.6) | 0x8015D0 over DDLI (the key into `KFPSSRM`) | — |
| `KLPRMAX` **0x5D5546** stays at 110 bar: an exhaust change is no reason to raise it | `prist` id **500** | itself |
| the stock low-octane detector `cand_KFSWKFZK` **0x5D5A3E**, `cand_KFSWKFZKR` **0x5D5AFE**, `cand_KFDZK` **0x5D597E** | `dwkrz` ids 88-93 and, over DDLI, the detector's own state | the knock window `cand_NKRMN` **0x5D611C** (3000 rpm) and `cand_DRLKR` **0x5D6124** decide where it may look at all. A cat-back change should not move them |
| **new:** the knock block's own enable, `TKRBB` **0x5D6122** = 130 → 49.5 °C, and `CW_KRKE_BB` **0x5D611A** = 0 | `tmot` id **80** (VCDS 001.2) — but see the 143 °C saturation warning | `CW_KRKE_BB` = 0 means a **knock-sensor fault alone arms 0x7FEA84**, which arms `cand_KLRLMXNRED` — §3 |

---

## 3. Camshafts / valve timing

| maps | logs | limits to watch |
|---|---|---|
| `KFZW`, `KFZWOP`, `cand_KFDZWKG` **0x5C753E** — different overlap, different internal EGR, different knock limit | ignition id **9**, `dwkrz` ids 88-93 | `%ZWMIN` (§5) |
| `cand_KFMIRL` / `cand_KFMIOP` — cam timing moves volumetric efficiency at every point, so this is a large change | ids **8** and **375** | `cand_RLSOLMX` **0x5C8C0C** |
| `cand_KFRLSOLDY` **0x5C8FAE** — the step size with which the charge setpoint chases the request. More overlap, slower manifold | `rlsol_w` **0x803508** and `rlsol_req` id **375** over DDLI. Check: no overshoot on a throttle step | `cand_DRLSOLMX` **0x5C91F0** = 1.00 %/step, the per-activation ceiling in `%MDFUE` |
| `KFPU*` — the pulsation at the HFM is a function of valve events; expect to re-measure | raw HFM **0x7FEF9E** | — |
| **new:** the six `rl_*` thresholds of `rl_thresholds_per_mode` 0x0C7DD0 (**0x5D9018, 0x5D90D8, 0x5D9138, 0x5D9198, 0x5D9010, 0x5D9216**) are maps over (engine speed, **operating mode**) | the operating-mode index id **130** (0x80223B, **VCDS 051.3 / 068.3**). Check: which mode the engine actually sits in, before and after | — |
| — | — | **`cand_KLRLMXNRED` 0x5D7EAE**, the one calibrated charge limiter: 100 % to 3520 rpm, then 71, 60, 55, 52, **50 %** at 4000…6520 rpm. Armed by the debounced flag **0x7FEA84**. Log the arbitrated limit id **2051** (0x80235A) on every run |

---

## 4. Injectors and fuel system (bigger HDEV, higher rail pressure)

`re/findings/injection.md` §5-§6 and `docs/05_flexfuel_design.md` are the real
documents; this is the index.

| maps | logs | limits to watch |
|---|---|---|
| `KRKATE` **0x5D3DBC** = 3858 — the injector constant, the single scalar a different static flow changes. Everything else in `%RKTI` is a shape | `ti` id **595** (0x8030C4, **VCDS 002.3**; the handler clamps at 0xFE01 and divides by 255) and `rk` id **3** (0x80303A). Check: `ti` at a known operating point moves by the flow ratio and nothing else does | `TIMINP` **0x5C7328** = **900 µs**, the minimum pulse |
| `KLTIKRPR` **0x5C72F8** (flow vs. rail pressure) and `TVUB` **0x5C7310** (dead time vs. dp) — injector-specific, from the data sheet, not from a guess | `ti` id **595** against `prist` id **500** | `TIMINP`; too large a value breaks `%ZGST` (fr_index §1.1) |
| `KLHDEV` **0x5C729C** — linearisation of small injection times. Idle quality is won or lost here | `ti` id **595** at idle; the per-cylinder `%ZGST` factors **0x801D8C[cyl]** over DDLI | `TIMINP` |
| `FKKVS` **0x5C71F8** — rail-pulsation correction over (`ti`, `nmot`) | `ti` id **595**, `prist` id **500** | — |
| `KFPRSOLHOM` **0x5D5324** / `KLPRMAX` **0x5D5546** — +15 bar of headroom exists (19000 → 22000) and buys about **7.6 % more flow at the same `ti`**. A mixture-preparation measure, **not** a way to make fuel mass (rail.md §12.1) | `prsoll` id **501**, `prist` id **500**. Check: `prist` reaches `prsoll` at the new level at full load | `KLPRMAX` **0x5D5546** = 110.0 bar, and the **pump**: 0x80316E is clamped at `VMSVMX` **0x5D4BC6** = 5000 (rail.md §12.2). Log 0x80316E over DDLI before and after |
| `KFKSTT` **0x5C6E24**, `KFWKSTT` **0x5C6C7C**, `KFWKSTN` **0x5C6C50** — cranking quantity and its decay | `ksta` **0x803028** and `ksta_adapted` **0x80302C** over DDLI, with `tmst` id **80** | `TIMINP` again: a bigger injector plus a cold start is where the minimum pulse bites |
| **new:** adaptation channel **8** (0x7FD067) multiplies `ksta` by 0.50…1.10 and channel **4** (0x7FD065) multiplies the running mixture by the same range, both tester-writable and EEPROM-persistent (`calibration_names.md` §10.1) | 0x7FD067 / 0x7FD065 over DDLI | their own calibration limits **0x5C6086/0x5C6087** and **0x5C6082/0x5C6083** = 141/64. **A workshop "basic setting" resets them to 128** and silently changes the fuelling |

---

## 5. A different fuel (E85 / flex fuel) — this project

Separated from §4 in draft 2, because the two changes need different columns:
§4 changes the *hardware* that delivers fuel, this one changes the *fuel*.

| maps | logs | limits to watch |
|---|---|---|
| the `FFCAL001` block **0x5E2510** (`patches/ff_fuel/ffcal001.py`, 22 objects) — `ff_F_curve`, `ff_fst_map`, `ff_fzw_curve`, `ff_dzw_map`, `ff_prail_add` and the per-feature enable bytes, all defaulting to **off** | the patch's own measuring groups **111 / 108 / 69 / 109** (ids 2196-2199 / 2192-2195 / 2188-2191 / 2184-2187) and `logging/sessions/ff_fuel.json`. Check: `ff_magic` = 'FF01' and `ff_ticks` rises ~100/s | every feature's enable byte is 0 and its table neutral until the bench says otherwise |
| the `rk` hook (injection.md §3, the `mullw` at 0x0AC39C) — **this is where the ~30 % more fuel comes from**. §10.2 of `calibration_names.md` is the reason it has to: this dataset has **no stock enrichment to lean out** and `cand_KFMIXA` / `cand_KFMIXB` are λ = 1 everywhere | commanded lambda id **320** (0x802CDE) against sensor lambda id **45** (0x802BEA), and `fr_w` ids **29 / 28** (0x802DF8 / 0x802E00, **VCDS 001.3 / 001.4**). **Check: on a fuel change the *request* (320) must move and `fr` (29) must stay near 1.0.** If `fr` is doing the work, the patch factor is wrong | `fr` itself: the controller's authority is finite, and `frm` ids **33 / 34** (**VCDS 032.2 / 032.4**) will adapt the error away and hide it |
| **cold start**: `mix_801CF5_map` **0x5D3580** (12 × 12 over `tmst`, up to **+34 %**) is the warm-up enrichment of this software — the only non-neutral term in `mixture_running` — and `KFKSTT` / `KFWKSTT` act during cranking only | `ksta` 0x803028 and `mixture_running` **0x803020** (Q12, 4096 = 1.0) over DDLI, with `tmst` id **80**. Check: the E85 start needs more of both, and 0x803020 is where "more" shows up after start end | 0x803020 saturates at 0xFFFF = 16.0; long before that, `TIMINP` and the injector's linear range |
| **ignition**: the ethanol advance goes in at `zwgru_build` 0x41D38C (ignition.md §11) | ignition id **9**, `dwkrz` ids 88-93. Check: `dwkrz` must stay at 0 — if the knock controller is pulling timing, the advance is too much for the actual blend | `zwdelta_load` **0x7FD338** (new, §0) already spends up to +7.5 °CA cold; `%ZWMIN` limits retard, not advance, so **nothing in the stock software limits added advance** |
| **rail**: `ff_prail_add` on top of `KFPRSOLHOM` | `prsoll` id **501**, `prist` id **500**, pump volume 0x80316E | `KLPRMAX` 110.0 bar and `VMSVMX` 5000 — both hard |
| the stock low-octane detector `cand_KFSWKFZK` **0x5D5A3E** / `cand_KFSWKFZKR` **0x5D5AFE** / `cand_KFDZK` **0x5D597E** | `dwkrz` ids 88-93 and the detector state over DDLI | **with E85 it must never latch** (ignition.md §13.2) |
| — | the arbitrated charge limit id **2051** (0x80235A) | `cand_KLRLMXNRED` **0x5D7EAE**: if a flex-fuel calibration ever provokes the fault that arms 0x7FEA84, the engine loses **half its charge above 6000 rpm** (`calibration_names.md` §9.3) |

---

## 6. What not to touch without a separate argument

* `KLPRMAX` **0x5D5546** above 110 bar.
* `cand_KFZWMN` **0x5D5BCB** and the rest of the `%ZWMIN` family
  (`calibration_names.md` §4) — they limit retard, not advance, so raising
  them buys nothing and costs cat heating and the torque reserve.
* the `%NMAXMD` ladder — `NMAXGA` **0x5D7F46** = 26000 = **6500 rpm**,
  `NMAXOGGA` **0x5D7F5E** = up to 6700 rpm, `TMOTNMX` **0x5D7F2C** = 69.75 °C
  (`calibration_names.md` §5).
* anything in the level-2 monitoring (`%UFRKTI`, `%UFRKC`, `%UFZWC`,
  fr_index §7).
* the **adaptation channel limits** 0x5C607C-0x5C6093. They are what stops a
  tester writing an arbitrary fuel trim into the EEPROM.

None of these is a tuning knob; all of them are protections whose failure mode
is expensive.

---

## 7. What draft 2 still does not have

* **No row has been checked against an engine.** Every "check" above is a
  prediction.
* The *limits* column is complete only where a findings file established the
  clamp. Where it says "—" the honest statement is "no clamp was found", not
  "there is none".
* Component-protection enrichment: `calibration_names.md` §10.2 shows it
  cannot reach `rk` in this dataset, so no row watches an exhaust temperature.
  If `%ATM` is ever named (`FUN_00108950` is the candidate module), this
  document needs a row for it.
* The operating-mode index id **130** has eight values and nobody knows which
  is which. One drive log with 051.3 next to `rlsol_req` would settle it and
  would make the §3 row usable.
