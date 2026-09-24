# Per modification type: which maps, which logs, which limits — draft 5 (issue #43)

Draft 1: agent E3, 2026-09-17, prose only. **Draft 2: agent F4, 2026-09-22** —
same content, restructured into the three columns issue #43 asks for, plus the
objects the naming passes added since. **Draft 3: agent G4, 2026-09-23** — the
objects of naming pass 4 (`calibration_names.md` §11) added to the rows they
belong to, marked **G4**, and four draft-2 statements corrected in place: the
rail-pressure mode maps were mislabelled (§11.4), 0x80223B is the **gear**, not
an operating mode (§11.7), `cand_KFPSSRM` is the exhaust-temperature map
`KFATMKRH` (§11.2, §11.6), and `gk_rk` has a **lambda-setpoint divisor** that
draft 2's "no stock enrichment" did not know about (§11.8). **Draft 4: agent
H1, 2026-09-24** — one row added, the **lambda request** in §5 (which stock
requester moves `lamsbg_w`, and how to see it in a log), G4's "unproven" in
the `rk` hook row replaced by the result (`calibration_names.md` §12: the
stock ECU *does* enrich — full load, component protection, cold start — down
to a floor of 0.700), and the §7 item settled. Marked **H1**. **Draft 5: agent
H5, 2026-09-24** — the objects of naming pass 5 (`calibration_names.md` §13):
the `%ATM` pipe segments and the exhaust locations behind the
component-protection thresholds (§2 gains the **limits for exhaust hardware
changes** #43 asks for), `KFLBTS` / `KFFDLBTS` / `KFDZWKG` settled, the
predicted protection shown dead, the vehicle-speed unit behind the gear
windows, and the tmot model `%GGTFM`. Marked **H5**.

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
**G4:** its x input 0x7FD3E5 is now known to be the **intake-air temperature**
`tans` (0.75 °C − 48; §11.1), so `zwdelta_7FD338_map` **0x5D5FFB** is the
hot-air ignition correction (−6.0 … +2.25 °CA over `tans` −24.75 … 80.25 °C and
speed). Log `tans` as id **85** (0x8021CC, **VCDS 004.4**) next to it.

| maps | logs | limits to watch |
|---|---|---|
| **G4:** the intake-air sensor chain — `tans_ntc_curve` **0x5D728F** (the NTC linearisation), `tans_subst` **0x5D7276** = 20.25 °C, `tans_plaus_min/_max` **0x5D7277/0x5D7278** = −45.0 / 138.75 °C. A sensor change is the only reason to touch them | id **85** (unfiltered, VCDS 004.4 / 006.3 / 011.3) and 0x7FD3E5 (filtered) over DDLI. Check: they agree in steady state, and the filtered value lags by the 0.04/activation of `tans_filter_k` **0x5D72B8** | the substitute: a plausibility fault (debounced 5 activations, `tans_debounce` **0x5D72A9**) silently replaces `tans` by 20.25 °C, which moves every `tans`-keyed ignition and charge term |

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
| **G4 (replaces draft 2's `cand_KFPSSRM` row):** `KFATMKRH` **0x5D1CBA** — the stationary exhaust-manifold temperature, **315 … 886 °C** over (speed, fuel mass); `KFATLAMS` **0x5D1C1E** (λ correction, 0.80 … 1.00) and `KFATZWMS` **0x5D1E4E** (ignition-retard correction, up to +53 %) on top. It is a **model**, not a sensor: an exhaust change makes it wrong, it does not recalibrate itself | no measuring id; over DDLI the model temperatures, all u16 K at **3/128 K per LSB** (°C = x·3/128 − 273.15). **H5 (corrects G4's cells, §13.1):** bank 1 / bank 2 `taikr_w` **0x801766 / 0x801750** (manifold), `tavvk_w` **0x8017B6 / 0x8017A8** (before the pre-catalyst), `tkivkm_w` **0x8017DE / 0x8017D8**, `tanvk_w` **0x801786 / 0x80177C**, `tavhk_w` **0x80179A / 0x801790**, `tkihkm_w` **0x8017D2 / 0x8017CC** (0x801764 is the stationary value before the valve lag, 0x8017AC the pre-catalyst inlet plus the cat-heating offset). Check against a thermocouple if one is fitted, never against the display (143 °C saturation) | `temp_exh_max_5D179E` **0x5D179E** = **1000.0 °C** caps the block; see the next row for what the block does with it |
| **G4:** the cat-heating block `FUN_000E0A0C` / `FUN_000E06F8` evaluates `KFATMKRH` a second time at the charge `cand_KFMIRLINV` **0x5C9938** makes of `rl` (a relative charge, **100/4096 %/LSB**, §11.2) and at that charge divided by an ignition efficiency (0x8015AF, 200 = 1.0), and turns the result back into a charge 0x8015CC and a torque 0x8015C6 through `cand_KFMIOP` | 0x8015CC and 0x8015C6 over DDLI; 0x80156F bit 0 (homogeneous cat heating, HYPOTHESIS) | **HYPOTHESIS: a component-protection charge/torque limit.** Whether 0x8015C6 limits anything in normal running was not followed. Log it on any exhaust change |
| **G4:** the main-catalyst model — `TAVHKEMN` **0x5D1EA6** = 230.0 °C light-off, `cand_FATMEHK` **0x5D18F6** (+76 … +135 K exotherm), `FEXOLAHK` **0x5C6A0C** (0.70 at λ 0.70 → 1.00); the pre-catalyst terms (`EAVK*`, `MATMAVK/BVK`, `FEXOLAVK`) are all neutral, i.e. **no pre-catalyst is modelled** | 0x80179A over DDLI | a different catalyst (metal / sports cat) means different masses and light-off — `MATMAHK/BHK` **0x5D1E98 / 0x5D1E9E** (**H5**: labels settled), `TAVHKEMN` |
| **H5 — the pipe between the models (§13.1).** Each segment is a gas-to-wall loss `FATMA*` (over the exhaust mass flow), a wall heat capacity `KATMCP*`, a wind loss `FATMV*` (over `vfzg_w`) and an initial-wall factor `FATMTW*`: manifold `FATMAKR` **0x5D1AB0** / `KATMCPKR` **0x5D1BEE**, pipe before the main catalyst `FATMAHK` **0x5D1A8E** / `KATMCPHK` **0x5D1BEC**, pipe after it `FATMANHK` **0x5D1AD2** / `KATMCPNHK` **0x5D1BF0**. **Four segments are applied dead** (`FATMARO`, `FATMAVK`, `FATMANVK`, `FATMAVY` all zero). A new manifold, downpipe or catalyst position changes exactly these; with the dead ones the model has no pipe between manifold and pre-catalyst at all | the location cells of the row above, against a thermocouple at the same place. Check: the model must read **high**, never low — the FR APP asks for that | a wrong model moves every threshold of the next row |
| **H5 — the limits for exhaust hardware (§13.3).** Component protection (`lambts_w`, the enrichment of §5) arms when any location exceeds its threshold: **`TAIKRBTS` 0x5D18AE, `TAVROBTS` 0x5D18B8, `TAVVKBTS` 0x5D18BA = 875 °C** (manifold, front pipe, pre-catalyst inlet; after `TVLBTSVVK` **0x5D18D7** = 5 s), **`TAVHKBTS` 0x5D18B4 = 900 °C** (before the main catalyst), **`TANVKBTS` 0x5D18B2, `TKIVKBTS` 0x5D18CC, `TKIHKBTS` 0x5D18C8 = 1050 °C** (after the pre-catalyst and inside both catalysts; `TVLBTS` **0x5D18D6** = 0 s); **`TAVVKBTSW` 0x5D18BE** = 875 °C removes the delay and **`TAVVKBTSH` 0x5D18BC = 905 °C** applies full enrichment at once; hysteresis `DTBTS` **0x5C693A** = 60 K | `lambts_w` id **377**, the weight 0x8015FC and the location cells over DDLI, id **410** bit 6. Check: on a sustained pull, which location crosses first — that is the one a hardware change must be judged against | the thresholds are the hardware's rating, not tuning knobs: raise them only with the part's own data. The overrun cut-off limits `…SAO` (0x5D18B0 … 0x5D18CE) are all 65535, i.e. never limiting, and `TAVVKBTSP` **0x5C693C** = 65535 switches the **predicted protection off** (the 0x5C68A2 map never applies) |
| `KLPRMAX` **0x5D5546** stays at 110 bar: an exhaust change is no reason to raise it | `prist` id **500** | itself |
| the stock low-octane detector `cand_KFSWKFZK` **0x5D5A3E**, `cand_KFSWKFZKR` **0x5D5AFE**, `cand_KFDZK` **0x5D597E** | `dwkrz` ids 88-93 and, over DDLI, the detector's own state | the knock window `cand_NKRMN` **0x5D611C** (3000 rpm) and `cand_DRLKR` **0x5D6124** decide where it may look at all. A cat-back change should not move them |
| **new:** the knock block's own enable, `TKRBB` **0x5D6122** = 130 → 49.5 °C, and `CW_KRKE_BB` **0x5D611A** = 0 | `tmot` id **80** (VCDS 001.2) — but see the 143 °C saturation warning | `CW_KRKE_BB` = 0 means a **knock-sensor fault alone arms 0x7FEA84**, which arms `cand_KLRLMXNRED` — §3 |

---

## 3. Camshafts / valve timing

| maps | logs | limits to watch |
|---|---|---|
| `KFZW`, `KFZWOP`, `dzw_kg_weighted_map` **0x5C753E** (**H5**: renamed from `cand_KFDZWKG`, it is not the lambda map; clamped by `dzw_kg_weighted_max` 0x5C753D = 0, so it can only retard) — different overlap, different internal EGR, different knock limit | ignition id **9**, `dwkrz` ids 88-93 | `%ZWMIN` (§5) |
| `cand_KFMIRL` / `cand_KFMIOP` — cam timing moves volumetric efficiency at every point, so this is a large change | ids **8** and **375** | `cand_RLSOLMX` **0x5C8C0C** |
| `cand_KFRLSOLDY` **0x5C8FAE** — the step size with which the charge setpoint chases the request. More overlap, slower manifold | `rlsol_w` **0x803508** and `rlsol_req` id **375** over DDLI. Check: no overshoot on a throttle step | `cand_DRLSOLMX` **0x5C91F0** = 1.00 %/step, the per-activation ceiling in `%MDFUE` |
| `KFPU*` — the pulsation at the HFM is a function of valve events; expect to re-measure | raw HFM **0x7FEF9E** | — |
| **new:** the six `rl_*` thresholds of `rl_thresholds_per_mode` 0x0C7DD0 (**0x5D9018, 0x5D90D8, 0x5D9138, 0x5D9198, 0x5D9010, 0x5D9216**) are maps over (engine speed, **gear** — **corrected by G4**: 0x80223B is `gangi`, 0 = none, 1 … 6, 7 = reverse, §11.7) | the gear, id **130** (0x80223B, **VCDS 051.3 / 068.3**). Check: the logged gear matches the lever | **G4:** the gear itself comes from the n/v windows `NVQUOT1O` … `NVQUOT6U` **0x5D77F0 … 0x5D7806**; a **different final drive or tyre size moves n/v out of them** and the ECU then reports gear 0. **H5 (§13.5):** `vfzg_w` 0x802260 is 1/128 km/h (id **86**), so the windows read in rpm per km/h: 200/76, 109/51, 65/40, 47/32, 36/27, 29/15 for gears 1 … 6 — scale them by the ratio change |
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
| `KFPRSOLHOM` **0x5D5324** / `KLPRMAX` **0x5D5546** — +15 bar of headroom exists (19000 → 22000) and buys about **7.6 % more flow at the same `ti`**. A mixture-preparation measure, **not** a way to make fuel mass (rail.md §12.1). **G4:** after a cold start the setpoint is **`KFPRSOLKH` 0x5D53A4** (catalyst heating, 60 … 95 bar), not `KFPRSOLHOM` — draft 2 and rail.md §3.2 had that map as `KFPRSOLHMM`; the four non-homogeneous labels were swapped (§11.4) | `prsoll` id **501**, `prist` id **500**. Check: `prist` reaches `prsoll` at the new level at full load | `KLPRMAX` **0x5D5546** = 110.0 bar, and the **pump**: 0x80316E is clamped at `VMSVMX` **0x5D4BC6** = 5000 (rail.md §12.2). Log 0x80316E over DDLI before and after |
| `KFKSTT` **0x5C6E24**, `KFWKSTT` **0x5C6C7C**, `KFWKSTN` **0x5C6C50** — cranking quantity and its decay | `ksta` **0x803028** and `ksta_adapted` **0x80302C** over DDLI, with `tmst` id **80** | `TIMINP` again: a bigger injector plus a cold start is where the minimum pulse bites |
| **G4:** the canister purge fuel `rkte_w` **0x80315C** is *subtracted* from `rk` by `gk_rk`, clamped to `FRKTEMN` **0x5D4888** … `FRKTEMX` **0x5D488A** = −0.08 … +0.50 × `rk`; the purge valve opening is limited by `KFFTEVFX` **0x5D479E** (§11.5). A bigger injector changes nothing here, but purge is a disturbance to separate from injector errors | `rkte` id **171** (formula 0x14, %), with `fr` ids **29 / 28**. Check: compare injector-change logs with purge inactive (id 171 at 0) | the clamp itself: up to half the fuel may come from the canister |
| **new:** adaptation channel **8** (0x7FD067) multiplies `ksta` by 0.50…1.10 and channel **4** (0x7FD065) multiplies the running mixture by the same range, both tester-writable and EEPROM-persistent (`calibration_names.md` §10.1) | 0x7FD067 / 0x7FD065 over DDLI | their own calibration limits **0x5C6086/0x5C6087** and **0x5C6082/0x5C6083** = 141/64. **A workshop "basic setting" resets them to 128** and silently changes the fuelling |

---

## 5. A different fuel (E85 / flex fuel) — this project

Separated from §4 in draft 2, because the two changes need different columns:
§4 changes the *hardware* that delivers fuel, this one changes the *fuel*.

| maps | logs | limits to watch |
|---|---|---|
| the `FFCAL001` block **0x5E2510** (`patches/ff_fuel/ffcal001.py`, 22 objects) — `ff_F_curve`, `ff_fst_map`, `ff_fzw_curve`, `ff_dzw_map`, `ff_prail_add` and the per-feature enable bytes, all defaulting to **off** | the patch's own measuring groups **111 / 108 / 69 / 109** (ids 2196-2199 / 2192-2195 / 2188-2191 / 2184-2187) and `logging/sessions/ff_fuel.json`. Check: `ff_magic` = 'FF01' and `ff_ticks` rises ~100/s | every feature's enable byte is 0 and its table neutral until the bench says otherwise |
| the `rk` hook (injection.md §3, the `mullw` at 0x0AC39C) — **this is where the ~30 % more fuel comes from**. §10.2 of `calibration_names.md` is the reason it has to: `cand_KFMIXA` / `cand_KFMIXB` are λ = 1 everywhere. **G4 correction (§11.8):** `gk_rk` *does* divide the fuel mass by a lambda setpoint, `lamsbg_w` **0x80304A** (4096 = 1.0, from `eta_coordinator` 0x442C18). **H1 (§12):** and it goes below 1.0 — the patch factor multiplies a stock enrichment whenever one is active (next row, and `docs/05` §3.3 note of 2026-09-24); "no stock enrichment" is **disproven** | commanded lambda id **320** (0x802CDE) against sensor lambda id **45** (0x802BEA), and `fr_w` ids **29 / 28** (0x802DF8 / 0x802E00, **VCDS 001.3 / 001.4**). **Check: on a fuel change the *request* (320) must move and `fr` (29) must stay near 1.0.** If `fr` is doing the work, the patch factor is wrong. **G4:** add the lambda setpoint, id **43** (0x80304A, formula 0x1F like id 45) — it must read 1.000 on the bench; anything else is a stock enrichment the patch factor sits on top of. **H1:** compare 320 against 43, not against 1.0: the controller's setpoint *is* `lamsbg_w` | `fr` itself: the controller's authority is finite, and `frm` ids **33 / 34** (**VCDS 032.2 / 032.4**) will adapt the error away and hide it |
| **H1 — the lambda request** (`calibration_names.md` §12). `%LAMKO` (`eta_coordinator` 0x442C14) min-selects the stock requesters into `lamsbg_w`, which `gk_rk` divides `rk` by from start end on: **full load** `cand_LAMFA` **0x5D34D8** (1.000 at ≤ 100 % charge request; 0.891 at ≥ 110 % and 6520 rpm), **component protection** `KFLBTS` **0x5C6636** / `KFLBTS2` **0x5C66F6** (0.820 / 0.836 at 7000 rpm, only above the thresholds of §2 — **H5**: labels settled, with `KFFDLBTS` **0x5C6576** weighting the ignition-driven delta) with the predicted-protection map **0x5C68A2** (0.730 — **H5: dead on this dataset, `TAVVKBTSP` = 65535**) and the rich limit map **0x5C67B6**, **after-start** `KFLANS` **0x5C6F36** (0.801 at −30 °C, 1.000 above +10 °C), catalyst clear-out 0x5D3430 (0.950) and the diagnoses. Leaning one of them out moves `rk` by 1/λ; `F(E)` multiplies the result | `lamsbg_w` / `lamsbg2_w` ids **43 / 44**, `lamfa_w` id **376**, `lambts_w` / `lambts2_w` ids **377 / 1555** (all formula 0x1F), the requester status id **410** (bit 2 B_kh, bit 4 B_lamka, bit 5 B_lamnswl, bit 6 B_lambts, bit 7 the `lamfa` charge-limit inhibit) and id **411** (bit 0 bank cut-off, bit 1 `B_lalgf`), `rlsol_req` id **375**, `bdemod_w` id **503**; over DDLI (`tuning_checklist.json`) `lamnswl_w` 0x803058, `lamka_w` 0x801CC6, `lamdiag_w` 0x801D2C, the protection weight 0x8015FC and the `%ATM` temperatures 0x8017A8 / 0x80179C. **Check: on a WOT pull the one requester whose id moves is the one whose status bit is set, and `lamsbg_w` equals the minimum of them; in a catalyst-heating phase `lamsbg_w` stays 1.000 even with bit 2 (B_kh) set** — the cat-heating lambda is not selected on this dataset (§12.2). A `lamsbg_w` of **0.700** with no requester set and id 411 bit 1 (`B_lalgf`) clear means `bdemod_w` bit 0 is clear (§12.1); DDLI `lamhsbg_w` 0x803046 then differs from `lamsbg_w` | the `%LAMKO` rich limit **0x5C54E0 = 0.700** (lean limit 0x5C54E2 = 1.200) bounds every request; and the **injection window**: the fuel it has to carry is `F(E) / lamsbg_w` — 1.73 at E85 full load, 1.88 under `KFLBTS` (`docs/05` §3.3, 2026-09-24). Judge `win_margin_min` (group 109) at the lowest `lamsbg_w` of the pull |
| **cold start**: `mix_801CF5_map` **0x5D3580** (12 × 12 over `tmst`, up to **+34 %**) is the warm-up enrichment of this software — the only non-neutral term in `mixture_running` — and `KFKSTT` / `KFWKSTT` act during cranking only. **G4:** the start ignition's 1-D term `cand_KLZWSTT` **0x5C7BAB** is over the **intake-air temperature** (axis −18 … 132 °C), all zero; it is the natural place for a cold-air E85 start-advance term. After start the rail setpoint is `KFPRSOLKH` **0x5D53A4** (cat heating), not `KFPRSOLHOM` | `ksta` 0x803028 and `mixture_running` **0x803020** (Q12, 4096 = 1.0) over DDLI, with `tmst` id **80**. Check: the E85 start needs more of both, and 0x803020 is where "more" shows up after start end | 0x803020 saturates at 0xFFFF = 16.0; long before that, `TIMINP` and the injector's linear range |
| **H5 — the lambda-dependent ignition term:** `KFDZWKG` **0x5C76D5** (renamed from `dzw_lambas_map`, §13.4) adds +7.5 … +8.25 °CA at λ 0.65 and nothing at λ = 1, keyed by the mean lambda setpoint. Every stock enrichment of the row above therefore also advances the ignition; an E85 calibration that runs richer on the same request moves this map | ignition id **9** with `lamsbg_w` id **43** | nothing limits the added advance (next row) |
| **ignition**: the ethanol advance goes in at `zwgru_build` 0x41D38C (ignition.md §11). **G4:** the exhaust model sees the result through `KFATZWMS` **0x5D1E4E** — more advance, higher efficiency, cooler modelled exhaust — so an E85 advance *lowers* the model's exhaust temperature by itself | ignition id **9**, `dwkrz` ids 88-93. Check: `dwkrz` must stay at 0 — if the knock controller is pulling timing, the advance is too much for the actual blend | `zwdelta_load` **0x7FD338** (new, §0) already spends up to +7.5 °CA cold; `%ZWMIN` limits retard, not advance, so **nothing in the stock software limits added advance** |
| **rail**: `ff_prail_add` on top of `KFPRSOLHOM` | `prsoll` id **501**, `prist` id **500**, pump volume 0x80316E | `KLPRMAX` 110.0 bar and `VMSVMX` 5000 — both hard |
| the stock low-octane detector `cand_KFSWKFZK` **0x5D5A3E** / `cand_KFSWKFZKR` **0x5D5AFE** / `cand_KFDZK` **0x5D597E** | `dwkrz` ids 88-93 and the detector state over DDLI | **with E85 it must never latch** (ignition.md §13.2) |
| **G4:** purge fuel `rkte_w` **0x80315C** is modelled as gasoline vapour; with E85 in the tank the canister gas is ethanol-rich | `rkte` id **171** against `fr` ids **29 / 28** during purge | `FRKTEMX` **0x5D488A** = 0.50 × `rk` |
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
* **H5:** the coolant-temperature diagnosis and models (`%GGTFM`,
  `calibration_names.md` §13.6) — `TMDMN` / `TMDMX` **0x5D7355 / 0x5D7356**
  (−45 / 138.75 °C), the step and stuck checks, the thermostat monitor. A
  different thermostat or radiator shows up here as a diagnosis, not as a
  tuning need; nothing in it depends on the fuel.
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
* ~~Component-protection enrichment: draft 2 said it cannot reach `rk`.~~
  **SETTLED 2026-09-24 (H1, `calibration_names.md` §12): it reaches `rk`
  through `lamsbg_w`, as `lambts_w` 0x80160C / 0x801608 from `%LAMBTS`
  (`FUN_0042EF0C`), armed by the `%ATM` temperatures at 875 °C; the §5
  lambda-request row has the logs.** The earlier text, for history:
  draft 2 said it cannot reach `rk`.
  **G4:** `%ATM` is named now (`FUN_001043C8`, not `FUN_00108950`; §11.6) and
  §2 has rows for it, and `gk_rk` divides by the lambda setpoint 0x80304A
  (§11.8). What is still missing is the link between the two: which input of
  `eta_coordinator` (0x803058, 0x801CC6 / 0x801CC4 are the candidates) is the
  temperature-driven enrichment request, and whether it is calibrated at all.
  Until then, log id **43** on every run.
* ~~The operating-mode index id **130** has eight values and nobody knows which
  is which.~~ **Settled by G4 (§11.7): id 130 is the gear.**
* **H5:** the heat capacities `KATMCP*` and the wind-loss tables `FATMV*` have
  no derived unit, and the `FATMV*` tables have no XDF entry (no draft row,
  `calibration_names.md` §13.1) — an exhaust re-calibration of the model is
  a bench/thermocouple job this desk work cannot replace.
* **G4:** the objects this draft adds are desk results like the rest; the
  temperatures of §2 are a model's, and none of the `tans` / `%ATM` / `%TEB`
  rows has been compared with a measurement.
