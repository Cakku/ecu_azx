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
