# Brief C3 — Live RAM logger over TP2.0/KWP2000, tested against an emulated ECU (#20, software half)

Issue **#20** (software; the runs on the bench ECU and in the car remain
open). Prepares **#44** and the dynamic half of **#23**. Wave C, second pair
(parallel with C4). No `sudo`. No real ECU, no real CAN adapter.

Read `00_common_rules.md`, `re/findings/kwp.md` (all — §0, §4, §5 and §8 are
the specification), `re/findings/measuring_vars.md`, `logging/README.md` §1
(the CSV format you must write), `docs/03_tooling.md` §6,
`tools/kwp_seckey_verify.py`, `tools/kwp_upload_verify.py` (how the real
handlers are driven in the emulator), `emu/README.md`, `re/findings/can.md`
§3-§4 (for the module B/C bus check), `pi_can_setup/README.md`.

## Facts
- Transport: VW **TP 2.0 over CAN** (channel setup on id 0x200, engine
  logical address 0x01) carrying KWP2000. TP2.0 is not described in this
  repo. Take it from public references (I-CAN-hack/pq-flasher `tp20`,
  notyal/vwcanread, EliasTuning/KWP2000-CAN and MED9RamReader, TP2.0
  write-ups), **cite them, check each licence before copying a line, prefer a
  clean re-implementation**; anything vendored is listed in `NOTICE.md`.
- KWP (VERIFIED-STATIC, dynamic checks emulated): session `10 89`; DDLI
  `2C F0 03 <pos> <size> <a2 a1 a0>` up to 20 chunks, `2C F0 04` clears;
  `21 F0` -> `61 F0 <bytes>`; TesterPresent `3E 01` / `3E 02` every ~2 s;
  SecurityAccess level 2: `27 03` -> `67 03 <seed>`, `27 04 <seed + 0x11170>`
  -> `67 04 34`; `10 86`; `35 <addr:3> 00 <size:3>` -> `75 3F`; `36` ->
  `76 <=62 bytes>` until NRC 0x12; `37` -> `77`. Reads overlapping
  **0x7F9E3C-0x7FA47F** are refused with NRC 0x31. `21 <group>` returns up
  to four VAG `(formula, A, B)` triples; the group table is at 0x5C5518.
- Handler addresses for the simulator: dispatcher 0x13E98C, I/O struct
  0x803DA4, `kwp_sid_2C_h1` 0x34D28, `kwp_sid_21_h1` 0x35F6C,
  `kwp21_dynamic_read` 0x34F48, `kwp_sid_27_h1` 0x36210, `kwp_sid_35_h1`
  0x360C4, `kwp_sid_36_h1` 0x37434, `kwp_sid_37_h1` 0x360B0. The two verify
  tools show exactly which RAM globals each success path needs.
- The Mac has no SocketCAN: python-can (`requirements.txt`) with `slcan`,
  `gs_usb` or `socketcand` (the Pi route); its `virtual` interface is the
  test bus.

## Tasks
1. Package `logging/med9kwp/`: `can_transport.py` (python-can bus from a
   config: interface, channel, 500 kbit/s; `virtual` for tests), `tp20.py`
   (client and **server** side: channel setup, parameter exchange,
   segmentation, ACK, keep-alive, disconnect), `kwp.py` (request/response,
   NRC decoding per kwp.md §7, session, tester present, security level 2,
   DDLI define/read, upload). Python 3.13+, type hints, the protocol core
   free of threads (a polling loop with timeouts or asyncio).
2. CLI `logging/med9log.py`:
   - `log --session logging/sessions/<x>.json -o logs/<date>.csv`: define the
     variables (DDLI, <=20 chunks; for more, rotate two dynamic ids or refuse
     with a clear message), poll `21 F0` as fast as the bus allows, write the
     long-form CSV of logging/README.md §1 with `# ecu`, `# dump_sha256`,
     `# transport` metadata; scaled values in `value`, raw counts as
     `<var>_raw` rows when `--raw`.
   - `dump --ranges logging/sessions/ram_snapshot.json -o snapshots/<date>.bin`
     (+ `.json` manifest: ranges, sha256, timestamp): security level 2,
     session 0x86, upload range by range, never crossing the protected window.
   - `groups 001 002 106 ...`: read measuring blocks and print the triples,
     decoded where the formula is known (take the VAG formula table from an
     open source, cite it, tag COMMUNITY; cross-check with A3's anchors: id 1
     formula 0x01 A=0xC8 = 40 rpm/LSB, id 2 formula 0x21 A=0x85 = %).
   - `probe`: channel setup + `10 89` + `3E 01`, prints round-trip times.
   Session file: `{"vars": [{"name": "nmot_w", "addr": "0x7FEE74", "size": 2,
   "signed": false, "scale": 0.25, "offset": 0, "unit": "rpm",
   "source": "re/findings/scheduler.md"}], "rate_hint_hz": 40}`; a var may
   give `"symbol": "<name>"` instead, resolved from `re/symbols.csv` or
   `re/measuring_vars.csv`.
3. **Simulator** `logging/ecu_sim.py`: TP2.0 server on a python-can
   `virtual` bus; every KWP request runs through the **real handlers** with
   `emu.Med9Emu` (seed the I/O struct and globals as the verify tools do;
   keep the emulator RAM as the simulated ECU RAM across requests); animate a
   few cells (an rpm ramp, a counter) so logs show movement. Options:
   `--drop-ack N`, `--delay-ms` for robustness tests. The protected-window
   refusal must come from the real range check, not from the simulator.
4. Tests `tests/test_med9kwp.py`, logger against simulator end to end: define
   six variables, log 2 s, the CSV loads in `tools/logcmp.py`, values equal
   the seeded/animated RAM; a 1 KB upload equals the emulator RAM; a range
   crossing the protected window is split and the protected part is refused
   as expected; keep-alive holds the session across 5 s; a dropped ACK is
   retried. Skip cleanly (with a message) if python-can is missing.
5. Session files in `logging/sessions/`:
   - `wave_b_confirm.json` — every row of #44: `tmot` 0x8021EF u8 (0.75 C/LSB,
     -48 C), `tmot_w` 0x802228, `ti_sum` 0x8030C4 (1 us/LSB), `prist` 0x8031DA
     and `prsoll` 0x8031F4 (0.005 bar/LSB), `zw` 0x7FEF87 s8 (0.75 deg),
     `dwkrz` 0x7FCE57-0x7FCE5C, `dwi` 0x803088 and `wbho1s` 0x80307E,
     `nmot_w` 0x7FEE74 (0.25 rpm), `rl` 0x7FED38 (100/4096 %), 0x80316E,
     0x8031F6, 0x80235A, `rk` 0x803038, `ksta_kstaa` 0x80302C (1024 = 1.0),
     and the raster counters 0x7FD760 / 0x7FD778 / 0x7FD758 (the 10 / 20 /
     100 ms epilogue counters, r13-0x2890 / -0x2878 / -0x2898: their rates
     settle the task periods of scheduler.md §5).
   - `can_bc_check.json` — the state words 0x802B08 (id 0x1A0, module B),
     0x802B72 (0x0C2, module C), 0x802AFE (0x050, module C) and the data
     bytes 0x803EF4 / 0x803F54 / 0x803F60: if all are live with only the
     powertrain pair connected, modules B and C share the wire (can.md §3).
   - `flash1_counter.json` — C1's counter address (read `patches/ff_counter/`
     if merged, else leave a placeholder and say so).
   - `ram_snapshot.json` — from C2 if merged, else ranges 0x7F8000-0x7F9E3B
     and 0x7FA480-0x807FFF.
6. Docs: new sections in `logging/README.md` (adapter choice — recommend a
   gs_usb/candleLight-class adapter for the Mac with the Pi socketcand route
   as fallback; OBD wiring; first-contact procedure on the bench and in the
   car; what to do if the gateway does not route 0x200); a pointer in
   docs/03 §6. Commit on `agent/C3`; comment on #20, one line each on #44
   and #23 naming the session files.

## Acceptance
All tests pass against the simulator on this Mac; the CLI is documented; the
hardware exit criterion of #20 stays open and the report lists the adapter
to buy and the first bench steps for the human.
