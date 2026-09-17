# ff_fuel bench + road procedure, E5 half — the rail adder and the injection window (#36)

Brief **E5**, 2026-09-17. Read `procedure.md` first (the on-chip read-back test
of §1 is still the first thing to do on any of this), then this file. The E1
and E2 halves are independent of it: nothing here needs `ff_zw_enable`,
`ff_st_enable` or `ff_zwst_enable` to be anything but 0.

> **The hard stop of this whole procedure, before anything else.**
> `prist_w` (0x8031DA, VCDS id 500, group 109 field 2 as a minimum) must stay
> **above 2600 = 13.0 bar** at every single sample. Below `PRWBHMX`
> (0x5D3CDC = 2600) the ECU arms **three** interventions at once — the
> injection driver's hard cut-off angle, the injection-angle clamp and (with
> the fuel fault bit) a charge limit that falls to about 30 % at 6000 rpm —
> and it does so simultaneously, not gently (`re/findings/rail.md` §14.3,
> §14.4). If `prist` goes there, **stop the run, set `ff_prail_enable` back to
> 0 and reflash before the next start.**

## 0. What is new in the image, compared with E2

| | |
|---|---|
| **One more hook word** | 0x45845C in `hdrpsol_main`, the single store of `prsoll_raw` 0x8031F0. **On-chip**, so `make apply` now prints **seven** on-chip warnings instead of six |
| **FFCAL001 v4**, 332 B | `ff_prail_enable` (+0x122, **0**), `ff_prail_max` (+0x124, 3000 = 15.0 bar), `ff_diag_window_ms` (+0x126, 1000), `ff_prail_curve[17]` (+0x128, **all 0**) |
| **State block 76 B** | `msv_sat_ticks` +0x43, `prail_add` +0x44, `win_margin_min` +0x46, `prist_min` +0x48, `diag_ticks` +0x4A — the second checksummed range is now +0x40..+0x4B |
| **Measuring block 109** | `21 6D`; ids 2184-2187 |

Everything ships **inert twice over**: `ff_prail_enable` = 0 *and* an all-zero
`ff_prail_curve`. Either alone pins `prail_add` at 0.

---

# Part A — with the adder OFF (the shipped file)

Do all of Part A before you change one calibration byte. It is also the part
that is worth doing on a stock-behaviour flash, because **the three
diagnostics run whether or not the adder does** — they are the first look this
project has ever had at the injection window on a running engine.

## A1. Group 109, ignition on, engine off

```
VCDS → Engine 01 → Measuring Blocks → group 109      (KWP `21 6D`)
```

| Field | Reads | Expect with the shipped file |
|---|---|---|
| 1 | `ff_prail_add`, bar | **0.00 bar**, always |
| 2 | `ff_prist_min`, bar | the rail pressure with the pump off; a few bar or 0 |
| 3 | `ff_win_margin_min`, °CA | large and positive, or the +285.75 °CA saturation |
| 4 | `ff_msv_sat_ticks`, count | 0 |

"not available" on all four means the state block header does not check out —
look at `ff_magic` and `ff_src_seen` first (`procedure.md` §2), and read the
**stock** rail pressures from group 106 field 1 (`prist`) or group 231 fields
2 and 3 in the meantime.

Rehearse the whole answer with no ECU at all:

```bash
./.venv/bin/python3 logging/med9log.py groups --sim \
    --sim-dump work/ff_fuel.bin 109
```

## A2. The hook really is executing

`ff_diag_ticks` (state block +0x4A) counts the diagnostic window down at
100/s and reloads at 100. If it is stuck, the 10 ms tick is not running and
nothing in this file means anything.

The *consumer* is harder to see, because with `prail_add` = 0 it stores
exactly what the stock code would. The proof is indirect and it is the E0 run
below.

## A3. E0 equivalence run

Exactly `procedure.md` §4, with the rail variables added. Record the same
session on the stock image first:

```bash
./.venv/bin/python3 logging/med9log.py log \
    --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json \
    --bus gs_usb:0 --seconds 600 -o logs/e5_base.csv
```

and compare with `tools/logcmp.py -t patches/ff_fuel/test/tolerance.json`.
**`prsoll_raw`, `prsoll_clamped`, `prsoll_rate` and `prsoll_w` must match the
baseline sample for sample**, not within a tolerance: with `prail_add` = 0 the
stub stores the identical halfword, and `tests/test_ff_rail_patch.py` proves
that over 1225 operating points and every mode-bit path in the emulator.

## A4. Read the injection window, once, before touching anything

This is worth a dedicated 10-minute drive on the **stock** calibration,
because it is the number every later decision rests on and nobody has it yet:

* a full-throttle pull in 3rd from 2000 to 6500 rpm;
* a steady 120 km/h cruise;
* a hot restart and 30 s of idle.

From the log, per sample: `win_margin_min` (field 3 or the state block),
`dwi_inj_angle`, `wbho1s_w`, `ti_sum`, `nmot_w`, `rl_w`. Tabulate the
**minimum** margin per 500 rpm band. That table is the budget; §B3 spends it.

> `dwi` and `wbho1s` have **no stock measuring id at all** (`rail.md` §11), so
> before E5 this table could only be had over DDLI with hand-resolved
> addresses. Field 3 is the point of the whole diagnostic half.

---

# Part B — the adder ON (`ff_prail_enable` = 1)

## B1. The decision rules, before any calibration

Three signals, in this order of authority:

1. **`prist_w` > 2600 (13.0 bar), always.** The hard stop above. Watch
   `ff_prist_min` (field 2), which is the worst of the last second and will
   catch a dip a 1 Hz logger misses.
2. **"the pump is saturated": `vmsv_limited` (0x80316E) pinned at `VMSVMX` =
   5000**, i.e. `ff_msv_sat_ticks` (field 4) above 0 on a steady-state pull.
   The MSV volume request is `min(request, 5000)`; sitting at 5000 means the
   high-pressure pump has nothing left, **and no map change will help**
   (`rail.md` §12.2). A few counts in a transient are normal; a sustained
   count under steady load is the signal to stop raising the curve.
3. **`vhdp_spare` (0x8031F6) > 0.** The same statement one step earlier in the
   chain: zero spare volume means the setpoint can only *ramp*, so `prsoll`
   will lag and `prdiff` will sit positive — which over time risks arming the
   rail-pressure DTC path and with it the limp-home charge limit
   (`rail.md` §12.4).

And one sanity rule that costs nothing: **`prsoll_clamped` (0x8031F2) can
never exceed 22000 = 110.0 bar.** If it ever does, the patch is not doing what
this document says and everything below is void — the adder goes in *before*
`KLPRMAX`, and the clamp re-reads the cell from RAM, so this is a property of
the insertion point rather than of the calibration (`rail.md` §12.1).

## B2. One bench step, before a road run

With the engine idling warm and a real ethanol reading on the bus:

```bash
# +2 bar at E85 and nothing below E40, as a first step
./.venv/bin/python3 patches/ff_fuel/ffcal001.py \
    --set ff_prail_enable=1 -o build/ffcal001.bin --no-rows
```

(edit `ff_prail_curve` in `ffcal001.json` for the shape; `--set` only takes
scalars). Then `make gen && make apply`, flash, and check at idle:

* field 1 shows the bar the curve asks for at the current `e_filt`;
* `prsoll_raw` minus the stock baseline at the same operating point equals it;
* `prsoll_clamped` is unchanged if the map was already at `KLPRMAX`, and
  raised by the same amount if it was not — **both are correct**, and the
  second is the only case where the adder does anything;
* `prist_w` follows within a second or two and `prdiff` returns to ~0.

If `prist` does *not* follow at idle, stop: the pump is already the limit and
the rest of this procedure has no headroom to work in.

## B3. Calibrating `ff_prail_curve`, +2 bar at a time

The shipped ceiling is deliberate arithmetic, not a guess: `KFPRSOLHOM` tops
out at 19000 = 95 bar and `KLPRMAX` is 22000 = 110 bar, so **15 bar is the
entire headroom the stock calibration has**, and `ff_prail_max` ships at
exactly that (3000). The code clamps to 6000 = 30 bar whatever the block says,
and `KLPRMAX` eats anything above the headroom in any case.

The loop, at E85 (or whatever the tank holds — record `ff_e_filt` with every
step):

1. set `ff_prail_curve` to 0 below E40 and **+2 bar (400 counts)** at E85,
   linear in between, 0 at E0 (`ffcal001.py` refuses any other E0 value);
2. drive the §A4 pattern again;
3. accept the step only if **all four** of these hold over the whole run:
   * `ff_prist_min` > 13.0 bar at every sample;
   * `ff_msv_sat_ticks` = 0 on every steady-state sample;
   * `vhdp_spare` > 0;
   * `ff_win_margin_min` no worse than the §A4 table for that rpm band;
4. add another **2 bar** and repeat, up to the 15 bar the headroom allows;
5. stop at the first step where any of the four fails, and **go back one**.

Expect to run out of pump before you run out of headroom. That is the point of
field 4: +15 bar is +15.8 % pressure = **7.6 % more flow** at the same `ti`
(`rail.md` §12.1), while E85 wants 40 % more fuel *mass* — the mass comes from
`rk` and the F curve, and the rail raise is a mixture-preparation and
injector-duty measure. If the pump saturates at +4 bar, take +4 bar and stop;
the remaining headroom is not worth a lean transient.

> **Do not raise `KLPRMAX` (0x5D5546).** It is out of scope for this brief and
> for a reason: the sensor curve saturates around 138 bar (`rail.md` §2c) and
> the pump-volume limiter will rate-limit the setpoint instead of reaching it.
> Nothing above 110 bar is available without bench data about the pump.

## B4. The fault matrix — the rail half of #37

Drive each row with `logging/ethanol_frame_send.py` (settle at the blend
first, *then* apply the fault):

| Step | What | `ff_mode` | `ff_prail_add` | `ff_f_q10` |
|---|---|---|---|---|
| 1 | E85 steady, adder on | 1 OK | the curve value | rich |
| 2 | status byte = 2 | 2 HOLD | **unchanged** | held |
| 3 | status byte = 1 or 3 | 3 FAULT | **0, within one activation** | **held for 60 s** |
| 4 | Pico unplugged | 3 FAULT | **0** | held, then decays |
| 5 | Pico back, good frames | 1 OK | the curve value again | follows `e_filt` |

Steps 3 and 4 are the whole asymmetry of **#37** in one screen: the *fuel*
factor is held and then decayed, because a stale-rich mixture is safe; the
*rail* adder goes to zero on the activation the mode leaves OK/HOLD, with no
hold and no ramp, because a stale pressure demand is not. Field 1 of group 109
and field 2 of group 111 next to each other show both halves at once.

## B5. What must never happen

* `prist_w` below 13.0 bar — see the box at the top;
* `prsoll_clamped` above 22000;
* `ff_msv_sat_ticks` climbing on a steady cruise (not a transient);
* `ff_win_margin_min` going negative. That means the injection no longer fits
  the window. At normal rail pressure **nothing in the ECU enforces it**
  (`rail.md` §14.4) — the fuel is simply injected late, with whatever
  mixture-preparation penalty that carries, and the only thing standing
  between that and a problem is this number.

---

## 5. What this procedure does **not** cover

* **Raising `KLPRMAX`.** Out of scope until bench data about the pump exist
  (`rail.md` §12.3, §14.4).
* **A torque limiter that follows the window.** Out of scope, deliberately;
  the design note is §6 below.
* **The absolute meaning of display format 0x53.** Cross-checked against the
  ECU's own arithmetic (`measuring_vars.md` §7.3) but never against a known
  physical pressure. One VCDS log of group 106 against a gauge settles it; the
  patch does not depend on it, because fields 1 and 2 use the same halving the
  stock handlers use.
* **Whether the OBD route can write 0x45845C at all.** That is `procedure.md`
  §1 and brief E6. Seven of the eight hook words are now on-chip.

---

## 6. Design note (not code): a torque limiter on the injection window

`docs/05_flexfuel_design.md` §3.6 assumed the ECU would cut the throttle on a
window exceedance. **It does not** (`rail.md` §9.4, §14.4): at normal rail
pressure the whole window machinery is disarmed, and the charge limit at
0x803070 is `0xFFFF` unless the fuel-system fault bit 0x80201E & 0x20 is set.
So if we want a limit that follows the *window* rather than a *fault*, we have
to add it. This is what it would look like; it is **not implemented** and it
should not be until §A4's margin table exists.

**Where.** The min-chain at **0x0C7CF8**, which already reduces the arbitrated
charge limit 0x80235A from 0x803070 and feeds the final limit 0x80360E. One
extra `min()` there propagates to the throttle exactly the way the stock
protection limits do, with no new path and no DTC. Brief **E3** named the
other four inputs of that chain (`calibration_names.md` §9.3) and found them
almost all switched off in this dataset — `cand_KLRLMXMI`, `cand_KFRLMXBTS`,
`cand_KFRLMXBTS2` and `cand_KLRLMXN` are all 0xFFFF — so there is room, but
also a warning: the one calibrated limiter, `cand_KLRLMXNRED`, takes half the
charge above 6000 rpm once the debounced flag 0x7FEA84 is set. Anything added
here has to be logged against 0x802358 and 0x80235A.

**What.** `emu/models/window.py` already has the exact arithmetic: the stock
`rl_max_window` (0x454B28) turns the window into a relative-charge limit,
`((latest - margin + 280) * KVWBHRL) << 15 / k_nmot`. A flex-fuel version would
compute the same thing from the *measured* margin and the current `ti`, i.e.
"the charge at which `dwi` would eat the margin", and feed it into the chain
with a calibratable safety factor and a floor below which it never limits
(a limiter that can reach idle charge is worse than no limiter).

**Shape, following E1/E2/E5.** A `ff_rl_win_enable` byte defaulting to 0, a
u16 `ff_rl_win_min` floor, a producer in the 10 ms tick that writes one core
field, and a stub at the min-chain that reads it with the magic check. The
consumer is *not* segment-synchronous, so the same magic-only check the rail
stub uses is enough.

**Why not now.** Three reasons, in order. (1) There is no margin table yet —
§A4 produces the first one and without it every constant would be a guess.
(2) A charge limiter is the first thing this project would add that can
*reduce* torque unexpectedly, which is a different risk class from everything
in `ff_fuel` so far; it wants its own brief and its own fault matrix. (3) The
mixture-preparation argument of `rail.md` §9.5 may make it unnecessary: if the
margin never approaches zero on E85 at a sane `ff_prail_curve`, the right
answer is to write that down and not to add a limiter at all.

---

## 7. Logging session

`logging/sessions/ff_fuel.json` carries every variable this file names, with a
`patch_offset` on each `ff_*` entry so the addresses come out of `patch.json`
and cannot go stale:

```bash
./.venv/bin/python3 logging/med9log.py log \
    --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json \
    --bus gs_usb:0 --seconds 600 -o logs/2026-xx-xx_e5.csv
```

Its check 9 is this procedure's acceptance list in one place.
