/*
 * ff_counter: the trampoline that replaces the stock `bl 0x11F02C` at 0x12067C.
 *
 * HOOK_TAIL is the cheap variant, and it is the right one here:
 * re/findings/scheduler.md section 7 shows that between two argument-less `bl`
 * in a flat raster task none of r3-r12, CR, CTR or XER is live, and section 8
 * shows that both neighbours of 0x12067C are exactly that.  Only LR has to be
 * saved, because `bl ff_counter_tick` destroys it and the stock leaf's `blr`
 * needs it back.
 *
 * The tail branch goes to MED9_CLR_RAM_7FE889_800E18 (0x11F02C), the leaf the
 * site originally called - so the stock work still happens, in the same task,
 * in the same order, with only our ~15 instructions in front of it.
 */
#include "../../common/med9_stock.h"
#include "../../common/hooks.S"

    HOOK_TAIL ff_counter_hook, ff_counter_tick, MED9_CLR_RAM_7FE889_800E18
