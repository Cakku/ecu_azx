# Brief F1 — Flash 1 counter patch: hook both OS task sets, add the source byte, rewrite the decision table (#27, #44 live-set row, prepares #26)

`patches/ff_counter` is the file that goes onto the bench ECU as **Flash 1**
(#27): the first code of ours that runs there. It hooks one word,
0x12067C in `task_100ms` (0x1205A0), which brief C4 showed to be a 10 ms
raster of **task set B** (`re/findings/scheduler.md` §11). Brief E1 then
showed, statically, that **task set A is the live set** (`scheduler.md`
§11.8, VERIFIED-STATIC reachability: `prsoll` and `zwstt` are produced only
by set-A processes, and the engine cannot run without them). As built today,
Flash 1 would therefore show a counter that never moves — the outcome
`patches/ff_counter/test/procedure.md` §4 already lists ("set A is live; the
hook in 0x1205A0 never runs … move the hook to 0x4328E4"). Nobody carried
E1's result into the patch. This brief does, and turns Flash 1 into the
experiment that answers two open questions at once. Wave F pair 1, parallel
with F4. Desk work only; nothing is flashed.

Read `00_common_rules.md`, `docs/06_patch_pipeline.md` §1-§5,
`patches/README.md`, `patches/common/*`, `patches/ff_counter/**` (all of
it), `re/findings/scheduler.md` §8, §8.1, §11 and §11.8,
`re/findings/ram.md` §8, `re/findings/flash_programming.md` §7,
`patches/ff_fuel/src/hooks.S` (the set-A stub at 0x432940 and the owner /
`ff_src_seen` logic in `src/ff_fuel.c`), `patches/ff_fuel/README.md` (hook
table format), `logging/sessions/flash1_counter.json`,
`logging/sessions/wave_b_confirm.json`, `tests/test_patch_framework.py`,
`tools/patch_gen.py`, `tools/patch_apply.py` (`onchip_edit`).

## Facts
- Current patch: hook word 0x12067C `4B FF E9 B1` (`bl 0x11F02C`) →
  `bl 0x150000`; trampoline `HOOK_TAIL` (16-byte frame, saves LR only,
  tail-branches `ba 0x11F02C`); blob 96 B at 0x150000; RAM 0x7FFB00/0x100,
  `ram_status: static` (#23 runtime half pending, do not change that);
  `u32 ff_ticks` +0, `u16 ff_alive` +4 = 0xFC01, `u16 reserved` +6. External
  flash, code descriptor #32 (`patches/ff_fuel/README.md` descriptor table).
- The set-A twin site is **0x432940** `4B C8 B0 A5` (`bl 0x0BD9E4`) inside
  0x4328E4, set A's 10 ms raster (task id 19). 0x0BD9E4 is an empty function
  (one `blr`) whose only caller in the image is that site, so a stub there
  preserves nothing (`scheduler.md` §8.1, brief D1). It is **on-chip flash**
  (0x404000-0x47FFFF): `"onchip_edit": true`, code descriptor #49.
  `patches/ff_fuel` already hooks exactly this word; copy its dead-register
  argument, do not re-derive it from scratch.
- `flash_programming.md` §7.1: the ECU's own OBD programming route whitelists
  0x404000-0x47FFFF. Whether KESSv2 *drives* that route is the bench
  read-back after Flash 0 (§7.2). So after Flash 0 the on-chip question is
  answered independently of this patch; the counter's job is the live set.
- Stock raster counters, u32, read before judging any slope
  (`procedure.md` §3 table): 0x7FD75C / 0x7FD754 (set A, 1 ms / 10 ms) and
  0x7FD760 / 0x7FD778 / 0x7FD758 (set B, 1 / 2 / 10 ms); expected +1000 /
  +100 and +1000 / +500 / +100 per second, ~0.25 % low.
- `patches/ff_fuel`'s `ff_src_seen` byte records which hook ran (1 = set A,
  2 = set B, 3 = both) and answers `scheduler.md` §11.7 from one logged
  sample. Flash 1 should give the same answer a day earlier, from a
  smaller patch.
- Rule from the E-series: every on-chip hook word is counted in the README
  hook table and names its external-flash alternative or says there is none.

## Tasks
1. **Baseline first.** Record `make apply`'s output on unmodified `main`
   (patched sha256, range counts) in your report; the external-only build of
   step 3 must reproduce it byte for byte.
2. **Second hook at 0x432940.** Add a trampoline for the set-A site. The
   displaced instruction is a `bl` to an empty leaf, so the stub does the
   tick and returns to 0x432944 with r1, LR and every live register intact
   (state the dead-register set and the instruction count in the README, as
   `ff_fuel` does). Both stubs increment `ff_ticks` and OR their bit into a
   new `u8 ff_src_seen` at +6 (1 = set A, 2 = set B); +7 stays reserved 0.
   `ff_alive` and the cold-start rule (first tick initialises, nothing zeroes
   `.bss`) are unchanged. RAM use stays 8 bytes; the block does not move.
3. **Build variants.** `make HOOKS=both` (default) and `make HOOKS=external`
   (the set-B word only — today's patch, byte for byte; useful only in the
   case §11.8 is wrong and set B is live while the on-chip array is not
   writable). `make gen` must be idempotent for both, `patch.json` for the
   default is what is committed, and the variant is documented in the README
   and in `test/procedure.md`. Do not add a set-A-only variant: the external
   word costs nothing and is the only hook that survives a skipped on-chip
   write. Note in the README what follows from §11.8: if the on-chip array
   cannot be written, **no** counter patch can run in the live set, and the
   fallbacks are those of `flash_programming.md` §7.3.
4. **Tests** (extend `tests/test_patch_framework.py` or add
   `tests/test_ff_counter_patch.py`, using `tests/common.py`): each stub
   increments `ff_ticks` by exactly 1 and sets exactly its bit; running
   `0x432940..0x432944` on stock and patched images diffs all 64 KB of SRAM
   and differs only in the counter block plus any stub frame; the stub
   returns into the task with r1 and LR as at entry; the existing 0x12067C
   tests still pass; the `HOOKS=external` blob is byte-identical to today's.
5. **The decision table.** Rewrite `patches/ff_counter/test/procedure.md`
   §3-§4 so that the bench day reads: (a) the five stock counters, (b) the
   Flash 0 on-chip read-back result (`flash_programming.md` §7.2 item 1),
   (c) `ff_ticks` slope and `ff_src_seen`. Outcomes: slope ≈ 100/s and
   `ff_src_seen` = 1 → set A live (upgrade `scheduler.md` §11.8 to
   VERIFIED-DYNAMIC) and the on-chip hook word was written; slope ≈ 100/s and
   `ff_src_seen` = 2 → set B live, §11.8 is wrong, say so loudly; slope 0
   with the set-A stock counters moving and the on-chip read-back showing
   stock bytes → KESS skipped the array, and because set A is live the
   external hook never runs either: no counter patch can work until the
   on-chip route is available (`flash_programming.md` §7.3), write that
   consequence out plainly; slope 0 with the on-chip bytes written → stop
   and investigate before any other flash. Keep C1's ±2 % slope tolerance
   and the "compare against two stock runs first" rule.
6. **Session file.** `logging/sessions/flash1_counter.json`: add
   `ff_src_seen` (+6, u8) and rewrite its `_comment` checks to the new
   decision table; keep the five stock counters. Addresses come from
   `patch.json` via `--patch`, as today.
7. **Docs.** `patches/ff_counter/README.md` hook table (two words, one
   on-chip, alternative column filled), `re/findings/scheduler.md` §8 — a
   dated note only, saying the patch now hooks both sets and why (do not
   edit §11.x). Note for the integrator in your report: `docs/06` §4 and
   `docs/07_workflow.md` §3 mention the single-word Flash 1 (F2 owns
   `docs/07`). Comment on #27 and #44; commit after every step.

## Acceptance
`make check && make gen && make apply` clean for both variants: `ALL OK (65
blocks)`, 0 unexpected bytes, one on-chip warning for the default build, the
`ram_status: static` warning still printed; `HOOKS=external` reproduces the
pre-brief patched image byte for byte; the tests of task 4 pass and the
whole suite is green; `procedure.md` gives an unambiguous verdict for every
combination of (stock counters, on-chip read-back, slope, `ff_src_seen`).
