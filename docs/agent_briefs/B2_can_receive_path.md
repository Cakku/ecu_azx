# Brief B2 — CAN receive path and the powertrain TouCAN

Issue: **#12**. Prerequisite: brief A1 merged; A2's TouCAN register notes
(`re/findings/mpc5xx_registers.md`) help. No `sudo`.

Read `00_common_rules.md`, `docs/02` §7 (CAN tables) and
`docs/05_flexfuel_design.md` §3.1 first.

## Starting facts (VERIFIED-STATIC)
- TouCAN base table at 0x2BC38: 0x707080 (A), 0x707480 (B), 0x707880 (C).
- Pointer at 0x2BC50 -> CAN configuration structure at 0x2BF50.
- RX table at 0x2BC90: header + 21 entries `index, 0x01mmnn08, 4, id`
  (ids 0x1A0 0x5A0 0x4A0 0x440 0x540 0x320 0x442 0x1AC 0x0C2 0x050 0x51A
  0x5E0 0x390 0x38A 0x7FF 0x7FF 0x2A0 0x368 0x7FF 0x7FF 0x5C0); TX table at
  0x2BDF0 (Motor_1..7 etc.). Both tables are referenced from code at file
  0x324D4 and 0x1345B8 (`find_abs_refs.py --range 0x2BCA0 0x2BF00`).
- TouCAN message buffers start at module base + 0x80, 16 bytes each.

## Tasks
1. Decode the configuration structure at 0x2BF50 and the meaning of the
   `0x01mmnn08` field (module index? buffer number? DLC?) by reading the init
   code that programs the TouCAN message buffer ID/control words from these
   tables. Determine which module carries the powertrain frames (the one whose
   buffers receive 0x1A0/0x4A0/0x440) and what B and C are used for
   (second bus? diagnostics?).
2. Find the receive handling (IFLAG polling or interrupt) and follow the
   copy of each message buffer into RAM. Produce a table: RX entry -> module,
   buffer, RAM data address (8 bytes), new-data/timeout flags, and the first
   consumer function of the data (for 0x1A0 name it if the FR helps, e.g.
   wheel speeds).
3. Determine precisely what has to change to repurpose a 0x7FF slot for a new
   id (table bytes, any mask registers RXGMSK/RX14MSK/RX15MSK, buffer
   enable), and where its data will appear in RAM. Write the step list into
   `docs/05_flexfuel_design.md` §3.1 (tagged) and `re/findings/can.md`.
4. Check how TX buffers are allocated so the new RX slot cannot collide.
5. Add symbols (`can_cfg_struct`, `can_rx_isr`/`can_rx_poll`, `can_rx_buf_*`,
   `can_init_mb`) to `re/symbols.csv`; commit on `agent/B2`; comment on #12.

## Acceptance
For Bremse_1 (0x1A0) the RAM buffer address and its consumer are named with
evidence, and the slot-repurposing procedure is written so brief-level work
in Phase 2 (#22) can test it on the bench without further RE.
