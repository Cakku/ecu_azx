/*
 * ff_fuel: the three trampolines.
 *
 * All three sites are `bl` whose whole EABI volatile set is provably dead, so
 * all three use HOOK_TAIL (16-byte frame, saves LR only) and tail-branch with
 * `ba` to the function the site originally called, which therefore still runs,
 * in the same task, in the same order.  One flash word changes per site.
 *
 *   0x42247C  bl rksplit (0x41C3A0), engine-synchronous task_segment_a
 *             (0x4223B0).  re/findings/injection.md section 6.2: the task body
 *             is a flat list of argument-less `bl`, so r3-r12, CR, CTR and XER
 *             are dead and LR is free.  ff_rk_scale scales `rk` at 0x803038
 *             and the `ba` hands control to rksplit exactly as the `bl` did -
 *             with r1 and LR unchanged, so rksplit's `blr` returns into the
 *             task.
 *
 *   0x432940  bl nop_leaf_bd9e4 (0xBD9E4), task set A's 10 ms raster
 *             (0x4328E4).  re/findings/scheduler.md section 8.1: the target is
 *             a one-instruction empty function whose only caller in the whole
 *             image is this site, and both neighbours are argument-less `bl`.
 *
 *   0x12067C  bl clr_ram_7FE889_800E18 (0x11F02C), task set B's 10 ms raster
 *             (0x1205A0).  re/findings/scheduler.md section 8 - C1's site,
 *             re-used unchanged (ff_counter and ff_fuel are never co-flashed).
 *
 * Both raster tasks are hooked because exactly one task set is live and the
 * dump cannot say which (scheduler.md section 11.7).  ff_fuel_tick_a/b differ
 * only in the source byte they pass, and the arbitration in ff_fuel.c keeps
 * the tick rate at one per raster period even if both ever fired.
 */
#include "../../common/med9_stock.h"
#include "../../common/hooks.S"
#include "ff_state.h"

    HOOK_TAIL ff_fuel_rk_hook, ff_rk_scale,    MED9_RKSPLIT
    HOOK_TAIL ff_fuel_hook_a,  ff_fuel_tick_a, MED9_NOP_LEAF_BD9E4
    HOOK_TAIL ff_fuel_hook_b,  ff_fuel_tick_b, MED9_CLR_RAM_7FE889_800E18

/*
 * ---------------------------------------------------------------------------
 * ff_zw_hook - the ignition blend's consumer (brief E1, issue #34).
 *
 * The fourth hook is NOT a trampoline in the sense of the three above: there
 * is no stock call to tail-branch to, because the word it replaces is not a
 * branch.  This is hook technique 3 of docs/06_patch_pipeline.md section 4 -
 * an instruction patch - and the displaced instruction is re-done here.
 *
 *   0041D404  add   r3,r11,r12
 *   0041D408  extsb r10,r10
 *   0041D40C  add   r3,r3,r10        <-- becomes `bl ff_zw_hook`
 *   0041D410  cmpwi r3,0x7f          ; the stock s8 clamp, still runs
 *   ...
 *   0041D430  stb   r3,-0x2cdb(r13)  ; zwgru = 0x7FD315
 *
 * Why no frame and no register saves (re/findings/ignition.md section 11.1,
 * VERIFIED-STATIC):
 *
 *   - live at the site: r3 (the accumulator), r10 (the last term) and r1.
 *     r11 and r12 were both consumed by 0x41D3FC / 0x41D404 and are dead, and
 *     r0 is dead too; this code writes only r11 and r12 and leaves r10 alone.
 *   - LR: `zwgru_build` saved it into 0xC(r1) in its own prologue at 0x41D394
 *     and reloads it at 0x41D42C, so the `bl` clobbers a value nobody will
 *     read again.  Nothing has to be saved, and r1 is never touched.
 *   - CR0: `cmpwi r3,0x7f` at 0x41D410 writes it before anything reads it, so
 *     the `cmplwi` below is free.  CTR and XER are not touched at all.
 *   - the stock s8 clamp at 0x41D410-0x41D428 runs AFTER this, so an offset
 *     that would overflow the byte saturates exactly as stock does.
 *
 * Ten instructions, and the seven after the `bnelr` only run once the state
 * block carries our magic.  RAM is addressed absolutely through r11, never
 * through r2 or r13 (`tools/blobdis.py --check-sda` enforces it); the
 * `PATCH_RAM@ha` / `@l` pair is the linker's, so the block can move without
 * touching this file.  Both offsets are ff_state.h's own, and src/ff_ign.c
 * asserts them against `struct ff_state`.
 * ---------------------------------------------------------------------------
 */
    .section .text.ff_zw_hook, "ax", @progbits
    .globl ff_zw_hook
    .type  ff_zw_hook, @function
    .align 2
ff_zw_hook:
    add   3, 3, 10                              /* the displaced instruction */
    lis   11, PATCH_RAM@ha
    lwz   12, (PATCH_RAM + FF_OFF_MAGIC)@l(11)  /* ff_state.magic            */
    xoris 12, 12, 0x4646
    cmplwi 12, 0x3031                           /* "FF01"?                   */
    bnelr                                       /* no valid state -> stock   */
    lbz   12, (PATCH_RAM + FF_OFF_DZW_E)@l(11)  /* ff_state.dzw_e, s8        */
    extsb 12, 12
    add   3, 3, 12
    blr                                         /* back to 0x41D410          */
    .size ff_zw_hook, . - ff_zw_hook
