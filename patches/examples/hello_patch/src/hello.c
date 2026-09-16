/*
 * hello_patch - the smallest useful MED9.1.1 patch, used to prove the cross
 * toolchain (issue #5) and, since brief C1, the patch framework in
 * patches/common/ (issue #25).  It is NOT meant to be flashed.
 *
 * What it demonstrates, in the order docs/04_re_guidelines.md section 7 asks
 * for:
 *   - freestanding big-endian PowerPC code, no libc, no floating point;
 *   - no small-data sections, so r2 and r13 (the ECU's SDA bases,
 *     docs/02_memory_map.md section 4) are never read or written;
 *   - .rodata addressed absolutely (lis/ori), so the blob can be linked
 *     anywhere in free flash;
 *   - all state in one explicitly placed RAM block, declared the way every
 *     patch declares it: `.bss.patch_state`, which patches/common/patch.ld
 *     puts first in .bss at PATCH_RAM.
 *
 * The RAM address is an EXAMPLE and patch.json says so ("ram_status":
 * "example").  A real patch must first prove the block is unused (static
 * references plus runtime RAM dumps across ignition cycles); the highest
 * static reference into the external SRAM recorded so far is 0x805784
 * (docs/02_memory_map.md section 3).
 */
#include "../../../common/types.h"

/* PATCH_RAM + 0x00, the whole state this example owns. */
struct hello_state {
    volatile u16 counter;
};

struct hello_state hello_state __attribute__((section(".bss.patch_state")));

void hello_init(void);
u16 hello_tick(u16 increment);
u16 hello_lookup(u8 index);

/* Default behaviour equals stock: the counter starts at zero. */
void hello_init(void)
{
    hello_state.counter = 0u;
}

u16 hello_tick(u16 increment)
{
    u16 value = (u16)(hello_state.counter + increment);
    hello_state.counter = value;
    return value;
}

/*
 * A constant table in flash, read with a clamped index.  This is the shape
 * every calibratable value of ours will take (docs/04_re_guidelines.md
 * section 7): a table in a checksummed block, never a magic number inline.
 */
static const u16 hello_table[8] = {
    0u, 100u, 200u, 400u, 800u, 1600u, 3200u, 6400u
};

u16 hello_lookup(u8 index)
{
    const u8 last = (u8)(sizeof hello_table / sizeof hello_table[0] - 1u);

    if (index > last) {
        index = last;
    }
    return hello_table[index];
}
