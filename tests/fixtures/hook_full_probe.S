/*
 * Test fixture for HOOK_FULL (patches/common/hooks.S), built and run only by
 * tests/test_patch_framework.py.  It is NOT part of any patch and is never
 * applied to an image: the test links it at 0x160000 and writes it straight
 * into the emulator's memory.
 *
 * Three symbols:
 *   test_full_hook   a HOOK_FULL trampoline around clobber_all, tail-branching
 *                    to the same stock leaf the Flash-1 hook uses
 *   clobber_all      destroys every register HOOK_FULL claims to preserve
 *   xer_probe        calls the trampoline and records XER before and after it,
 *                    read with `mfxer` in the guest.  That indirection is
 *                    necessary: Unicorn's uc_reg_read(UC_PPC_REG_XER) drops
 *                    SO/OV/CA, so the Python view of XER cannot decide whether
 *                    the trampoline restored it (emu/README.md, limitation 1).
 */
#include "med9_stock.h"
#include "hooks.S"

    HOOK_FULL test_full_hook, clobber_all, MED9_CLR_RAM_7FE889_800E18

    .section .text.clobber_all, "ax", @progbits
    .globl clobber_all
    .type  clobber_all, @function
    .align 2
clobber_all:
    li    0, -1
    li    3, -1
    li    4, -1
    li    5, -1
    li    6, -1
    li    7, -1
    li    8, -1
    li    9, -1
    li   10, -1
    li   11, -1
    li   12, -1
    mtctr 0
    mtxer 0
    mtcr  0
    blr
    .size clobber_all, . - clobber_all

/*
 * xer_probe(r3 = ignored, r4 = scratch address)
 *   scratch[0] = XER as the guest sees it before the trampoline
 *   scratch[4] = XER as the guest sees it after it
 * r31 holds the scratch pointer across the call because a volatile register
 * would beg the question the test is asking.
 */
    .section .text.xer_probe, "ax", @progbits
    .globl xer_probe
    .type  xer_probe, @function
    .align 2
xer_probe:
    stwu  1, -32(1)
    mflr  0
    stw   0, 28(1)
    stw  31, 24(1)
    mr   31, 4
    mfxer 5
    stw   5, 0(31)
    bl    test_full_hook
    mfxer 5
    stw   5, 4(31)
    lwz  31, 24(1)
    lwz   0, 28(1)
    mtlr  0
    addi  1, 1, 32
    blr
    .size xer_probe, . - xer_probe
