# ff_fuel bench + road procedure, E1 half — the ignition blend (#34, the ignition rule of #37)

Brief **E1**, 2026-09-17. The third companion of `procedure.md`, which covers
the MVP itself (#32/#37); do **not** start here. §1 of `procedure.md` (the
on-chip read-back) and §2 (the state block) must both have passed, and
`procedure_d2.md` part A (measuring block 111) should have passed too, because
group 108 is read the same way and a failure there is not an ignition problem.

Written **before** any flash, as `docs/04_re_guidelines.md` §7 requires.
Nothing here has been run on an ECU. Every expectation is a prediction from
`tests/test_ff_ign_patch.py` and `emu/models/flexfuel.py`; the bench and the
road are what turn them into facts.

> Both blockers of `procedure.md` still apply — `"ram_status": "static"` and
> the undemonstrated KESSv2 write of the on-chip flash — and E1 makes the
> second one worse: the ignition hook word **0x41D40C is on-chip too**, so
> three of the four hook words now live in 0x404000-0x47FFFF. **Do not flash
> yet.**

## 0. What is new in the image, compared with D2

| | |
|---|---|
| **Flash** | the blob at 0x152000 is now 4,932 B (was 3,860) |
| **New hook word** | **0x41D40C**, `7C 63 52 14` (`add r3,r3,r10`) → `bl 0x152060`. On-chip; the third such word |
| **New flash edits** | 16 B at 0x0A7898 (`tbl_measuring_vars` ids 2192-2195) and four u16 at 0x5C55F0 / 0x5C57EE / 0x5C59EC / 0x5C5BEA (`tbl_measuring_groups` group 108) |
| **New RAM** | **none.** `dzw_e` and `fzw_q8` are the two fields D1 reserved at +0x29 and +0x2A of the state block; `.bss` is still 80 B of 256 and `ff_persist_buf` / `ff_nvm_req` did not move |
| **Calibration** | FFCAL001 is now **v2, 266 B**. `ff_zw_enable` = **0**, `ff_dzw_max` = 8, `ff_fzw_curve` = the docs/05 §3.4 ramp, `ff_dzw_map` = **all zero**, plus the two 8-point axes |
| **New stock RAM read** | `nmot_w` 0x7FEE74, `rl_w` 0x7FEFB2, `dwkrz` 0x7FCE57-0x7FCE5C, the low-octane latch 0x7FD31B |
| **New stock RAM written** | **none** |

A rehearsal of the whole E1 half without an ECU, which should be run first:

```bash
cd patches/ff_fuel && make check gen apply          # -> work/ff_fuel.bin
cd ../.. && ./.venv/bin/python3 -m unittest tests.test_ff_ign_patch
./.venv/bin/python3 logging/med9log.py groups --sim --sim-dump work/ff_fuel.bin 108
```

---

# Part A — with the blend OFF (the shipped file)

This part is an **equivalence test**, not a feature test. The whole point of
shipping `ff_zw_enable` = 0 is that the flashed file must be indistinguishable
from the D2 file as far as the engine is concerned, while the new hook word is
already in the flash and already executing on every ignition event.

## A1. Group 108, ignition on, engine off

VCDS → **Engine 01 → Measuring Blocks → group 108** (`21 6C` in session
`10 89`).

| Field | Shows | Formula | Expected |
|---|---|---|---|
| 1 | `f_zw`, the blend factor | 0x21, A = 100 → the value *is* B | **0 %** |
| 2 | `dzw_e`, the applied offset | 0x22, A = 0x4B → `0.75 × (B − 128)` | **0.00 °CA** |
| 3 | the worst of the six `dwkrz` | 0x22, A = 0x4B | **0.00 °CA** |
| 4 | the low-octane latch, `0x7FD31B & 3` | 0x36, a plain count | **0** |

Fields 1 and 2 read `---` ("not available") if the state block header is
wrong. That is the same failure `procedure.md` §2 diagnoses; go back there
rather than suspecting the ignition path. Fields 3 and 4 are **stock** cells
and answer whatever happens to our block, which is deliberate.

**Pass:** all four read as above. If field 1 or 2 is non-zero with the shipped
calibration, stop: either the flashed FFCAL001 is not the one in this tree
(check `ff_zw_enable` at 0x5E25F6 = 0x00) or the blob and the calibration
disagree.

## A2. The hook really is executing

The blend being 0 is not evidence that the stub runs; a hook word that KESS
silently refused to write would give the same reading. Two independent checks:

1. **The read-back.** `procedure.md` §1 already reads the flash back; extend
   its comparison window to include **0x41D40C** and confirm the word is
   `4B D3 4C 55`, not `7C 63 52 14`. This is the cheapest and the only direct
   proof.
2. **A deliberate non-zero offset.** Build a file with the blend enabled and
   one non-zero cell (§B1) and watch `zw` move. Do this on the bench, not on
   the road.

## A3. E0 equivalence run

Repeat `procedure.md` §5 (the E0 equivalence drive) unchanged and add
`zwist_display_b1` (0x7FEF87, VCDS group 003 field 4) to the comparison set if
it is not already there. Compare against the **stock** baseline with
`tools/logcmp.py` and `test/tolerance.json`.

**Pass:** the ignition angle traces are identical sample for sample within the
existing tolerance, exactly as before E1. They must be: with `dzw_e` = 0 the
stub adds zero and `zwgru` is bit-identical, which
`tests/test_ff_ign_patch.py::TestZwgruBuild` proves over the whole nmot/rl grid
on the applied image.

---

# Part B — with the blend ON

**Nothing in part B belongs on a public road until B2 has passed on the
bench.** Everything here adds ignition advance, which is the direction that
destroys pistons.

## B1. One cell, on the bench

Build a file with the blend enabled and a single non-zero cell, so that a
mistake can only affect one operating point:

```bash
cd patches/ff_fuel
./../../.venv/bin/python3 - <<'EOF'
import json, ffcal001
p = ffcal001.load_params(ffcal001.HERE / "ffcal001.json")
m = [0] * 64
m[4 * 8 + 6] = 2        # row 4 = 3720 rpm, column 6 = 93.8 % rl: +1.50 degCA
open("ffcal001_b1.json", "w").write(json.dumps(dict(p, ff_zw_enable=1,
                                                    ff_dzw_map=m), indent=2))
EOF
./../../.venv/bin/python3 ffcal001.py --json ffcal001_b1.json \
    -o build/ffcal001.bin --no-rows
make gen && make apply
```

`ff_dzw_map` is `val[row * 8 + column]` with **row = nmot** and
**column = rl** — the same order `interp_2d_s8` uses for `KFZW`. Rows are
520, 1000, 2000, 2920, 3720, 4520, 5520, 6520 rpm; columns are 10.2, 21.1,
31.3, 41.4, 52.3, 72.7, 93.8, 103.9 % `rl`.

On the bench, with the Pico feeding E85 (or `ff_mode` = 2 and
`ff_e_override` = 85), hold 3720 rpm at ~94 % load and read group 108:

```
field 1   ~100 %       f_zw is on its plateau from E50 up
field 2   +1.50 degCA  = 2 counts x 0.75
```

and group 003 field 4 (`zwist_display_b1`, 0x7FEF87) must be **1.50 °CA more
advanced** than the same point with `ff_zw_enable` = 0, all else equal.

**Pass:** field 2 shows +1.50 and the final angle moved by the same amount.
**Fail:** field 2 shows +1.50 but the angle did not move → the hook word was
not written (go back to A2 check 1) or a downstream limit is binding — check
`zwmin` and whether `dwkrz` is already non-zero at that point.

## B2. The fault matrix — the ignition half of #37

The rule this implements: **ignition goes to gasoline immediately.** There is
no hold and no ramp. Run each step and read group 108 field 2 and group 111
field 4 (the mode) together.

| Step | Do | `ff_mode` | Group 108 field 2 | Group 111 field 2 (F) |
|---|---|---|---|---|
| 1 | Pico running, E85, warm | 1 OK | the map value | > 100 % |
| 2 | **unplug the Pico's CAN** | 3 FAULT within 1.1 s | **0.00 °CA within one activation (10 ms)** | **unchanged — held for 60 s** |
| 3 | wait 5 s | 3 FAULT | 0.00 | still held |
| 4 | wait past 60 s | 3 FAULT | 0.00 | decaying at 2 %/s |
| 5 | plug it back in | 1 OK on the first good frame | back to the map value | recovering |
| 6 | Pico reporting status 2 (contaminated) | 2 HOLD | **frozen at the last value**, not 0 | frozen |
| 7 | Pico reporting status 1 or 3 | 3 FAULT | 0.00 | held |

**Step 2 is the acceptance criterion of the ignition half of #37**, and step 6
is the one that separates HOLD from FAULT: HOLD keeps computing from the
frozen estimate, so the offset stays, while FAULT drops it. Steps 2 and 6
cannot both be satisfied by "set it to zero on any fault", which is why they
are both here.

The 10 ms figure in step 2 is not observable with VCDS. What is observable is
that field 2 is 0 on the **first** reading after the unplug, with no
intermediate value — log it at 40 Hz with
`logging/med9log.py log --session logging/sessions/ff_fuel.json --patch
patches/ff_fuel/patch.json` and look at `ff_dzw_e` (0x7FFB29) rather than at
the measuring block.

## B3. Calibrating `dzw_E` on the road — the knock-logging recipe

The road half of #34 is calibration, not code, and it is the part this brief
deliberately does not do. The recipe:

**Log these, at the highest rate the logger gives** (they are all in
`logging/sessions/ff_fuel.json`):

| Variable | Address | Why |
|---|---|---|
| `dwkrz_worst` | 0x7FCE57-0x7FCE5C | **the acceptance signal**: must stay 0 |
| `wkrm` | 0x7FCE76 | the mean retard the stock low-octane detector watches |
| `zw_low_octane_latch` | 0x7FD31B | bits 0 and 1 **must never set** |
| `zwist_display_b1` | 0x7FEF87 | the final angle, to see the offset arrive |
| `ff_dzw_e` | 0x7FFB29 | what we asked for |
| `ff_fzw_q8` | 0x7FFB2A | the blend factor it was scaled by |
| `ff_e_filt`, `ff_mode` | 0x7FFB08, 0x7FFB0C | which estimate produced it |
| `nmot_w`, `rl_w` | 0x7FEE74, 0x7FEFB2 | **which cell** is being exercised |

**The loop, one cell at a time:**

1. start from `ff_dzw_map` all zero and a full tank of the blend you are
   calibrating for (E85 pump fuel is 75-81 % ethanol in practice, docs/05 §2);
2. pick a cell and raise it by **one count (0.75 °CA)**, never more;
3. drive that operating point in third or fourth gear, steady and then a
   full-load pull through it, twice;
4. read the log. **`dwkrz` at any cylinder anything but 0, or either latch bit
   in 0x7FD31B set → put the cell back and stop.** That cell is done;
5. otherwise repeat from step 2 until the cell reaches **+2 counts
   (1.50 °CA)**, which is where docs/05 §3.4 says to stop for a first pass;
6. only after a whole pass at +2 has held over several tanks should any cell
   go above it, and never above the budget in §B4.

Do the cells in order of **increasing** risk: high load first at *low* speed
is the worst place to be wrong, so start mid-load (columns 4-5) at mid-speed
(rows 3-5), then work outwards. Leave the two lowest-load columns at 0: §B4
shows there is nothing to gain there anyway.

## B4. The hard ceiling — `KFZWOP - KFZW` on this very grid

`KFZWOP` is the torque model's estimate of the optimum (MBT) angle and `KFZW`
is what the base map actually commands, so `KFZWOP - KFZW` is **the ECU's own
statement of how much advance it is giving up to knock** at that point. It is
the physical budget: a `dzw_E` cell above it is asking for more advance than
the ECU itself believes is useful, which is a calibration error whether or not
it knocks.

Evaluated on `ff_dzw_map`'s own 8 × 8 grid (reproduce with
`./.venv/bin/python3 -m emu.zw_model`, and see `re/findings/ignition.md` §12):

**Degrees CA:**

| rpm \ % rl | 10.2 | 21.1 | 31.3 | 41.4 | 52.3 | 72.7 | 93.8 | 103.9 |
|---|---|---|---|---|---|---|---|---|
| **520** | −6.75 | −1.50 | 3.00 | 5.25 | 7.50 | 12.75 | 17.25 | 18.75 |
| **1000** | −9.00 | −9.00 | −2.25 | 0.00 | 5.25 | 10.50 | 13.50 | 15.75 |
| **2000** | −0.75 | −2.25 | 0.00 | −0.75 | 1.50 | 9.00 | 13.50 | 15.75 |
| **2920** | −0.75 | −3.00 | −1.50 | −0.75 | −0.75 | 6.00 | 9.00 | 9.00 |
| **3720** | −1.50 | −3.00 | −3.00 | −0.75 | 1.50 | 6.00 | 9.00 | 9.75 |
| **4520** | −2.25 | −3.00 | −2.25 | −1.50 | 2.25 | 6.75 | 9.75 | 10.50 |
| **5520** | −3.00 | −5.25 | −4.50 | −3.00 | 0.00 | 6.00 | 8.25 | 9.00 |
| **6520** | −6.00 | −8.25 | −4.50 | −1.50 | 0.75 | 6.00 | 8.25 | 9.00 |

**The same in counts of 0.75 °CA, rounded towards zero — this is the number to
compare a map cell against:**

| rpm \ % rl | 10.2 | 21.1 | 31.3 | 41.4 | 52.3 | 72.7 | 93.8 | 103.9 |
|---|---|---|---|---|---|---|---|---|
| **520** | −9 | −2 | **4** | **7** | **10** | **17** | **23** | **25** |
| **1000** | −12 | −12 | −3 | 0 | **7** | **14** | **18** | **21** |
| **2000** | −1 | −3 | 0 | −1 | **2** | **12** | **18** | **21** |
| **2920** | −1 | −4 | −2 | −1 | −1 | **8** | **12** | **12** |
| **3720** | −2 | −4 | −4 | −1 | **2** | **8** | **12** | **13** |
| **4520** | −3 | −4 | −3 | −2 | **3** | **9** | **13** | **14** |
| **5520** | −4 | −7 | −6 | −4 | 0 | **8** | **11** | **12** |
| **6520** | −8 | −11 | −6 | −2 | **1** | **8** | **11** | **12** |

Read it like this:

* **A negative or zero entry means there is nothing to win.** At low load
  `KFZW` is already at or past the modelled optimum, so advance there buys no
  torque and only moves the engine towards knock. Leave those cells at 0
  permanently — that is 26 of the 64 cells, essentially the two lowest-load
  columns and most of the third and fourth.
* The useful region is the right-hand half, and it is widest exactly where
  E85's knock resistance is worth having: **8 to 14 counts (6.0-10.5 °CA) at
  72-104 % load above 2900 rpm**.
* The idle/low-speed rows read large (17-25 counts at 520 rpm) because the
  torque model's optimum is far away there; **do not believe them**. 520 rpm
  at 94 % load is not an operating point a car reaches, `ff_dzw_max` = 8
  clamps it anyway, and the knock margin at low speed and high load is the
  smallest on the whole map.

**Two ceilings are already in the file and neither is negotiable from the
calibration:** `ff_dzw_max` (shipped 8 counts = 6.00 °CA) and
`FF_DZW_HARD_MAX` = 16 counts = 12.00 °CA, which the patch clamps to whatever
FFCAL001 says. 16 counts is above every entry in the table except the four
low-speed cells the paragraph above says to ignore, so the code ceiling never
binds a sane calibration — it only bounds a corrupt one.

## B5. What the stock low-octane detector must never do

`zwgru_low_octane_detect` (0x0F436C) watches the **mean** knock retard 0x7FCE76
and, after `TSWZK` samples worse than `KFSWKFZK`, latches bit 0 of
**0x7FD31B** and applies the retard map `KFDZK` (0x5D597E) through 0x80208E —
which `zwbas_per_bank` adds on top of everything, including our offset
(`ignition.md` §13.2).

On E85 that latch is a **calibration failure, not a protection**: it means the
advance we asked for was too much often enough that the ECU decided the fuel
is bad. Group 108 field 4 is that latch, and §B3 step 4 stops on it.

If it ever latches: put the offending cells back to 0, clear it with a key
cycle (the latch lives in RAM plus 0x7F9424 bit 0), and do not raise anything
until a full tank has run without it.

---

## 5. What this procedure does **not** cover

* **The actual `dzw_E` values.** They come out of §B3 on a real car with real
  fuel; nothing in this repository can predict them, and #34's road half stays
  open until they exist.
* **The on-chip write.** `procedure.md` §1 answers it for all three on-chip
  words at once, including 0x41D40C.
* **The interaction with the start angle.** During the start `zwstt` (0x802096)
  **replaces** the whole per-bank angle (`start.md` §5), so the `zwgru` offset
  is bypassed and the blend is invisible until `B_stend` sets. That is brief
  **E2**'s insertion point Z1, not this one.
* **Rail pressure.** Brief **E5**.

## 6. Logging session

`logging/sessions/ff_fuel.json` carries every variable in §B3, with
`patch_offset` on the `ff_*` ones so the addresses come out of `patch.json`:

```bash
./.venv/bin/python3 logging/med9log.py log \
    --session logging/sessions/ff_fuel.json \
    --patch patches/ff_fuel/patch.json \
    --bus gs_usb:0 --seconds 300 -o logs/2026-xx-xx_ff_ign.csv
```
