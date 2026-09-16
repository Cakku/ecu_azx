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

    HOOK_TAIL ff_fuel_rk_hook, ff_rk_scale,    MED9_RKSPLIT
    HOOK_TAIL ff_fuel_hook_a,  ff_fuel_tick_a, MED9_NOP_LEAF_BD9E4
    HOOK_TAIL ff_fuel_hook_b,  ff_fuel_tick_b, MED9_CLR_RAM_7FE889_800E18
