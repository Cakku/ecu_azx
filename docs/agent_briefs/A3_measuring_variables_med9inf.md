# Brief A3 — Measuring-variable table and ECU id with 360trev/MED9inf

Issue: **#10**. Runs now; no Ghidra needed. No `sudo`.

Read `00_common_rules.md` first.

## Context
- MED9 firmware carries a table of measuring variables (called TKMWL in the
  community tooling) mapping variable ids to RAM addresses/sizes; VCDS
  measuring blocks and the KWP 0x21/0x2C services index it. `docs/02` §7 has
  a candidate at CPU 0xA5654 from a byte signature (HYPOTHESIS) and the
  ECU identification block at 0x5CEE20 (file 0x1CEE20).
- `https://github.com/360trev/MED9inf` parses such a table and the ECU id;
  its author tested it on a 3.6 FSI 03H906032DQ (close relative).
- Our RAM: on-chip SRAM 0x7F8000-0x7FFFFF, external SRAM 0x800000-0x807FFF,
  r13 range 0x7F8000-0x8073E9. Any "variable address" outside these is wrong.

## Tasks
1. Clone MED9inf into `work/` (gitignored), build with clang, read its source
   to learn what file layout it expects (2 MB image at file offset 0 or at
   0x400000-based addresses?). Adapt the input accordingly (e.g. feed the first
   2 MB, `med9_re/passat_azx_flash.bin` is exactly that) and document the
   adaptation.
2. Run it; capture the raw output to `re/findings/med9inf_output.txt`.
3. Validate: every RAM address must fall in the ranges above; the table
   address must agree with, or supersede, the 0xA5654 candidate (explain
   either way); the ECU id must match `docs/02` §1.
4. Produce `re/measuring_vars.csv`: `var_id, ram_addr, size, name_or_blank,
   scaling_or_blank, evidence`. Names only where MED9inf or a hard fact gives
   them; otherwise leave blank rather than guess. If the FR index from brief
   A2 is already merged, use it to name the obvious ones (nmot_w, rl_w,
   tmot, ti, lambda, fra/frau/frao) and state the reasoning.
5. Cross-check with the dump: with `tools/find_abs_refs.py --range` or a
   small script, confirm the table is referenced by code near the KWP
   dispatcher handlers (0x21/0x2C at `docs/02` §7) — this upgrades the table
   location to VERIFIED-STATIC.
6. Append the table and any confirmed variables to `re/symbols.csv`;
   write `re/findings/measuring_vars.md` (method, results, tags).
7. Commit on `agent/A3`. Comment on #10.

## Not in scope
Decompiling the KWP handlers themselves (brief B3); dynamic confirmation.
