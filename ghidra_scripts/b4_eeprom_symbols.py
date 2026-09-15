#!/usr/bin/env python3
# Name the SPI/QSPI, EEPROM and EEP_CONF block-manager symbols found by B4.
# @category MED9
# @runtime PyGhidra
"""b4_eeprom_symbols.py -- apply agent B4's names and plate comments.

Everything this script names is documented, with its evidence, in
``re/findings/eeprom.md`` and ``re/findings/variants.md`` (issue #18).  The
confidence tag travels in the plate comment, which is what
``export_symbols.py`` reads, so run this first and ``export_symbols.py``
afterwards to land the rows in ``re/symbols.csv``.

Run it as a Ghidra post-script::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript b4_eeprom_symbols.py

or standalone::

    ./.venv/bin/python ghidra_scripts/b4_eeprom_symbols.py \
        --project-dir /tmp/ghidra_B4 --project-name med9
"""
from __future__ import annotations

import os
import sys

# (cpu_addr, kind, name, plate comment)
#   kind: "func" -> make/rename a function, "label" -> a data label
SYMBOLS = [
    # ---- boot-module QSPI driver (runs under r2 = 0x017FF0) ----------------
    (0x017838, "func", "qspi_configure",
     "VERIFIED-STATIC QSPI setup from a 5-byte device record: PQSPAR=0x7B, "
     "DDRQS=0x7E, PORTQS|=0x78 (PCS idle high), SPCR0/1/2 from cfg[0..4]. "
     "B4 #18, re/findings/eeprom.md 1.4"),
    (0x0178DC, "func", "qspi_transfer",
     "VERIFIED-STATIC qspi_transfer(queue, n<=32, rxbuf, wait): copies n "
     "{u16 data, u8 cmd} entries to TXRAM 0x705180 / CMDRAM 0x7051C0, sets "
     "ENDQP=n-1 (5-bit field, so the queue is 32 deep), SPE, polls SPIF, "
     "copies RXRAM 0x705140. B4 #18, re/findings/eeprom.md 1.3"),
    (0x0179EC, "func", "qspi_transfer_blocking",
     "VERIFIED-STATIC qspi_transfer with wait=1. B4 #18"),
    (0x017A10, "func", "spi_find_device",
     "VERIFIED-STATIC walks the 15-byte SPI device records at *(0x7F83A0) "
     "for record[0] == (id | 0x80); count at file 0x0102EA = 4. B4 #18"),
    (0x017A84, "func", "eeprom_test_wel",
     "VERIFIED-STATIC M95xxx write-enable-latch self-test: WRDI/RDSR/WREN/"
     "RDSR queue at *(0x7F83A4); passes only if status bit 1 (WEL) is 0 after "
     "WRDI and 1 after WREN. B4 #18, re/findings/eeprom.md 1.6"),
    (0x017B34, "func", "eeprom_dump_probe",
     "VERIFIED-STATIC reads the whole EEPROM twice, once with a 1-byte and "
     "once with a 2-byte address, into two buffers: a device-width probe. "
     "B4 #18"),
    (0x017CF0, "func", "qspi_loopback_test",
     "VERIFIED-STATIC QSPI self-test with SPCR3.LOOPQ set; compares RX with "
     "the transmitted 5-entry pattern. Error codes 1/2/3. B4 #18"),
    (0x017E14, "func", "spi_select_device_table",
     "VERIFIED-STATIC picks one of six SPI device tables by hardware variant "
     "and stores 0x7F83A0/A4/A8. Operands are r2=0x017FF0 relative, so Ghidra's "
     "DAT_005C2xxx labels are wrong by 0x5B2D00. B4 #18, eeprom.md 1.5"),
    (0x011898, "func", "ext_sram_probe",
     "VERIFIED-STATIC sizes the CS1 external SRAM by writing 0x5AA53CC3 at "
     "0x800000 and 0xA55AC33C at 0x808000 and checking for aliasing; result "
     "0x41 (32 KB) or 0x44 (64 KB) in 0x7F8012. It SAVES and RESTORES both "
     "words, i.e. the firmware treats external SRAM as retained. "
     "B4 #18, re/findings/eeprom.md 6"),

    # ---- boot-module data (file offset == cpu address) --------------------
    (0x0102E9, "label", "spi_eeprom_device_id",
     "VERIFIED-STATIC byte = 3: the SPI device id of the EEPROM, used by "
     "eeprom_test_wel. Addressed as r2-0x7D07 with the boot r2. B4 #18"),
    (0x0102EA, "label", "spi_device_count",
     "VERIFIED-STATIC byte = 4: number of records in the SPI device table. "
     "B4 #18"),
    (0x0102EB, "label", "tbl_spi_devices_v0",
     "VERIFIED-STATIC 4 x 15-byte SPI device records, variant 0 (MPC561 "
     "hardware). byte0 = id|0x80 when enabled; bytes 1..5 = the QSPI config. "
     "Five more variants at 0x010327/0x010363/0x01039F/0x0103DB/0x010417. "
     "B4 #18"),
    (0x010417, "label", "tbl_spi_devices_v5",
     "VERIFIED-STATIC the default (last else) SPI device table variant. "
     "B4 #18"),
    (0x0104BA, "label", "qspi_eeprom_wel_test_queue",
     "VERIFIED-STATIC 7 x {u16 data, u8 cmd}: WRDI, RDSR(CONT), dummy, WREN, "
     "RDSR(CONT), dummy, WRDI. B4 #18"),
    (0x0105F0, "label", "tbl_spi_api_pointers",
     "VERIFIED-STATIC 4 function pointers: qspi_configure, "
     "qspi_transfer_blocking, spi_find_device, eeprom_dump_probe. B4 #18"),

    # ---- application M95160 driver ----------------------------------------
    (0x085888, "func", "eeprom_spi_config",
     "VERIFIED-STATIC SPCR0 = 0xA000 | f_sys/2.5MHz -> master, 8 bits, "
     "CPOL=0 CPHA=0 (SPI mode 0), SCK ~1.25 MHz; PQSPAR=0x7B, DDRQS=0x7E, "
     "PORTQS=0x78. B4 #18, re/findings/eeprom.md 2"),
    (0x085920, "func", "eeprom_qspi_xfer",
     "VERIFIED-STATIC application copy of qspi_transfer (32-entry queue). "
     "B4 #18"),
    (0x085A8C, "func", "eeprom_write_byte",
     "VERIFIED-STATIC M95160 byte write: WREN(0x06), WRITE(0x02), addr hi, "
     "addr lo, data -- all with command byte 0x0E/0x8E, i.e. PCS0 low, so the "
     "EEPROM is on PCS0 -- then polls RDSR(0x05) until WIP (bit 0) clears. "
     "B4 #18"),
    (0x085B54, "func", "eeprom_write_bytes",
     "VERIFIED-STATIC byte-at-a-time write loop over eeprom_write_byte. "
     "B4 #18"),
    (0x085BC0, "func", "eeprom_read_bytes",
     "VERIFIED-STATIC M95160 read in chunks of <=0x1D bytes: READ(0x03) + "
     "16-bit address + up to 29 dummy entries = the full 32-entry queue. "
     "Error count in 0x7FD150. B4 #18"),
    (0x09D800, "func", "eeprom_spi_config_copy2",
     "VERIFIED-STATIC second copy of eeprom_spi_config. B4 #18"),
    (0x09D894, "func", "eeprom_qspi_xfer_copy2",
     "VERIFIED-STATIC second copy of eeprom_qspi_xfer. B4 #18"),
    (0x09D9EC, "func", "eeprom_read_bytes_copy2",
     "VERIFIED-STATIC second copy of eeprom_read_bytes; error count 0x7FAAD0. "
     "B4 #18"),
    (0x085F44, "func", "eeprom_read_immo_block",
     "VERIFIED-STATIC reads EEPROM 0x280, 32 bytes, into the immobiliser "
     "mirror 0x7FD2CC. B4 #18"),
    (0x0894A0, "func", "eeprom_write_verify_immo_block",
     "VERIFIED-STATIC writes then reads back and compares a 32-byte block at "
     "EEPROM 0x280 or 0x2A0. B4 #18"),

    # ---- EEP_CONF block manager -------------------------------------------
    (0x06131C, "func", "nvm_block_request",
     "VERIFIED-STATIC nvm(blk, off, len, mode, buf, handle) -- the EEP_CONF "
     "API. Called via lis/addi + mtlr + blrl, never with bl. Stage fields "
     "with len>0 (returns 2), then commit with len=0, buf=0, handle!=0 "
     "(returns 1). B4 #18, re/findings/eeprom.md 3.1"),
    (0x05FB64, "func", "nvm_copy_address",
     "VERIFIED-STATIC eepAddr(blk, copy) = table[blk].addr + "
     "copy * ceil(len/32) * 32. B4 #18"),
    (0x05FCC8, "func", "nvm_device_read_sm",
     "VERIFIED-STATIC per-block device read state machine (0x7FADAC): calls "
     "(*0x7FAB70)(eepAddr, len, buf, &status), verifies the checksum, retries "
     "and falls back to the second copy. B4 #18"),
    (0x060524, "func", "nvm_device_write_sm",
     "VERIFIED-STATIC per-block device write state machine. B4 #18"),
    (0x060A68, "func", "nvm_queue_pump",
     "VERIFIED-STATIC EEP_CONF request-queue state machine (0x7FADAB), "
     "states 0x20-0x27. B4 #18, re/findings/eeprom.md 3.5"),
    (0x061944, "func", "nvm_pump_from_background",
     "VERIFIED-STATIC thin wrapper that drives nvm_queue_pump; called from "
     "the background tasks at 0x1205A0 and 0x4328E4. B4 #18"),
    (0x0619AC, "func", "nvm_checksum_verify",
     "VERIFIED-STATIC returns (sum16(payload[0..len-3]) + "
     "u16be(payload[len-2])) == 0xFFFF. Block 0 is exempt. B4 #18, "
     "re/findings/eeprom.md 3.4"),
    (0x061A48, "func", "nvm_checksum_generate",
     "VERIFIED-STATIC writes ~sum16(payload[0..len-3]) as a big-endian u16 "
     "at payload offset len-2, in place. B4 #18"),
    (0x061AC4, "func", "nvm_checksum_generate_copy",
     "VERIFIED-STATIC same as nvm_checksum_generate but copies src->dst while "
     "summing. B4 #18"),
    (0x061CD0, "func", "nvm_block_read_with_retries",
     "VERIFIED-STATIC reads one block through (*0x7FAB70), up to 3 attempts, "
     "then hands over to the pointer at 0x7FAB78. B4 #18"),
    (0x062280, "func", "nvm_read_all_blocks",
     "VERIFIED-STATIC walks every EEP_CONF block, reads it from the device "
     "and verifies its checksum -- the start-up read (FR EEPINIKW). Reached "
     "through a pointer, no static caller. B4 #18"),
    (0x062740, "func", "nvm_write_all_blocks",
     "VERIFIED-STATIC walks every EEP_CONF block, regenerates the checksum "
     "and writes it to the device -- the write-back. Reached through a "
     "pointer, no static caller. B4 #18"),

    # ---- EEP_CONF data -----------------------------------------------------
    (0x0B2FF0, "label", "tbl_eep_conf",
     "VERIFIED-STATIC 32 x 12-byte EEPROM block descriptors: +0 u16 mirror "
     "offset from 0x7F9E80 (0xFFFF none), +2 u16 EEPROM address, +6 u16 "
     "default-table offset, +8 u16 flags, +10 u8 length incl. the 2-byte "
     "checksum. Covers 0x000-0x7FF with no gap. B4 #18, tools/eeprom_map.py"),
    (0x0B3184, "label", "ptr_eep_mirror_base",
     "VERIFIED-STATIC = 0x7F9E80, base of the 0x600-byte EEPROM RAM mirror. "
     "B4 #18"),
    (0x0B318C, "label", "ptr_eep_defaults",
     "VERIFIED-STATIC = 0x0B3238, the per-block default/init values. B4 #18"),
    (0x0B3196, "label", "eep_page_size",
     "VERIFIED-STATIC = 0x20, the M95160 page size used to space block "
     "copies. B4 #18"),
    (0x0B3238, "label", "tbl_eep_defaults",
     "VERIFIED-STATIC default contents of every EEP_CONF block, indexed by "
     "descriptor field +6. B4 #18"),
    (0x0B3228, "label", "ptr_nvm_checksum_verify",
     "VERIFIED-STATIC -> nvm_checksum_verify. B4 #18"),
    (0x0B322C, "label", "ptr_nvm_checksum_generate",
     "VERIFIED-STATIC -> nvm_checksum_generate. B4 #18"),
    (0x0B3230, "label", "ptr_nvm_checksum_generate_copy",
     "VERIFIED-STATIC -> nvm_checksum_generate_copy. B4 #18"),

    # ---- KWP variant-coding path ------------------------------------------
    (0x0A376C, "func", "kwp_sid_3B_dispatch",
     "VERIFIED-STATIC record dispatcher for SID 0x3B: looks the record local "
     "id up in tbl_kwp_3b_records and runs each field descriptor. B4 #18, "
     "re/findings/variants.md 4"),
    (0x0A2C9C, "label", "tbl_kwp_3b_records",
     "VERIFIED-STATIC SID 0x3B record table, 8-byte entries {localId, "
     "nFields, 0x01, 0x00, ptr}, zero-count terminated. Only two records "
     "exist: 0x9A (14 B, handler 0x0A25A8) and 0xBC (7 B, handler 0x0A2938). "
     "B4 #18"),
    (0x0A2938, "func", "kwp_3b_bc_coding_write",
     "VERIFIED-STATIC handler for SID 0x3B record 0xBC: stores 6 bytes at "
     "EEP_CONF block 7 offset +2 and a u16 at +12, then commits block 7. The "
     "variant-coding write path; nothing in the fuelling path reads it. "
     "B4 #18, re/findings/variants.md 4"),
    (0x0A25A8, "func", "kwp_3b_9a_write",
     "HYPOTHESIS handler for SID 0x3B record 0x9A (14 bytes); role not "
     "established. B4 #18"),

    # ---- RAM ---------------------------------------------------------------
    (0x7F8012, "label", "ext_sram_size_code",
     "VERIFIED-STATIC 0x41 = 32 KB, 0x44 = 64 KB external SRAM, written by "
     "ext_sram_probe. B4 #18"),
    (0x7F83A0, "label", "ptr_spi_device_table",
     "VERIFIED-STATIC -> the active 4 x 15-byte SPI device table. B4 #18"),
    (0x7F83A4, "label", "ptr_spi_wel_test_queue",
     "VERIFIED-STATIC -> the 7-entry EEPROM WREN/WRDI test queue. B4 #18"),
    (0x7F83A8, "label", "ptr_spi_eeprom_cmd_template",
     "VERIFIED-STATIC -> the command-byte template used by eeprom_dump_probe. "
     "B4 #18"),
    (0x7F9E80, "label", "eep_mirror",
     "VERIFIED-STATIC 0x600-byte RAM mirror of the SPI EEPROM, laid out by "
     "tbl_eep_conf field +0. Block 8's payload starts at 0x7F9F80. B4 #18"),
    (0x7FAB70, "label", "ptr_eep_device_read",
     "VERIFIED-STATIC (*this)(eepAddr, len, buf, &status) -- the EEPROM read "
     "primitive. Never written by a statically resolvable instruction, so the "
     "binding to eeprom_read_bytes is HYPOTHESIS. B4 #18"),
    (0x7FAB74, "label", "ptr_eep_device_write",
     "VERIFIED-STATIC the EEPROM write primitive, same shape. B4 #18"),
    (0x7FADAB, "label", "nvm_queue_state",
     "VERIFIED-STATIC state variable of nvm_queue_pump (0x20-0x27). B4 #18"),
    (0x7FADAC, "label", "nvm_device_state",
     "VERIFIED-STATIC state variable of the per-block device state machines "
     "(0x40-0x44). B4 #18"),
    (0x7FD150, "label", "eeprom_read_error_count",
     "VERIFIED-STATIC accumulated transfer-error count of eeprom_read_bytes. "
     "B4 #18"),
    (0x7FD2CC, "label", "immo_eep_block_280_mirror",
     "VERIFIED-STATIC private 32-byte mirror of EEPROM block 0x280 kept by "
     "the immobiliser module, which bypasses the EEP_CONF manager. B4 #18"),
    (0x7FD2EC, "label", "immo_eep_block_260_mirror",
     "VERIFIED-STATIC private 32-byte mirror of EEPROM block 0x260. B4 #18"),
]


def apply(program, monitor=None):
    from ghidra.program.model.symbol import SourceType

    af = program.getAddressFactory().getDefaultAddressSpace()
    fm = program.getFunctionManager()
    listing = program.getListing()
    st = program.getSymbolTable()
    memory = program.getMemory()

    named = skipped = 0
    for cpu, kind, name, comment in SYMBOLS:
        addr = af.getAddress(cpu)
        if not memory.contains(addr):
            print("[b4_eeprom_symbols] skipped 0x%06X %s: no memory block" % (cpu, name))
            skipped += 1
            continue
        target = None
        if kind == "func":
            func = fm.getFunctionAt(addr)
            if func is None:
                try:
                    from ghidra.app.cmd.function import CreateFunctionCmd
                    CreateFunctionCmd(addr).applyTo(program)
                except Exception as exc:                       # noqa: BLE001
                    print("[b4_eeprom_symbols] 0x%06X: createFunction failed (%s)"
                          % (cpu, exc))
                func = fm.getFunctionAt(addr)
            if func is not None:
                func.setName(name, SourceType.USER_DEFINED)
                target = addr
            else:
                kind = "label"
        if kind == "label":
            st.createLabel(addr, name, SourceType.USER_DEFINED)
            target = addr
        if target is not None:
            listing.setComment(target, 3, comment)             # 3 = PLATE_COMMENT
            named += 1
    print("[b4_eeprom_symbols] %d symbols named, %d skipped" % (named, skipped))
    return named, skipped


def _run_as_ghidra_script():
    apply(currentProgram)                                      # noqa: F821


def _run_standalone(argv):
    def flag(nm, default):
        for i, a in enumerate(argv):
            if a == nm and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(nm + "="):
                return a.split("=", 1)[1]
        return default

    project_dir = os.path.abspath(flag("--project-dir", "ghidra_projects"))
    project_name = flag("--project-name", "med9")
    program_name = flag("--program", "passat_azx_ori.bin")

    import pyghidra
    pyghidra.start(verbose=False)
    from ghidra.base.project import GhidraProject

    project = GhidraProject.openProject(project_dir, project_name, True)
    try:
        program = project.openProgram("/", program_name, False)
        tx = program.startTransaction("b4_eeprom_symbols")
        try:
            apply(program)
        finally:
            program.endTransaction(tx, True)
        project.save(program)
    finally:
        project.close()
    return 0


def _running_inside_ghidra():
    try:
        currentProgram                                         # noqa: F821,B018
    except NameError:
        return False
    return True


if _running_inside_ghidra():
    _run_as_ghidra_script()
elif __name__ == "__main__":
    sys.exit(_run_standalone(sys.argv[1:]))
