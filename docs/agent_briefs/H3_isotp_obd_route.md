# Brief H3 — The generic OBD route: the ISO 15765-2 single-frame parser, session 6 reachability, and PID 0x52 rehearsed over 0x7DF/0x7E8 (#48; #39 bench prerequisite)

Brief G1 implemented OBD mode-01 PID 0x52 and proved it in the emulator — over
**TP2.0**, through ecu_sim's KWP dispatch in internal session 6. A generic scan tool
on the car does something else: **ISO 15765-4 functional requests on 0x7DF (or
physical 0x7E0) as ISO 15765-2 single frames, answered on 0x7E8.** On this ECU the
request path is TouCAN module C mailbox 15 (mask 0x7C0 accepts 0x7C0-0x7FF) and the
answer is module C MB13 with id 0x7E8 (`re/findings/obd.md` §1, `can.md` §3). Two
items are open in `obd.md` §8: **(1)** the single-frame parser — which function
reads MB15 and hands the payload to `kwp_service_dispatch` (traced only to the ISR
arm at 0x4040C4 and the channel table `[0x7FDA7C] + idx*0x14`, 0x1443C4); **(4)**
whether internal session 6 is reachable on the car — it needs `[0x803D6A] == 4` and
`[0x7F804B] == 0x33` (the ISO 15765-4 OBD address; 0x37134 `cmpwi r4,0x33` →
`li r3,6` at 0x37154), and 0x7F804B has one reader and no statically resolved
writer (it sits inside the word written by `stw` at 0x15BB8/0x15C0C and possibly
the `stswi` at 0x1443A0). Session 4 (`10 86`) reaches the OBD services regardless.
G3 added the reconnect rule: the support bitmaps are rebuilt by
`kwp_service_h2_walk` 0x13ECB0 once per new diagnostic connection. Until the route
is traced, the bench check "read `01 52` with a real scan tool" rests on
assumption. Wave H pair 1, parallel with H1. Static RE + simulator; desk only.

Read `00_common_rules.md`, `re/findings/obd.md` **in full** (§1, §8, §9 G1, §10
G3), `re/findings/can.md` §3 (module C, mailboxes, the 0x7E8 correction),
`re/findings/kwp.md` §1-§2 (dispatcher 0x13E98C, `kwp_conn_cyclic` 0x13E650, the
session table, the config struct `blrl`s) and §12.7 (G5's re-dispatch loop),
`re/findings/scheduler.md` §13, `emu/toucan.py` (E4's TouCAN model),
`logging/ecu_sim.py` **in full** (`Med9Handlers`, the TP2.0 server, G5's
`DtcStore`, the h2 walk on each new connection, `--sim-patch`),
`logging/med9kwp/`, `logging/bench_rehearsal.py` (the `pid52` step),
`tests/test_ff_obd_patch.py` (`TestPid52OverKwp`), `tests/test_ecu_sim_rehearsal.py`,
`tools/find_abs_refs.py`, `tools/sda_xref.py`, `tools/callgraph.py`,
`tools/blobdis.py`, `ghidra_scripts/decompile.py`.

## Tasks
1. **The request path, function by function.** From the module C MB15 receive
   (ISR kind-4 arm 0x4040C4, the channel table entry at `[0x7FDA7C] + idx*0x14`,
   the store at 0x1443D4) to the buffer `kwp_service_dispatch` reads: who copies
   the 8-byte frame, where the ISO 15765-2 PCI byte (single frame `0N`) is
   checked, how multi-frame (first/consecutive/flow control) is handled or
   refused, and which task or ISR level runs it. Then the **answer path** to MB13
   / 0x7E8: PCI byte, padding byte, transmit trigger. VERIFIED-STATIC with
   `re/symbols.csv` rows; `obd.md` new §11 and §8 item 1 SETTLED.
2. **Session 6 reachability.** Trace the writers of the word holding 0x7F804B
   (0x15BB8, 0x15C0C, the `stswi` 0x1443A0 — channel-table initialisation?) and
   of 0x803D6A: does a frame arriving on 0x7DF/0x7E0 set the channel's address to
   0x33 and select session 6, or does the generic-OBD request run in session 4?
   Settle §8 item 4 or bound it and name the bench check (send `01 00` on 0x7DF
   and see which session answers on 0x7E8).
3. **The simulator route.** Add a CAN-level OBD path to `logging/ecu_sim.py`: a
   virtual module-C MB15 that accepts a 0x7DF/0x7E0 single frame and drives the
   **firmware's own parser** under Unicorn (seed the mailbox RAM as F3 seeded the
   init entries), producing the 0x7E8 answer frame from MB13; if the parser cannot
   be driven, a **labelled model** that reproduces the frame format of task 1 and
   says so in its docstring and in `logging/README.md`. Expose it to a client (an
   `obd` subcommand in `logging/med9log.py` or a small `logging/obd_client.py`;
   your call, documented). Keep every existing flag working (docs/08 cites them).
4. **Rehearsal.** `bench_rehearsal.py` gains a `pid52_obd` step: `01 00` on
   0x7DF → stock bitmap on 0x7E8 (switch off: byte-for-byte stock, the G1 proof
   over this route); set `ff_pid52_enable`; **reconnect** (new connection → h2
   walk); `01 40` advertises 0x52; `02 01 52` → `03 41 52 A` for E0 and E85.
   `--fresh-eeprom` score reported (79/79 + your checks). Simulated transcripts
   under `logging/samples/` with `# simulated: true`.
5. **Tests and docs.** New `tests/test_ecu_sim_obd.py` (the route, the PCI
   handling, the session outcome, the two proofs); `tests/test_ecu_sim_rehearsal.py`
   extended; `obd.md` §11 + §8 items 1/4 marked; `can.md` dated note (frame format
   on 0x7E8); `logging/README.md` one paragraph; `re/symbols.csv` rows. Comment
   on #48 and #39; commit after every finding.

## Ownership
`re/findings/obd.md`, `can.md` note, `logging/ecu_sim.py`, `logging/bench_rehearsal.py`,
`logging/med9log.py` (an `obd` subcommand only) or a new `logging/obd_client.py`,
`logging/med9kwp/` (additive), `logging/README.md`, `logging/samples/` (new files),
`emu/` (additive), `tests/test_ecu_sim_*`, `re/symbols.csv` rows. Do **not** edit
`patches/**`, `kwp.md` (H2 next pair), `eeprom.md`, `re/calibration_names.*`,
`injection.md`, `start.md`, docs/05 (H1 runs alongside you), docs/07, docs/08.

## Acceptance
The MB15 → dispatcher and dispatcher → MB13 paths are VERIFIED-STATIC; §8 items 1
and 4 are SETTLED or bounded with the bench check named; ecu_sim answers a 0x7DF
single frame on 0x7E8 (firmware parser, or a labelled model); the `pid52_obd`
rehearsal step passes with the switch-off stock proof; `--self-test` PASS; suite
green (820 + yours); dump untouched; `patches/**` untouched.
