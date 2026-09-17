# Which named maps matter per hardware change — draft (brief E3, issue #43)

Agent E3, 2026-09-17. **Everything here is HYPOTHESIS and a starting point
only.** It is a reading list, not a calibration procedure: nothing in it has
been tried on an engine, and issue #43 exists to turn it into one.

Read `re/README.md` ("Using it in TunerPro") first. Addresses are CPU
addresses; the XDF uses file offsets. Names are the ones in
`re/calibration_names.csv`; a `cand_` prefix means the Bosch label is a
candidate, not a fact. Nothing here overrides
`docs/04_re_guidelines.md` §6: work on a copy, fix the checksums, bindiff, and
nothing goes to the car that has not run on the bench spare.

## 0. The five objects every hardware change touches

| Object | Address | Why |
|---|---|---|
| `cand_KFMIRL` | 0x5C9372 | charge setpoint from torque setpoint. If the engine now makes more torque per unit of charge, this map and `cand_KFMIOP` are the pair that has to move together, or the torque structure will keep asking for the old charge |
| `cand_KFMIOP` | 0x5CA252 | torque from charge; the inverse of the above. The whole torque interface, the gearbox request and the level-2 monitoring read it |
| `KFZW` / `KFZWOP` | 0x5C75FE / 0x5CA3F1 | base ignition and the optimum-ignition reference. Any change to charge motion, exhaust back-pressure or fuel changes the knock limit |
| `KFPRSOLHOM` | 0x5D5324 | the rail-pressure setpoint the engine runs on in normal driving, capped by `KLPRMAX` 0x5D5546 = 110 bar (rail.md §12.1) |
| `KFKHFM` + `KFPU*` | 0x5C430D, 0x5C43EF … 0x5C4A1D | the air-mass sensor correction. Any change to the intake tract upstream of the HFM changes both the mean reading and its pulsation error |

Everything below is on top of those five.

## 1. Intake (filter, pipe, manifold, throttle body)

* **`KFKHFM` 0x5C430D** — the (nmot, rl) correction of the HFM signal, today
  all 128 = 1.0. A different intake tube diameter or a different screen in
  front of the sensor changes the calibration of the hot film; this is the map
  Bosch provides for exactly that.
* **`KFPU` … `KFPUKL123` 0x5C43EF/0x5C44D1/0x5C4859/0x5C45B3/0x5C4A1D/
  0x5C4777/0x5C493B/0x5C4695** — the pulsation correction, today ±5 %. A
  freer intake makes the pulsation *worse*, not better, and the error is
  worst at low speed and wide-open throttle, which is where the fuelling
  matters. Check `KFPUKL123` (all adjusters) first.
* `cand_MLDKFHFM` 0x5C4AF0 and `cand_NPULSHFMMN` 0x5C4AE4 — the filter of the
  same signal. Leave them alone unless a log shows the air mass ringing.
* **`cand_KFMIRL` / `cand_KFMIOP`** — more charge for the same throttle angle
  means the pair is no longer the inverse of reality.
* Watch: the charge ceiling `cand_RLSOLMX` 0x5C8C0C = 254 (99.2 %) clips the
  request, not the measurement.

## 2. Exhaust (manifold, cat, back pressure)

* **`KFZW` 0x5C75FE and `KFZWOP` 0x5CA3F1** — lower back pressure means less
  residual gas, which moves the knock limit *and* the optimum angle. Both, not
  one.
* **`cand_KFPSSRM` 0x5D1CBA** and the rest of `FUN_000E06F8` — the
  manifold/residual-gas chain. Its value unit is not established yet
  (`calibration_names.md` §9.6), so treat this as "read before you touch",
  not as a tuning target.
* **`KLPRMAX` 0x5D5546** stays where it is: an exhaust change is no reason to
  go above 110 bar (rail.md §12.1).
* Watch: the knock-control window `cand_NKRMN` 0x5D611C (3000 rpm) and
  `cand_DRLKR` 0x5D6124 decide where the stock "bad fuel" detector may look
  at all (ignition.md §13.2). A cat-back change should not move them.

## 3. Camshafts / valve timing

* **`KFZW`, `KFZWOP`, `cand_KFDZWKG` 0x5C753E** — different overlap, different
  internal EGR, different knock limit and a different optimum angle.
* **`cand_KFMIRL` / `cand_KFMIOP`** again, and this time the change is large:
  cam timing moves the volumetric efficiency at every point of the map.
* **`cand_KFRLSOLDY` 0x5C8FAE** — the step size with which the charge setpoint
  chases the request. More overlap makes the manifold slower; this is the map
  that decides how the setpoint ramps.
* **`KFPU*`** — the pulsation at the HFM is a function of valve events. Expect
  to re-measure them.
* Watch: `cand_KLRLMXNRED` 0x5D7EAE. It is the one charge limiter that is
  actually calibrated (100 % → 50 % between 3520 and 6520 rpm,
  `calibration_names.md` §9.3). It is armed by a fault flag, not by the cams,
  but a cam change that provokes that fault costs half the top-end charge.

## 4. Injectors (bigger HDEV, or a flex-fuel blend)

This is the one the rest of the project is about; `re/findings/injection.md`
§5-§6 and `docs/05_flexfuel_design.md` are the real documents.

* **`KRKATE` 0x5D3DBC = 3858** — the injector constant, the single scalar that
  a different static flow changes. Everything else in `%RKTI` is a shape.
* **`KLTIKRPR` 0x5C72F8** (flow vs. rail pressure, `k/sqrt(dp)`) and
  **`TVUB` 0x5C7310** (dead time vs. dp) — injector-specific, and both have to
  come from the new injector's data sheet, not from a guess.
* **`KLHDEV` 0x5C729C** — the linearisation of small injection times. A bigger
  injector's small-pulse behaviour is where the idle quality is won or lost.
* **`TIMINP` 0x5C7328 = 900 µs** — the minimum pulse. A bigger injector may
  need it raised; too large a value breaks `%ZGST` (fr_index §1.1).
* **`FKKVS` 0x5C71F8** — rail-pulsation correction over (ti, nmot). Different
  injectors, different pulsations.
* **`KFPRSOLHOM` 0x5D5324 / `KLPRMAX` 0x5D5546** — +15 bar of headroom exists
  (19000 → 22000) and buys about 7.6 % more flow at the same `ti`. It is a
  mixture-preparation measure, **not** a way to make fuel mass (rail.md
  §12.1).
* **`KFKSTT` 0x5C6E24, `KFWKSTT` 0x5C6C7C, `KFWKSTN` 0x5C6C50** — the cranking
  quantity and its decay. E85 needs far more of both when cold; this is where
  a flex-fuel start enrichment belongs (brief E2).
* Watch: the pump, not the map — `0x80316E` clamped at `VMSVMX` 0x5D4BC6 =
  5000 (rail.md §12.2). Log it before and after.
* Watch: the stock low-octane detector `cand_KFSWKFZK` 0x5D5A3E /
  `cand_KFSWKFZKR` 0x5D5AFE / `cand_KFDZK` 0x5D597E. With E85 it must never
  latch (ignition.md §13.2).

## 5. What not to touch without a separate argument

`KLPRMAX` above 110 bar, `cand_KFZWMN` 0x5D5BCB and the rest of the `%ZWMIN`
family (they limit retard, not advance — `calibration_names.md` §4), the
`%NMAXMD` ladder (0x5D7F46 etc., the 6500/6700 rpm limiter, §5), and anything
in the level-2 monitoring. None of them is a tuning knob; all of them are
protections whose failure mode is expensive.
