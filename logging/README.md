# logging/ — log format and regression comparison

The bench and drive logs that prove a patch changed nothing it should not
(`docs/06_patch_pipeline.md` section 7, `docs/05_flexfuel_design.md` E0
equivalence test). `tools/logcmp.py` reads this format.

## 1. The CSV format (long form) — the format loggers must write

```csv
# session: bench idle + load step, 2026-09-15
# ecu: 03H906032 / 1037382557
# dump_sha256: b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3
# transport: KWP2000 0x2C/0x21 over TP2.0
time_s,var,value,unit
0.000,nmot_w,780.1664,rpm
0.000,rl_w,17.0552,%
0.050,nmot_w,785.9819,rpm
```

Rules:

| Field | Meaning |
|---|---|
| `time_s` | seconds since the start of the log, decimal, non-negative. One sample per row; rows for the same timestamp may appear in any order. |
| `var` | variable name. Bosch Funktionsrahmen name when confident (`nmot_w`, `rl_w`, `ti_1_w`), otherwise the `re/symbols.csv` name, `cand_` prefix while unconfirmed (`docs/04_re_guidelines.md` section 3). |
| `value` | decimal number, `.` as the separator, no thousands grouping, no unit suffix. Enumerations and bit flags are logged as their numeric value. |
| `unit` | optional 4th column, free text, metadata only — `logcmp` ignores it. |

* Lines starting with `#` are comments. `# key: value` lines before the header
  become the log's metadata; record at least `ecu`, `dump_sha256` and
  `transport` so a log can be tied to the exact software it came from.
* Variables are sampled independently, at whatever rate the transport gives
  (KWP `0x2C`+`0x21` manages roughly 40 samples/s **in total**, so a set of six
  variables lands near 6 Hz each). The long form is the format precisely
  because per-variable rates differ and the set changes between sessions.
* One file per session. No interpolation, no gap filling, no re-ordering by the
  logger — write the samples as they arrive.

### Wide form

`tools/logcmp.py` also accepts `time_s` followed by one column per variable
(what most VCDS/CSV exports produce), and converts it on load. Empty cells are
skipped. Write the long form for new loggers; the wide form exists so that
third-party exports can be compared without conversion.

## 2. Comparing two logs

```bash
python3 tools/logcmp.py baseline.csv candidate.csv -t tolerance.json
python3 tools/logcmp.py baseline.csv candidate.csv -t tolerance.json --json report.json
```

For every variable present in **both** logs, the candidate is resampled onto
the baseline's timestamps inside the overlapping time window and the deviation
`candidate - baseline` is summarised as mean, mean-absolute, max-absolute (with
the time it occurred) and RMS. Variables present in only one log are listed,
not compared (`--strict` makes that a failure). Exit status is 1 if any
variable exceeds its tolerance, so it can gate a build.

Two logs from two different runs are never sample-aligned, so the numbers are
only as good as the scenario: **run the same bench scenario both times**, and
expect a deviation wherever the signal moves fast (the resampling error is
roughly the signal's slope times the time skew between the runs). Steady-state
sections are where the comparison actually has power.

### Tolerance file

JSON (recommended) or CSV with columns `var,max_abs,mean_abs,rel,interp`
(`var` = `*` sets the defaults).

```json
{
  "default": { "max_abs": 1.0, "mean_abs": 0.5, "interp": "linear" },
  "vars": {
    "nmot_w":  { "max_abs": 60.0, "mean_abs": 20.0 },
    "ti_1_w":  { "max_abs": 0.08, "mean_abs": 0.02 },
    "B_stend": { "max_abs": 0.0,  "interp": "hold" }
  }
}
```

| Key | Meaning |
|---|---|
| `max_abs` | limit on the largest absolute deviation |
| `mean_abs` | limit on the mean absolute deviation — the one that catches a small constant offset |
| `rel` | limit as a fraction of the largest absolute baseline value of that variable; the effective `max_abs` is the larger of the two |
| `interp` | `linear` (default) or `hold`. Use `hold` for enumerations, bit flags and anything that steps rather than ramps. |

Tolerances belong next to the thing they judge: the bench tolerances for a
patch live in `patches/<name>/test/`, not here.

## 3. Synthetic samples

`samples/` holds a deterministic 10 s bench scenario (idle, a trapezoidal load
step from 4 s to 8 s, idle) used by `tests/test_logcmp.py`:

| File | What it is |
|---|---|
| `baseline.csv` | the stock run |
| `candidate_ok.csv` | a repeat run: sensor noise and a 17 ms time skew, inside tolerance |
| `candidate_bad.csv` | the same run with `ti_1_w` deliberately +8 % |
| `tolerance.json` | limits for this scenario |

```bash
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_ok.csv \
    -t logging/samples/tolerance.json          # RESULT: OK,     exit 0
python3 tools/logcmp.py logging/samples/baseline.csv logging/samples/candidate_bad.csv \
    -t logging/samples/tolerance.json          # FAIL ti_1_w,    exit 1
python3 logging/make_samples.py                # regenerate them (byte-identical)
```

They are synthetic — the numbers come from `make_samples.py`, not from an ECU.
They exist to test the tooling, not to describe the engine.

## 4. Not here yet

The logger itself. It needs the KWP2000/TP2.0 transport (`docs/03_tooling.md`
section 6) and the measuring-variable table, and belongs to a later brief.
Whatever writes it must emit section 1 above.
