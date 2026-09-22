/*
 * ff_counter: the trampolines, one per 10 ms raster.
 *
 * HOOK_TAIL is the cheap variant, and it is the right one at both sites:
 * re/findings/scheduler.md section 7 shows that between two argument-less `bl`
 * in a flat raster task none of r3-r12, CR, CTR or XER is live, and sections 8
 * and 8.1 show that both hook words are exactly that.  Only LR has to be
 * saved, because `bl <tick>` destroys it and the original target's `blr` needs
 * it back.
 *
 *   ff_counter_hook_a  replaces `bl 0x0BD9E4` at 0x432940, in task set A's
 *                      10 ms raster 0x4328E4 (id 19).  MED9_NOP_LEAF_BD9E4 is
 *                      an EMPTY function - one `blr` - whose only caller in
 *                      the whole image is that site (scheduler.md 8.1), so the
 *                      tail branch preserves literally nothing; it is there so
 *                      that exactly one flash word changes.  ON-CHIP FLASH.
 *
 *   ff_counter_hook_b  replaces `bl 0x11F02C` at 0x12067C, in task set B's
 *                      10 ms raster 0x1205A0 (id 32).  MED9_CLR_RAM_7FE889_
 *                      800E18 clears two RAM cells, so here the tail branch
 *                      really does carry stock work - in the same task, in the
 *                      same order, with only our ~18 instructions in front of
 *                      it.  External flash.
 *
 * Dead-register argument, both sites (VERIFIED-STATIC, scheduler.md 8 / 8.1,
 * the same one patches/ff_fuel's src/hooks.S states for 0x432940): the task
 * bodies are flat lists of argument-less `bl`s; nothing sets a register before
 * either word and nothing consumes a return value after it, and the entry
 * blocks of both neighbours' targets write r3-r12 before they read them.  r1
 * is restored by the trampoline's `addi` before the `ba`, and LR is reloaded
 * from the frame, so the original target returns into the task as before.
 *
 * With `make HOOKS=external` only the set-B trampoline is assembled and it
 * keeps the pre-F1 name `ff_counter_hook`, so that build reproduces the old
 * blob byte for byte (README.md, "Two build variants").
 */
#include "../../common/med9_stock.h"
#include "../../common/hooks.S"

#ifdef FF_HOOKS_BOTH
    HOOK_TAIL ff_counter_hook_a, ff_counter_tick_a, MED9_NOP_LEAF_BD9E4
    HOOK_TAIL ff_counter_hook_b, ff_counter_tick_b, MED9_CLR_RAM_7FE889_800E18
#else
    HOOK_TAIL ff_counter_hook,   ff_counter_tick,   MED9_CLR_RAM_7FE889_800E18
#endif
