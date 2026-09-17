# Common rules for every agent brief

You are an autonomous engineering agent working in a git worktree of
`Cakku/ecu_azx` on a dedicated branch (`agent/<brief-id>`). Nobody will
answer questions while you work: make assumptions explicit in your report and
continue. Read this file first, then your brief.

## Read before starting
1. `docs/README.md` (status legend), `docs/02_memory_map.md` (verified layout;
   the only source of truth for addresses), `docs/04_re_guidelines.md`
   (conventions), then the sections of `docs/03_tooling.md` and
   `docs/05_flexfuel_design.md` your brief points to.
2. `tools/README.md`; run `python3 tools/checksum.py verify -q data/passat_azx_ori.bin`
   and confirm `ALL OK (65 blocks)` before and after your work.

## Hard rules
- **Never modify `data/passat_azx_ori.bin`.** Its SHA-256 must stay
  `b15590d3f1874ace3125c5d047c09a686db9b8bb498187663539ebab205609b3`.
  Work on copies under your scratch directory or `work/` (gitignored).
- **Never flash, write to, or talk to a real ECU.** These briefs are desk work.
- Addresses are **CPU addresses**; file offsets are prefixed `file`.
  Convert only with `tools/med9lib.py`. Remember the aliases: external flash
  at 0x000000, calibration used as 0x5Cxxxx, on-chip flash at 0x404000.
- Every address or fact you record carries a status tag
  (VERIFIED-STATIC / VERIFIED-DYNAMIC / COMMUNITY / HYPOTHESIS) and the
  evidence: the script and command, or the disassembly lines, or the URL.
  No address without evidence. Do not "verify" one guess with another.
- Do not extend `med9_re/old_work/`; it is frozen and partly wrong.
- Do not rewrite existing docs wholesale. Add or correct facts in place with
  the tag and date, or add a "Corrections" entry, so history stays visible.
- New findings go to `re/findings/<topic>.md`; new symbols are appended to
  `re/symbols.csv` in its existing column format (one row per symbol, evidence
  column filled). Keep new scripts in `tools/` (analysis), `ghidra_scripts/`,
  `emu/`, `logging/` or `patches/` as the brief says, with a docstring and a
  usage line.
- Use `sudo` only if the brief allows it. If a step needs a password or a
  physical action, write the exact commands/steps for the human into your
  report and continue with the alternative the brief names.
- Stay inside the assigned issues. Note tangents in one paragraph of the
  report; do not pursue them.
- Commit on your branch in small, described commits. **Do not push** and do
  not touch `main`. The human merges after review.
- Time-box dead ends: if an approach has not produced evidence after a
  reasonable effort, record what you tried and switch to the next approach in
  the brief.

## GitHub
Issues live in `Cakku/ecu_azx`; `gh` is authenticated. When you finish (or
stop), post one comment on each issue you worked on: what was done, what is
verified, what remains, and the branch name. Do not close issues; the human
does after review. You may tick checklist items in the issue body with
`gh issue edit N --body-file` if you completed them.

## Final report format (your last message)
1. **Summary** (5 lines max).
2. **Verified facts** — table: fact, evidence, tag.
3. **Files added/changed** with one line each; branch name and commit list.
4. **Open questions / hypotheses** you could not settle.
5. **Blockers for the human** (exact commands, purchases, physical steps).
6. **Suggested next steps** for the follow-on brief.

## Added 2026-09-16 (after waves A and B)

- `main` contains waves A and B. Before any reverse engineering, read the
  `re/findings/*.md` file for your area and `re/symbols.csv`; do not redo
  what is there, correct it in place with a dated note if it is wrong.
- Ghidra: never open `ghidra_projects/med9` read-write from two agents. Copy
  it to `/tmp/ghidra_<brief-id>` and import the symbols
  (`re/findings/injection.md` §0 has the exact commands, about a minute);
  `ghidra_scripts/decompile.py` opens a project read-only.
- Do not regenerate `re/med9_draft.xdf` on your branch; the integrator does
  it at merge time. Append rows to `re/calibration_draft.csv` only when your
  brief says so.
- Commit after every finding, not at the end. Agents that batched their
  commits lost work when the API rate limit hit.
- Run `python3 -m unittest discover -s tests` before your final report and
  add tests for every new tool (`tests/common.py` has the dump-unchanged
  base class). New Python needs nothing beyond `requirements.txt` unless the
  brief says otherwise.
- Patch code follows `docs/04_re_guidelines.md` §7 and the framework in
  `patches/common/` once it exists. Nothing an agent produces is flashed.

## Added 2026-09-17 (after wave D, for wave E)

- **Worktree set-up, first two commands:** `git branch -m agent/<brief-id>`
  (the worktree tool names the branch itself) and
  `ln -s /Users/carlo/ecu_azx/.venv .venv` (gitignored). Then use
  `./.venv/bin/python3` everywhere — the bare `python3` on this Mac is a
  different interpreter without the project's packages. `make` in a patch
  directory already uses `$(REPO)/.venv/bin/python3` and the LLVM at
  `/Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64`.
- **`patches/ff_fuel` is one patch that grows** (D1 → D2 → E1 → E2 → E5), not
  a family of patches: one blob, one state block, one FFCAL001. A feature is
  its own `src/ff_<feature>.c`, gated by an `ff_<feature>_enable` byte in
  FFCAL001 that **defaults to 0**, with neutral tables, and its trampolines
  live in `hooks.S`. Only the brief named in the README's ownership table
  edits `patches/ff_fuel/**`, `emu/models/flexfuel.py`, the `tests/test_ff_*`
  files, `logging/sessions/ff_fuel.json` and docs/05 at any one time.
- **FFCAL001 changes append; nothing moves.** Bump `VERSION` and `LENGTH` in
  `ffcal001.py`, mirror them in `ff_state.h`, make `ff_cal_ok()` accept the
  new version only, regenerate `ffcal001_rows.csv`, and update the model, the
  tests and `ff_fuel.json` in the **same commit**. `struct ff_state` grows
  only past its current end (D2's `ff_nvm_req` sits at +0x40..+0x4B, then
  `ff_persist_buf`); the core/annex checksum split is documented in the
  header and must stay true.
- **Two proofs per feature, in the emulator:** the hooked stock code is
  bit-identical (a) with the feature disabled and (b) with it enabled at
  neutral calibration, shown by a whole-SRAM diff that moves nothing outside
  the state block. Segment-synchronous stubs check the state-block magic (or
  the brief proves the producer runs first), use only registers proven dead
  at the site, address RAM absolutely (never r2/r13), and state their
  instruction count in the README.
- **On-chip hook words** (0x404000-0x47FFFF) carry `"onchip_edit": true`, are
  counted in the patch README's hook table, and each names its external-flash
  alternative or says there is none. Whether the OBD route writes that flash
  is open until brief E6 and the bench read-back.
- **The measuring-block budget is fixed in the README** (groups 111/108/69/109,
  ids 2196-2199/2192-2195/2188-2191/2184-2187). Take only what your brief
  names, after `python3 tools/measuring_vars.py data/passat_azx_ori.bin --free`
  confirms it is still free.
- **The #37 asymmetry, now a rule for every feature:** on FAULT the *fuel*
  factor is held and then decayed; *ignition* and *rail* terms go to zero on
  the activation the mode leaves OK/HOLD. No hold, no ramp, for anything that
  adds advance or pressure.
- **Simulated data is labelled.** Logs from `logging/ecu_sim.py` carry
  `# simulated: true` and live under `logging/samples/`; they never enter a
  bench comparison set or a `VERIFIED-DYNAMIC` claim about the ECU.
- Findings files gain **dated sections**, never rewrites; when a brief settles
  an open item listed in a findings file's "Open" table, mark that row
  **SETTLED (date, brief, section)** in place, as C4 and D3 did.
