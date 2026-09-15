# Brief B4 — vkKraQu variant byte and the EEPROM block handler

Issue: **#18**. Prerequisite: brief A1 merged; A2's FR index and QSMCM
register notes help. No `sudo`.

Read `00_common_rules.md` first.

## Tasks
1. **vkKraQu.** In newer MED9.1 software a fuel-quality variant byte selects
   between map sets (~17 maps). Community patches for 1K8907115F/L NOP a
   `stb r10,-0x1122(r13)` that writes it. Search our firmware for an
   equivalent: a byte, written from CAN or coding, consumed by map-pointer
   selection (functions choosing between two calibration pointers). Use the
   FR index ("Variantenkriterium", "Kraftstoffqualität") for the module
   name. Conclude present/absent with evidence; if present, list consumers
   and the maps switched. Write `re/findings/variants.md`.
2. **EEPROM.** The QSMCM (SPI) at 0x705000 has 49 references. Trace the SPI
   driver to the EEPROM routines (ST M95160-class, 2 KB; community: blocks
   with a byte-sum checksum), the block table (EEP_CONF), read at start-up,
   write at key-off, and the RAM mirror of each block. Determine whether a
   spare block or spare bytes exist and how a runtime write is triggered.
   Also check whether the external SRAM is battery-backed (RAM init table at
   0x5C2E78 and the start-up clearing logic tell whether 0x800000-0x807FFF is
   cleared on every start). Write `re/findings/eeprom.md`.
3. Add symbols; update `docs/05_flexfuel_design.md` §3.8 with the chosen
   persistence route (tagged); commit on `agent/B4`; comment on #18.

## Acceptance
Present/absent decision for vkKraQu with evidence; EEPROM read/write path
understood at least statically, and a concrete proposal for storing one byte
(E%) with its checksum implications.
