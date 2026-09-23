# Brief G3 — The two unfound table walkers: the init-array periodic walk (which decides the flash-CRC period) and the OBD bitmap-builder's second caller (#20 CRC timing, #39 completeness)

Two waves left the same shape of open item: a firmware table that is walked
**twice** — once at start-up and once by a periodic process nobody has placed.
F3 found it for the one-shot init array at 0x0B1A68 (`boot.md` §6.5(d)): the
flash-CRC task is activated through that array during the init walk, but
something re-walks a slice of it afterwards (0x0B4E24 stores index 771 →
cursor 0x7FC9D8, which 0x0B5878 compares against time-table cell 0x7FE5A0), and
**what rate that walk has is open — it is what decides how long a real ECU
takes to publish its flash checksum**. F6 found the twin for OBD: the mode-01
dispatch table's `h2` field (the bitmap builder `obd_pid_support_build` 0x5CBE8)
"runs from a second walker not yet found" (`obd.md` §8 item 2). This brief
settles both, statically. Both feed something the bench day needs: the CRC
period tells the simulator and the logger how long `flash_crc.json` must wait,
and the OBD walker completes the mode-01 picture G1 builds on. Wave G pair 2,
parallel with G2. Desk work only.

Read `00_common_rules.md`, `re/findings/boot.md` §2, §3, **§6 in full (§6.5(d)
is task 1)**, `re/findings/flash_programming.md` §5.3 (the CRC task states and
`tbl_crc32_ranges` 0x0A3A10), `re/findings/scheduler.md` §11 (the ERCOSEK
rasters, the time tables 0x478EE4 / 0x478F80, the divider chains), `re/findings/obd.md`
§1, §2, §4, **§8 item 2 (task 2)**, `re/findings/kwp.md` §1-§2 (the dispatcher
0x13E98C and how it calls handler `+0x8` vs the config-struct `blrl`s),
`logging/sessions/flash_crc.json`, `logging/ecu_sim.py` (`FlashCrcTask`,
`INIT_ENTRIES`), `tools/callgraph.py`, `tools/sda_xref.py`,
`tools/find_branch_refs.py`, `tools/find_abs_refs.py`, `tools/blobdis.py`,
`tools/ercosek_tasks.py`.

## Facts
- **Init array** 0x0B1A68: flat NULL-terminated array of 1,028 function
  pointers, walked once at start-up (`boot.md` §6). `flash_crc_task` (0x11CB10)
  is reached via `tbl_module_init` slots 0x0B2684-0x0B2694 (indices 775-779),
  five times during the init walk = 500 bytes hashed. **Afterward**: 0x0B4E24
  stores 0x0B2678 (index 771) into cursor 0x7FC9D8; 0x0B5878 compares 0x7FC9D8
  against time-table cell 0x7FE5A0. So the run from index 771 is **also** walked
  as a periodic process list. Its rate is **open, HYPOTHESIS** (`boot.md`
  §6.5(d)).
- The CRC task hashes 2,462,208 bytes at **0x64 bytes per activation** = 24,627
  activations to publish 0x5562139F (VERIFIED, `flash_crc.json`). If the
  periodic walk runs the index-771 slice every N ms, the publish time is
  24,627 × N. `flash_crc.json`'s note and `ecu_sim.py --flash-crc` currently
  assume one activation per simulated 10 ms raster (246 s); confirming or
  correcting N is the point.
- **OBD**: the mode-01 dispatch-table entry at 0x2B9BC holds handler `+0x8`
  (0x5D0F4, `obd_mode01_h1`) and `h2 +0xC` (0x5CBE8, `obd_pid_support_build`).
  The main dispatcher (0x13E98C) only ever calls `+0x8`; its three `blrl`s at
  0x13EB74/0x13EB90/0x13EBAC take handlers from the **config struct**, not the
  entry. So `obd_pid_support_build` (which rebuilds the RAM support bitmaps,
  `obd.md` §4) is reached from a second walker not yet found (`obd.md` §8
  item 2). The ISO-TP arm is traced as far as the ISR at 0x404000 kind-4
  (0x4040C4) and the channel table `[0x7FDA7C] + idx*0x14` (0x1443C4)
  (`obd.md` §8 item 1) — related, and worth a look if the walker turns out to
  be on that path.

## Tasks
1. **The init-array periodic walk and the CRC period** (primary). Follow
   0x0B4E24 and 0x0B5878: what function contains them, what activates that
   function, and at what raster (use `sda_xref --code`, `find_branch_refs`,
   `ercosek_tasks --periods`, and the time-table cell 0x7FE5A0 as the anchor —
   `scheduler.md` §11 decodes those cells). Establish N, the period of the walk
   that re-runs index 771, VERIFIED-STATIC, or bound it and say what dynamic
   check closes it. Compute the real flash-CRC publish time (24,627 × N) and
   whether the stock task even reaches state 2 within a normal drive. If the
   walk covers more of the array than just the CRC slice, list which indices it
   re-runs (this is the "second consumer" `boot.md` §6.1 could not place —
   settle §6.5(d) and §6.1 together).
2. **The OBD bitmap-builder's caller** (secondary). Find what calls
   `obd_pid_support_build` (0x5CBE8) — the second walker of `obd.md` §8 item 2.
   Candidates: a mode-01 pre-pass that runs `h2` before `h1`, the config-struct
   `blrl` path with the entry's `h2` copied into the struct, or the ISO-TP
   receive path (0x1443C4 family). Name it VERIFIED-STATIC; if it is periodic,
   give its raster; if it is on the request path, say which service triggers it.
   This tells G1 (and a bench tester) whether the support bitmap is current at
   the moment a scan tool reads `01 00`.
3. **Feed the simulator/logger** (light touch — you do **not** edit
   `logging/ecu_sim.py`; that is G5's file this pair). Write the period finding
   into `logging/sessions/flash_crc.json`'s comment as a dated note (that file
   is a session descriptor, not code) **only if** G5 is not the owner of it —
   check the wave-G ownership table; if G5 owns it, put the number in your
   report and in `boot.md` for G5 to apply. Prefer the latter to avoid a
   collision.
4. **Docs and symbols.** Settle `boot.md` §6.5(d) and §6.1 **SETTLED (date,
   G3, section)** with the period; settle `obd.md` §8 item 2 in place; add a
   dated note to `scheduler.md` if a new periodic process is named; `re/symbols.csv`
   rows for the walker function(s) and cursor 0x7FC9D8. Comment on #20 and #39;
   commit after every finding.

## Acceptance
The period of the init-array periodic walk is VERIFIED-STATIC (or bounded with
the dynamic check named), and the real flash-CRC publish time is stated;
`obd_pid_support_build`'s caller is named VERIFIED-STATIC; `boot.md` §6.5(d)/§6.1
and `obd.md` §8 item 2 are marked SETTLED in place; new symbols recorded; suite
green (you add tests only if you add a tool); dump untouched. `logging/ecu_sim.py`
and `logging/bench_rehearsal.py` are **not** edited by this brief.
