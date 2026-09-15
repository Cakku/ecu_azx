# Brief A2 — Reference documents and MPC5xx register facts

Issue: **#6**. Also resolves the register-level open questions in
`docs/02_memory_map.md` §3 (marked HYPOTHESIS). Runs now, no hardware.

Read `00_common_rules.md` first. No `sudo`.

## Tasks
1. Download into `documents/` (add anything > 10 MB to `.gitignore`):
   - Bosch MED9.1 Funktionsrahmen (TFSI edition):
     `https://files.s4wiki.com/docs/MED9.1%20TFSI.pdf` (~55 MB).
   - NXP MPC561/MPC563 Reference Manual `MPC561RM.pdf`
     (nxp.com/docs/en/data-sheet/MPC561RM.pdf) and, if reachable, the MPC500
     memory map summary and the MPC561/2/3/4 product brief.
   - Record URLs, dates and SHA-256 in `documents/SOURCES.md`.
2. `brew install poppler` (pdftotext) and extract text to
   `work/` (gitignored) for searching. Do not commit extracted text.
3. **FR index** `re/findings/fr_index.md`: for each of these topics list the
   FR module names, the key variables/maps and the PDF page numbers:
   injection quantity and time (KRKATE/TVUB/FKKVS and the FSI high-pressure
   path: rail pressure setpoint, injection window, pump volume limit), lambda
   targets and component protection, lambda adaptation (fra/frau/frao/rkat),
   ignition (ZWGRU, KFZW/KFZW2/KFZWOP, knock control dwkrz), start and
   afterstart enrichment, warm-up, CAN (CAN_CONF, CANECUR, message layouts of
   Motor_1..7 and the received Bremse/Getriebe/Kombi frames), tester
   communication (KWP services, measuring blocks, DDLI, security access),
   EEPROM (EEP_CONF), scheduler/task structure, variant coding
   (vkKraQu/Variantenkriterium if present in this FR edition), ROM/RAM checks.
   Mark which module names are confirmed by strings or tables in our dump
   (e.g. our identification block, the KWP SID list in `docs/02` §7) and which
   are assumptions because our software is the VR6 FSI variant.
4. **Register facts** `re/findings/mpc5xx_registers.md` from the reference
   manual, each with the manual section/page:
   - IMMR bit layout: PARTNUM/MASKNUM values for MPC561/562/563/564, FLEN,
     ISB encoding. Decide what `andi. 0xFFF1; ori 0x2` at file 0x1008-0x100C
     sets (our reading: ISB=1 -> base 0x400000). Record the PARTNUM value
     compared at file 0x1236C (0x35) and which parts have on-chip flash.
   - USIU memory controller BR/OR field layout; decode our values: BR0 =
     0x000103 / 0x00090B, OR0 in {0xFF800650, 0xFF8006FF, 0xFF800140,
     0xFF800130, 0xFF800120}, BR1 = 0x800403, OR1 in {0xFFFC0140..0xFFFC0180,
     0xFFFC0110, 0xFFFC0100}, BR2 = 0x900823, OR2 0xFFFC0110 / 0x0FFF1F00 (?),
     BR3 = 0xA00003, OR3 in {0xFFFF8C20, 0xFFFF8C30}. State region sizes, port
     sizes, wait states. Confirm or refute the "8 MB CS0 region, flash aliased
     at 0x400000-0x5FFFFF where no internal module responds" model.
   - Dual mapping DMBR/DMOR semantics; interpret DMBR = 0x70000001 and
     DMOR = 0x70000000 written at file 0x1250C-0x12514.
   - Exception vector prefix with MSR[IP]=1 (0xFFF00000) on a device with a
     24/26-bit external address bus: where do vectors come from in our
     configuration? (our MSR values: 0x3942, 0x2942).
   - TouCAN register map and message buffer layout (needed by brief B2).
   - QSMCM/SPI registers (EEPROM driver, brief B4), UC3F block structure and
     protection (candidate explanation for the missing 0x400000-0x403FFF).
   - Internal map offsets used in `docs/02` §3 (DECRAM, USIU, UC3F control,
     IMB modules, CALRAM, SRAM) — confirm each against the manual.
5. Apply the results to `docs/02_memory_map.md`: upgrade or correct the
   HYPOTHESIS items in §3 with a dated note and the manual reference. Do not
   change VERIFIED-STATIC statements unless the manual proves them wrong; in
   that case add a "Corrections" entry.
6. Commit on `agent/A2`. Comment on #6 with the summary and where the index is.

## Not in scope
Reading the dump beyond quoting the values listed above; naming functions.
