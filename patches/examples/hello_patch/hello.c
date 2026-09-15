/*
 * hello_patch - the smallest useful MED9.1.1 patch, used to prove the
 * cross toolchain (issue #5).  It is NOT meant to be flashed.
 *
 * What it demonstrates, in the order docs/04_re_guidelines.md section 7 asks
 * for:
 *   - freestanding big-endian PowerPC code, no libc, no floating point;
 *   - no small-data sections, so r2 and r13 (the ECU's SDA bases,
 *     docs/02_memory_map.md section 4) are never read or written;
 *   - .rodata addressed absolutely (lis/ori), so the blob can be linked
 *     anywhere in free flash;
 *   - all state in one explicitly placed RAM word.
 *
 * The RAM address below is an EXAMPLE.  A real patch must first prove the
 * word is unused (static references plus runtime RAM dumps across ignition
 * cycles); docs/02_memory_map.md records the highest static reference into
 * the external SRAM as 0x805784.
 */

#include <stdint.h>

#define HELLO_COUNTER (*(volatile uint16_t *)0x00806000u)

/* Default behaviour equals stock: the counter starts at zero. */
void hello_init(void)
{
    HELLO_COUNTER = 0u;
}

uint16_t hello_tick(uint16_t increment)
{
    uint16_t value = (uint16_t)(HELLO_COUNTER + increment);
    HELLO_COUNTER = value;
    return value;
}

/*
 * A constant table in flash, read with a clamped index.  This is the shape
 * every calibratable value of ours will take (docs/04_re_guidelines.md
 * section 7): a table in a checksummed block, never a magic number inline.
 */
static const uint16_t hello_table[8] = {
    0u, 100u, 200u, 400u, 800u, 1600u, 3200u, 6400u
};

uint16_t hello_lookup(uint8_t index)
{
    const uint8_t last = (uint8_t)(sizeof hello_table / sizeof hello_table[0] - 1u);

    if (index > last) {
        index = last;
    }
    return hello_table[index];
}
