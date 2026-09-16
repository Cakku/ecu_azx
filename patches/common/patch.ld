/*
 * patch.ld - the linker script every MED9.1.1 flash patch links with.
 *
 * Generic: nothing in here names a patch.  Placement comes from the command
 * line (patches/common/patch.mk passes all three; there are no defaults, see
 * the note above SECTIONS):
 *
 *     ld.lld -m elf32ppc -T ../../common/patch.ld \
 *            --defsym=PATCH_FLASH=0x00150000 \
 *            --defsym=PATCH_RAM=0x007FFB00 \
 *            --defsym=PATCH_RAM_SIZE=0x40 ...
 *
 * FLASH  0x150000-0x1AFFFF is 384 KiB of 0xFF inside the checksummed 64 KiB
 *        blocks (docs/02_memory_map.md section 5, docs/06_patch_pipeline.md
 *        section 3).  `tools/checksum.py fix` must run over the patched image
 *        afterwards; `tools/patch_apply.py` does that for you.
 * RAM    is the patch's private block.  It must have been proven unused -
 *        statically and across ignition cycles - before anything is flashed
 *        (issue #23 / brief C2).
 *
 * Section rules (docs/04_re_guidelines.md section 7):
 *   - .text and .rodata live in flash; there is no startup code, so
 *   - .data must be EMPTY: nothing copies initialised data to RAM;
 *   - .bss lives in the patch RAM block and is NOT zeroed by anyone, so patch
 *     code has to detect its own cold start (see patches/ff_counter);
 *   - no small-data sections at all: they would be addressed through r2/r13,
 *     which belong to the ECU.
 *
 * lld quirks that cost time and must not be "cleaned up" (docs/03_tooling.md
 * section 3.1):
 *   - a section header needs a space before the colon (`.srodata : {`),
 *     otherwise lld reports "malformed number";
 *   - .got/.got2/.plt must NOT appear in /DISCARD/: ld.lld 23.1.1 segfaults in
 *     Writer::finalizeSections() when a synthetic section is discarded.  They
 *     stay empty because the code is built -fno-pic, and `make check` compares
 *     the blob size with __patch_flash_end to prove nothing else crept in.
 */

OUTPUT_FORMAT("elf32-powerpc")
OUTPUT_ARCH(powerpc)

/*
 * PATCH_FLASH, PATCH_RAM and PATCH_RAM_SIZE are REQUIRED on the command line.
 * There are deliberately no defaults here, because in ld.lld a script
 * assignment silently beats the --defsym it is meant to fall back to:
 *
 *     PATCH_RAM = DEFINED(PATCH_RAM) ? PATCH_RAM : 0x00807F00;
 *
 * evaluates to the default while the script computes addresses, and only
 * afterwards does --defsym overwrite the symbol's value in the symbol table.
 * The result is a blob linked at the default with a symbol table that says
 * otherwise - a silent wrong answer, which is the worst kind.  VERIFIED-STATIC
 * 2026-09-16 (brief C1): with the idiom above, --defsym=PATCH_RAM=0x806000
 * produced `.bss` at 0x807F00 and `PATCH_RAM = 0x806000` in the same ELF;
 * without it, `.bss` lands at 0x806000.  So: no defaults, and an undefined
 * PATCH_* fails the link loudly.  patches/common/patch.mk always passes all
 * three (from patch.json's "build" section).
 */

/* Bounds of the free flash area, so a mis-typed PATCH_FLASH fails the link
 * instead of overwriting stock code (docs/06_patch_pipeline.md section 3). */
PATCH_FLASH_AREA_START = 0x00150000;
PATCH_FLASH_AREA_END   = 0x001B0000;

ENTRY(__patch_entry)

SECTIONS
{
    . = PATCH_FLASH;
    __patch_entry = .;

    .text : {
        KEEP(*(.text.entry))
        *(.text .text.*)
        . = ALIGN(4);
    }

    .rodata : {
        *(.rodata .rodata.*)
        . = ALIGN(4);
    }

    /* Must stay empty - see the header comment and the ASSERTs below. */
    .sdata   : { *(.sdata .sdata.* .sdata2 .sdata2.*) }
    .srodata : { *(.srodata .srodata.*) }
    .data    : { *(.data .data.*) }

    __patch_flash_end  = .;
    __patch_flash_size = __patch_flash_end - PATCH_FLASH;

    . = PATCH_RAM;

    .bss (NOLOAD) : {
        __bss_start = .;
        /* A patch's documented state block goes first, so its layout is the
         * one written in the patch README and read by the logger.  Declare it
         * with __attribute__((section(".bss.patch_state"))). */
        KEEP(*(.bss.patch_state))
        *(.sbss .sbss.* .bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    }

    __patch_ram_size = __bss_end - __bss_start;

    /DISCARD/ : {
        *(.comment)
        *(.note .note.*)
        *(.eh_frame .eh_frame_hdr)
        *(.gcc_except_table*)
    }

    ASSERT(SIZEOF(.sdata) == 0,
           "small data section is not empty: it would be addressed through r2/r13")
    ASSERT(SIZEOF(.srodata) == 0,
           "small read-only data is not empty: it would be addressed through r2/r13")
    ASSERT(SIZEOF(.data) == 0,
           "writable initialised data cannot exist: nothing copies it to RAM. Make it const.")
    ASSERT(__patch_ram_size <= PATCH_RAM_SIZE,
           "the patch .bss is larger than PATCH_RAM_SIZE")
    ASSERT(PATCH_FLASH >= PATCH_FLASH_AREA_START,
           "PATCH_FLASH is below the free flash area 0x150000-0x1AFFFF")
    ASSERT(__patch_flash_end <= PATCH_FLASH_AREA_END,
           "the patch code runs past the end of the free flash area 0x1AFFFF")
}
