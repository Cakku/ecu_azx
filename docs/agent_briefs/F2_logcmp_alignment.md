# Brief F2 — `tools/logcmp.py`: align two runs on the ECU's own clock, derive tolerances from two stock runs (#42 desk gap, #32 E0 recipe, #27 compare step)

Brief E4 found the missing line of every log comparison in this project:
two runs are two power-ups, and the tens of milliseconds between "the ECU
powered on" and "the tester finished the DDLI set-up" differ every time. On
an rpm ramp that offset alone eats a third of the `nmot_w` tolerance and says
nothing about the software. E4 fixed it inside `logging/bench_rehearsal.py`
(`_align_on_raster`) and E7 recorded that `tools/logcmp.py` itself still
cannot do it (`docs/agent_briefs/README.md`, "Open after wave E"). This brief
lifts the implementation into the tool every bench procedure points at, and
adds the second thing those procedures assume but no command provides: a
tolerance file derived from two stock runs. Wave F pair 2, parallel with F3.
Small brief; a filler. Desk work only.

Read `00_common_rules.md`, `tools/logcmp.py`, `tests/test_logcmp*.py` (if
present; otherwise the logcmp cases in `tests/`), `logging/bench_rehearsal.py`
(`_named_only`, `_align_on_raster`, `compare_e0`), `logging/README.md` §9,
`docs/07_workflow.md` §5, `patches/ff_fuel/test/procedure.md` §4 and
`test/tolerance.json`, `patches/ff_counter/test/procedure.md` §4 and
`test/tolerance.json`, `logging/sessions/ff_fuel.json` (the raster counter
variables), `tools/README.md`, `logging/make_samples.py` (synthetic logs).

## Facts
- `logcmp.py` today: `baseline candidate [-t tolerance] [--json OUT]
  [--strict] [-q]`; `load_log`, `load_tolerances`, `compare` are importable.
  No alignment, no way to say "compare only the variables the file names",
  no way to produce a tolerance file.
- `bench_rehearsal.py::_align_on_raster(base, cand)`: uses
  `raster_setA_10ms_count` (the live 10 ms raster, 100/s,
  `scheduler.md` §11); shift = `(c.v[0] − b.v[0]) / 100 − (c.t[0] − b.t[0])`
  seconds, applied to every candidate series; returns `(log, shift)`.
  `_named_only` restricts a log to the variables `tolerance.json` names,
  because stock-image logs of the `ff_fuel` session carry `ff_*` variables
  that read 0 and would otherwise fail on the default limit.
- The rehearsal passes 69/69 on `main` (2026-09-17 integration note in
  `logging/README.md` §9). It must still pass when it uses the tool.
- Brief C1's rule for Flash 1 (#27): tolerances are predictions until
  retightened against **two stock runs**; `procedure.md` §4 of both patches
  repeats it. There is no command for that step.
- Sessions carry free-running counters (the raster counters, `ff_ticks`)
  that can never be compared between runs and must be excluded explicitly,
  not compared with a default limit (E4's #37 comment).

## Tasks
1. **`--align-on VAR[:RATE]`** in `logcmp.py` (e.g.
   `raster_setA_10ms_count:100`): shift the candidate as `_align_on_raster`
   does, print the shift in the text report header and in the JSON summary,
   fail loudly if the variable is missing in either log. Add
   `--align-shift SECONDS` for a manual value. Default: no alignment.
2. **`--uncovered {fail,report,ignore}`** (default `fail`, today's behaviour):
   what happens to variables the tolerance file does not name. `report` is
   what `_named_only` implements; list them in the summary.
3. **`derive` sub-command** (or `--derive-tolerance`): from two baseline logs
   of the same procedure, aligned as in task 1, write a tolerance file with
   per-variable limits = max |d| over the common time range × a factor
   (default 1.5, `--factor`), excluding a `--exclude` list (raster counters,
   `ff_ticks`) which it writes into the file as explicit "not compared"
   rows. Keep the existing tolerance-file format so the patches' files
   still load; document the new keys in the module docstring.
4. **Make `bench_rehearsal.py` use the tool**: delete its private
   `_align_on_raster` and `_named_only`, call `logcmp`'s implementation,
   keep `--fresh-eeprom` at 69/69. Do not change what the rehearsal checks.
5. **Tests**: synthetic logs (`logging/make_samples.py` or in-test) with a
   known power-up offset — the comparison fails unaligned and passes
   aligned; `derive` reproduces known spreads; `--uncovered` modes; the
   file round-trips through `load_tolerances`.
6. **Docs**: `docs/07_workflow.md` §5 (the align and derive steps, with real
   command output as E7 did), `logging/README.md` §9 (one dated line),
   `patches/ff_fuel/test/procedure.md` §4 and
   `patches/ff_counter/test/procedure.md` §4 (the exact commands; you may
   edit these two sections only, and only if F1 has been merged — otherwise
   put the wording in the report for the integrator), `tools/README.md`
   row. Comment on #42 and #32; commit after every task.

## Acceptance
`logcmp.py` aligns, derives and reports uncovered variables from the command
line; `bench_rehearsal.py --fresh-eeprom` is 69/69 using the tool's code;
the E0 recipe in `docs/07` §5 is three commands with quoted output; suite
green; `data/passat_azx_ori.bin` untouched.
