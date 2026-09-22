# Brief F6 — OBD mode 01 PID 0x52 (ethanol fuel %): locate the generic-OBD dispatcher and, if it is a table edit, add the PID to `patches/ff_fuel` behind an enable byte (#39 optional half)

Issue #39's deliverable has an optional second half: "optionally PID 0x52 in
the OBD handler". D2 left it untouched because the measuring block covers
the exit criterion and "the OBD handler is a separate surface". It is also a
separate *protocol*: generic OBD on this car is SAE J1979 over ISO 15765
(CAN ids 0x7DF / 0x7E0 → 0x7E8), not the TP2.0/KWP path `logging/med9kwp`
speaks. Nothing in `re/findings/` locates that stack. This brief finds it,
and adds PID 0x52 (ethanol fuel %, one byte, `A × 100 / 255`) only if the
addition is a table entry plus a small handler in the pattern D2 used for the
TKMWL. Wave F pair 3 filler, parallel with F5; the only wave-F brief allowed
in `patches/ff_fuel/**`. Desk work only.

Read `00_common_rules.md` (the `patches/ff_fuel` rules of 2026-09-17),
`re/findings/kwp.md` §1, §2, §12, `re/findings/can.md` (receive table
0x2BC90, TouCAN slots, which ids the ECU answers), `re/findings/measuring_vars.md`
§8 (how D2 found and used free TKMWL slots), `patches/ff_fuel/README.md`,
`src/ff_diag.c`, `src/hooks.S`, `ffcal001.py`, `patch.json`,
`tests/test_ff_diag_patch.py`, `logging/ecu_sim.py` (`--sim-patch`),
`tools/measuring_vars.py`, `tools/find_abs_refs.py`, `tools/callgraph.py`.

## Facts
- J1979 mode 01: PID 0x00 / 0x20 / 0x40 / 0x60 return 32-bit "supported PIDs"
  bitmaps; PID 0x52 is bit 14 (from the MSB, 0-based bit 13 → mask
  0x00040000) of the 0x40 bitmap; response `41 52 A` with
  ethanol % = A × 100 / 255. A scan tool shows it only if the 0x40 bitmap
  advertises it.
- The ECU's KWP dispatcher (0x2B870, 28 entries, `kwp.md`) is the TP2.0
  path. `can.md` lists the receive slots; whether 0x7DF/0x7E0 are among them,
  and where an ISO-TP (single-frame at least) reassembly and a mode-01 PID
  table live, is unknown. Bosch MED9 firmware normally holds the PID
  support bitmaps as constants next to a PID → handler table.
- D2's pattern for a new measuring variable: point a free table word at a
  handler in the blob (`build.data` in `patch.json`), handler reads
  `ff_state` and emits `(formula, A, B)`; end-to-end test through the
  firmware's own SID 0x21 route in `ecu_sim.py`. The same shape is wanted
  here: a table word (and a bitmap bit) plus a handler that returns
  `e_filt` scaled to 0..255, both gated by a new FFCAL001 byte
  `ff_pid52_enable` (default **0**; when 0 the handler answers exactly as
  the stock "unsupported" path does, and the bitmap bit is set only in the
  patched image if the enable byte is 1 — if the bitmap is a flash constant
  the bit cannot be gated at run time; then ship the bit **clear** and
  document the one-word calibration edit that turns it on).
- FFCAL001 is at v4, 332 B; changes append; `ff_cal_ok()` accepts the new
  version only; regenerate `ffcal001_rows.csv`; update the model, tests and
  `logging/sessions/ff_fuel.json` in the same commit. The patched file must
  stay behaviourally identical to today's with the byte at 0.

## Tasks
1. **Locate generic OBD**: which CAN receive slot(s) carry 0x7DF / 0x7E0
   (`can.md` table 0x2BC90 and the slot → buffer → consumer chain), the
   ISO-TP single-frame parser, the mode dispatcher, the mode-01 PID table
   and the four support bitmaps. VERIFIED-STATIC with `find_abs_refs`,
   `callgraph` and disassembly; name the functions and tables in
   `re/symbols.csv`; write `re/findings/obd.md` (short). If the stack is
   not in this image (some VAG MED9 builds answer OBD through the gateway
   only), say so with the evidence and stop after task 1.
2. **Decide**: is PID 0x52 a table word + bitmap bit + handler? If the PID
   table is dense (index = PID) or a sparse (PID, handler) list with free
   space, yes. If adding it needs an instruction rewrite in the dispatcher,
   write the design in `obd.md` and stop — do not patch code paths outside
   D2's pattern for an optional feature.
3. **Implement** (only if task 2 says yes): `src/ff_obd.c` handler, the
   `build.data` words, FFCAL001 v5 with `ff_pid52_enable`, model + tests
   (`tests/test_ff_obd_patch.py`: the stock mode-01 path for a few standard
   PIDs is bit-identical on the patched image with the byte at 0; PID 0x52
   returns `round(e_filt × 255 / 100)` with the byte at 1; whole-SRAM diff
   clean outside the state block). If `ecu_sim.py` can be taught to accept a
   raw 0x7E0 single frame and run the firmware's own OBD handler, prove it
   end to end there as D2 did for `21 6F`; otherwise call the handler
   directly and say so.
4. **Docs**: patch README (hook table unchanged, new data words and the
   enable byte), `docs/05_flexfuel_design.md` §3.7 dated note, `measuring
   _vars.md` or `obd.md`. Do not regenerate the XDF. Comment on #39; commit
   after every step. `make apply` must stay `ALL OK (65 blocks)`, 0
   unexpected, with the same eight hook words.

## Acceptance
`re/findings/obd.md` locates the generic-OBD stack or proves its absence;
if implemented, PID 0x52 works in the emulator behind `ff_pid52_enable`
(default 0) with the stock mode-01 behaviour bit-identical when disabled;
FFCAL001 v5 appended, model/tests/session updated in one commit; suite
green; dump untouched.
