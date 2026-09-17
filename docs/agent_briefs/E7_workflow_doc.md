# Brief E7 — `docs/07_workflow.md`, the end-to-end walkthrough (#42, first step) and documentation drift

Issue **#42**, step 1 ("write the walkthrough"); the bench dry-run stays for
the human. Wave E filler: run it in any free slot **after E4 is merged** (its
simulator section depends on E4). Documentation only, plus the drift fixes
listed below. No `sudo`; nothing is flashed.

Read every file under `docs/`, `tools/README.md`, `patches/README.md`,
`patches/ff_fuel/README.md` and `test/*.md`, `logging/README.md`,
`emu/README.md`, `re/README.md`, `docs/agent_briefs/README.md`, and the
GitHub issues #26, #27, #28, #42, #45 (`gh issue view`). Then **do** the
software steps yourself: build `patches/ff_fuel` from clean (`make check
&& make dump && make gen && make apply`), run the suite, run the simulator
rehearsal from E4's `logging/README.md`, generate the XDF. Every command you
write down must have been run.

## Tasks
1. `docs/07_workflow.md`, for a reader who did not build any of it, with the
   real commands and expected outputs:
   1. **A calibration-only change**: open the XDF in TunerPro on a copy of the
      dump, edit, save, `checksum.py fix`, `bindiff` against the stock file,
      `verify`; what "descriptor" changes in the diff mean; the E0 rule.
   2. **A code change**: patch directory anatomy, `make check/dump/gen/apply`,
      the emulator tests, `blobdis --check-sda`, `patch_gen`/`patch_apply`
      guards (`calibration_edit`, `onchip_edit`, `ram_status`), FFCAL001
      versioning and the descriptor rows.
   3. **Flashing** (the human's steps, written as a checklist that quotes
      docs/04 §6 and docs/06 §6, marks every step whose behaviour is still
      unverified until Flash 0/1, and includes the read-back comparison and
      the roll-back file).
   4. **Logging**: `med9log.py` sessions, VCDS groups 111/108/69/109 (as far as
      merged), the simulator rehearsal, log format.
   5. **Log review**: `logcmp.py`, tolerance files, what a pass and a fail
      look like, the E0 equivalence and fault-matrix procedures.
   6. **Roll back and recovery**: stock file, BDM backup, what never to flash.
   Cross-link instead of copying; one command per code block.
2. Drift fixes, each a separate small commit: `tools/README.md` test count
   and the list of test files; `docs/README.md` index row for 07;
   `docs/03_tooling.md` §7 dated pointer to 07; `docs/01_project_plan.md`
   §2 status paragraph if the briefs README already moved on; any command in
   a README that no longer runs as written (report each).
3. Do not edit `re/findings/*` or anything under `patches/` except a README
   typo; do not regenerate the XDF. Commit on `agent/E7`; comment on #42.

## Acceptance
A second person can follow `docs/07_workflow.md` up to the flashing step on
this Mac with only the repository; every command in it was executed by you
and its output is quoted; the drift list is fixed or reported.
