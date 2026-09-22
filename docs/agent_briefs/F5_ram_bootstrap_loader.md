# Brief F5 — The RAM-resident bootstrap loader: what it accepts, what it can rewrite, and what that means for recovery (#26, #28, risk table of #2; `flash_programming.md` §8)

Brief E6 mapped the ECU's **application-side** programming route: `10 85`
reboots into a second KWP stack whose whitelist admits 0x020000-0x07FFFF,
0x0A0000-0x1BFFFF, 0x404000-0x47FFFF and the calibration, and refuses
0x000000-0x01FFFF, 0x080000-0x09FFFF and 0x400000-0x403FFF
(`re/findings/flash_programming.md` §2.4). It also found a **second
programming path** it did not decode: the boot copies flash
0x019798-0x02A827 (0x5090 B) to RAM 0x7F8728 and, under conditions read at
0x01270C, branches into it (`flash_programming.md` §5.1, `boot.md` §3.2
note). That loader carries its own copy of the flash device table (flash
0x01E71C, RAM 0x7FD6AC) and its own five command sets — and it is the only
route in the image that *could* rewrite the boot module and the resident
programming module, i.e. the route that decides whether a damaged ECU is
recoverable over the connector or only over BDM. That is exactly the question
the 2026-09-22 hardware plan (`docs/01_project_plan.md` §4 Phase 0, M1) asks
before buying anything. Wave F pair 3, parallel with F6. Static work plus
emulation; **KESS and other tools are out of scope**, only our firmware.

Read `00_common_rules.md`, `docs/02_memory_map.md` §2, §3, §6,
`re/findings/boot.md` §1-§5, `re/findings/flash_programming.md` (all; §1,
§3, §4, §5.1, §8 closely), `re/findings/mpc5xx_registers.md` §5, §7, §8,
`re/findings/kwp.md` §1, §3, `re/findings/eeprom.md` §1.1, §1.5,
`re/findings/hardware_prep.md` §2 (what the tools claim to do),
`tools/r2_context.py`, `tools/blobdis.py`, `tools/callgraph.py`,
`tools/flash_segments.py` + its test, `emu/core.py`, `emu/README.md`.

## Facts
- Relocation: code at flash offset `f` in 0x019798-0x02A827 runs at
  `0x7F8728 + (f − 0x019798)`; `boot_swsr_service` covers 0x7F8728-0x7FD7FF
  with the copy (`flash_programming.md` §5.1). Check the r2/r13 the loader
  expects with `tools/r2_context.py` before disassembling — E6 found the
  relocated *application* driver does use r2 (§3.1), contrary to an earlier
  note.
- Entry decision (§5.1): two RAM magics drive the boot's mode choice; the
  branch at 0x01270C tests byte RAM 0x7F8010 ∉ {0, 0x10} plus the two CS2
  pointers at DECRAM 0x6F8404 / 0x6F8408 and is labelled "external-tool
  bootstrap"; `10 85`'s magic 0xAABFFB11 at 0x7F8020 selects the
  application's programming stack instead (§2.1). A bad `5A5A` marker at
  file 0x1E2500 reboots "straight into the RAM bootstrap loader" (§5.3b) —
  confirm which of the two paths that really is.
- The loader's device table is identical to the application's (three
  devices, same start/end pairs) with its own command sets (§3.2). Erase
  geometry: UC3F ten blocks 16K/48K/48K/16K/6 × 64K from 0x400000; CS0 8 KB
  parameter + 64 KB main blocks (§4.2).
- Open rows of §8 this brief owns: the consumer of `boot_mode_flags` bit 2
  (0x7FD401) that starts the programming KWP stack (getters 0x04CC34-0x04CC7C
  and module entry 0x087494 have no static callers — HYPOTHESIS: an
  init-table entry; `boot.md` §6.3-§6.4 now classify all 1,028 entries, so
  check there first); the loader's whitelist; the meaning of the selector
  byte 0x7FD328 (0x11 / 0x33 / other); `31 C5` (0x0891C8), partly decoded.
- Transport: the application stacks speak KWP over TP2.0 (CAN) and a K-line
  path (`kwp_sid_10_h1` 0x3716C, `kwp.md` §12.4). Which transport the loader
  speaks — TouCAN, SCI/K-line, or the QSMCM serial paths `eeprom.md` §1.1
  attributes to flash tools — is not written down anywhere.

## Tasks
1. **Map the loader**: entry, its own init, the transport it opens (which
   peripheral registers it programs), its command parser (SIDs or a raw
   frame format), and the address/length checks on each write- or
   erase-capable command — a table like `flash_programming.md` §2.4 with
   the physical ranges it will and will not touch, VERIFIED-STATIC. Name
   every function in `re/symbols.csv` with a `ldr_` prefix and the *flash*
   address in the evidence column (the RAM address in the notes).
2. **How one gets there**: decode the conditions at 0x01270C and their
   writers — who sets RAM 0x7F8010 and the DECRAM pointers, under what
   command (an application-stack or programming-stack service? BDM only? a
   power-on with a pin state?). Say plainly whether a tool on the OBD
   connector can reach the loader without BDM.
3. **Recovery consequences**, the decision section: for each failure the
   plan worries about — interrupted write of the application range, of the
   calibration, of the on-chip array, a wrong `5A5A` marker, a corrupted
   programming module 0x080000-0x09FFFF — which path the boot takes and
   whether the connector still offers a route back. Feed the risk table in
   `docs/01_project_plan.md` §6 and `docs/06` §6 with one dated paragraph
   each (you may edit those two paragraphs only). Settle the §8 rows you
   can; `31 C5` is lowest priority.
4. **Optional, time-boxed (≤ 2 h)**: run the relocated loader in `Med9Emu`
   to its first command parse with the transport registers stubbed, the way
   E6's task 4 proposed for the application driver.
5. **Deliverables**: new `re/findings/ram_loader.md` (flow, transport,
   command table, whitelist, device table, decision section, reproduction
   commands); `flash_programming.md` §8 rows marked SETTLED in place and a
   dated §10 pointer; `tools/flash_segments.py --loader` (or a sibling) that
   dumps the loader's tables, with a test; symbols. Do **not** edit
   `boot.md` (F3 owns it this pair) — hand the integrator a one-line pointer.
   Comment on #26 and #2; commit after every finding.

## Acceptance
`ram_loader.md` says, with evidence, how the loader is entered, what it
speaks, and which physical ranges it can erase and program; the decision
section answers "recoverable over the connector or BDM only" per failure
mode; the §8 open rows are settled or narrowed with what was excluded;
suite green; dump untouched.
