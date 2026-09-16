/*
 * hooks.S - trampoline macros for MED9.1.1 "call redirect" hooks.
 *
 * Hook technique 1 of docs/06_patch_pipeline.md section 4: one stock
 * `bl <orig>` word is replaced by `bl <trampoline>`; the trampoline saves what
 * the site needs, calls our C function, restores, and tail-branches to the
 * original target so the stock call still happens.  Exactly one flash word of
 * stock code changes.
 *
 * Include this file from a patch's own .S (it only defines macros, it emits
 * nothing), then instantiate one trampoline per hook:
 *
 *     #include "../common/med9_stock.h"
 *     #include "../common/hooks.S"
 *         HOOK_TAIL ff_counter_hook, ff_counter_tick, MED9_CLR_RAM_7FE889_800E18
 *
 * Register discipline (docs/06 section 4, re/findings/scheduler.md section 7):
 * r2, r13, r14-r31 and the FP registers are never touched - r2/r13 are the
 * ECU's small-data bases and `tools/blobdis.py --check-sda` fails the build if
 * they appear.  r1 stays 8-byte aligned; both frames below are multiples of 16.
 *
 * -------------------------------------------------------------------------
 * WHY `ba` AND NOT `b` (verified 2026-09-16, brief C1)
 *
 * The GNU/LLVM PowerPC assembler reads a *numeric* branch operand as a
 * DISPLACEMENT, not as a target address.  Assembling `b 0x0011F02C` with
 * clang 23.1.1 yields the word 0x4811F02C, i.e. a branch to pc + 0x11F02C -
 * silently the wrong place.  `.set stock, 0x0011F02C` + `b stock` does the
 * same thing.  Only an *undefined* symbol resolved by the linker (e.g.
 * `--defsym=stock=0x11F02C`) produces the intended relative word.
 *
 * Rather than couple every patch Makefile to a list of --defsym stock
 * addresses, the trampolines use `ba` (branch absolute, AA=1), whose operand
 * IS the target address: `ba 0x0011F02C` assembles to 0x4811F02E and the
 * target is readable straight out of the blob disassembly.  Semantics are
 * identical for a fixed stock address, and there is no relocation to get
 * wrong.  Reach: the 26-bit signed LI field covers 0x000000-0x1FFFFFF, which
 * contains the whole external flash and the on-chip flash at 0x404000+.
 * -------------------------------------------------------------------------
 */
#ifndef MED9_HOOKS_S
#define MED9_HOOKS_S

/*
 * HOOK_TAIL name, c_func, orig
 *
 * The cheap trampoline, for a `bl` site where r3-r12, CR, CTR and XER are all
 * dead - which is the case between two argument-less `bl`s in a flat ERCOSEK
 * raster task (re/findings/scheduler.md section 7).  It saves only LR, because
 * `bl c_func` destroys it and the stock leaf's `blr` needs it back.
 *
 * Frame (16 bytes, r1 stays 8-byte aligned):
 *     0(r1)   back chain (written by stwu)
 *     4(r1)   free - the EABI LR save slot c_func writes if it is not a leaf
 *     8(r1)   free
 *    12(r1)   our saved LR
 *
 * On entry LR = the return address inside the hooked task; on the `ba` it has
 * been restored, so the stock leaf's `blr` returns to the task as before.
 */
.macro HOOK_TAIL name, c_func, orig
    .section .text.\name, "ax", @progbits
    .globl \name
    .type  \name, @function
    .align 2
\name:
    stwu  1, -16(1)
    mflr  0
    stw   0, 12(1)
    bl    \c_func
    lwz   0, 12(1)
    mtlr  0
    addi  1, 1, 16
    ba    \orig
    .size \name, . - \name
.endm

/*
 * HOOK_FULL name, c_func, orig
 *
 * The safe trampoline, for a `bl` site whose live registers are unknown or
 * known to matter.  Preserves the whole EABI volatile set the hooked code can
 * observe: r0, r3-r12, CR, LR, CTR and XER.  r2, r13 and r14-r31 are
 * non-volatile or reserved and are not touched; no FP state is saved, so
 * c_func must not use floating point (-msoft-float enforces that).
 *
 * Frame (80 bytes, 16-byte aligned):
 *     0(r1)   back chain (written by stwu)
 *     4(r1)   free - EABI LR save slot for c_func
 *     8(r1)   free - EABI CR save slot for c_func
 *    12(r1)   padding
 *    16(r1)   r0
 *    20(r1)   LR
 *    24(r1)   CR
 *    28(r1)   CTR
 *    32(r1)   XER
 *    36(r1)   r3 .. 72(r1) r12
 *    76(r1)   padding
 */
.macro HOOK_FULL name, c_func, orig
    .section .text.\name, "ax", @progbits
    .globl \name
    .type  \name, @function
    .align 2
\name:
    stwu  1, -80(1)
    stw   0, 16(1)          /* r0 first: mflr below clobbers it */
    mflr  0
    stw   0, 20(1)
    mfcr  0
    stw   0, 24(1)
    mfctr 0
    stw   0, 28(1)
    mfxer 0
    stw   0, 32(1)
    stw   3, 36(1)
    stw   4, 40(1)
    stw   5, 44(1)
    stw   6, 48(1)
    stw   7, 52(1)
    stw   8, 56(1)
    stw   9, 60(1)
    stw  10, 64(1)
    stw  11, 68(1)
    stw  12, 72(1)
    bl    \c_func
    lwz   3, 36(1)
    lwz   4, 40(1)
    lwz   5, 44(1)
    lwz   6, 48(1)
    lwz   7, 52(1)
    lwz   8, 56(1)
    lwz   9, 60(1)
    lwz  10, 64(1)
    lwz  11, 68(1)
    lwz  12, 72(1)
    lwz   0, 32(1)
    mtxer 0
    lwz   0, 28(1)
    mtctr 0
    lwz   0, 24(1)
    mtcr  0
    lwz   0, 20(1)
    mtlr  0
    lwz   0, 16(1)          /* r0 last: nothing clobbers it after this */
    addi  1, 1, 80
    ba    \orig
    .size \name, . - \name
.endm

#endif /* MED9_HOOKS_S */
