/*
 * Linker script for MED9.1.1 flash patches.
 *
 * FLASH origin sits in the free area that begins after the end-of-code marker
 * at 0x144950 (docs/02_memory_map.md section 5).  Everything written there is
 * inside a checksummed 64 KiB block, so `tools/checksum.py fix` must run over
 * the patched image before it goes anywhere near an ECU.
 *
 * RAM origin is an EXAMPLE address in the external SRAM (CS1, 0x800000); the
 * highest static reference recorded so far is 0x805784.
 *
 * Both can be overridden without editing this file:
 *     ld.lld ... --defsym=PATCH_FLASH=0x146000 --defsym=PATCH_RAM=0x806100
 */

OUTPUT_FORMAT("elf32-powerpc")
OUTPUT_ARCH(powerpc)
ENTRY(hello_init)

PATCH_FLASH = DEFINED(PATCH_FLASH) ? PATCH_FLASH : 0x00145000;
PATCH_RAM   = DEFINED(PATCH_RAM)   ? PATCH_RAM   : 0x00806000;

SECTIONS
{
    . = PATCH_FLASH;

    .text : {
        KEEP(*(.text.entry))
        *(.text .text.*)
        . = ALIGN(4);
    }

    .rodata : {
        *(.rodata .rodata.*)
        . = ALIGN(4);
    }

    /*
     * These must stay empty.  If the compiler emitted small data, the ECU's
     * own r2/r13 bases would be used as if they were ours, silently
     * corrupting calibration or RAM.  Build with -msdata=none -G0 (GCC), or
     * rely on the LLVM defaults, and let the assertions below fail the link
     * if either toolchain still emits something.
     */
    .sdata   : { *(.sdata .sdata.* .sdata2 .sdata2.*) }
    .srodata : { *(.srodata .srodata.*) }
    .data    : { *(.data .data.*) }

    __patch_flash_end = .;

    . = PATCH_RAM;

    .bss (NOLOAD) : {
        __bss_start = .;
        *(.sbss .sbss.* .bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    }

    /*
     * Do NOT add .got/.got2/.plt here: ld.lld 23.1.1 segfaults in
     * Writer::finalizeSections() when a synthetic section is discarded.
     * They stay empty anyway because the code is built -fno-pic, and
     * `make check` proves the blob contains no GOT-relative access.
     */
    /DISCARD/ : {
        *(.comment)
        *(.note .note.*)
        *(.eh_frame .eh_frame_hdr)
        *(.gcc_except_table*)
    }

    ASSERT(SIZEOF(.sdata) == 0,
           "small data section is not empty: rebuild with -msdata=none -G0")
    ASSERT(SIZEOF(.srodata) == 0,
           "small read-only data is not empty: rebuild with -msdata=none -G0")
    ASSERT(SIZEOF(.data) == 0,
           "writable initialised data cannot live in flash: make it const")
}
