/*
 * ff_ign - the ignition blend `zw += f_zw(E) * dzw_E(nmot, rl)` (brief E1,
 * issue #34, and the ignition half of the #37 rule).
 *
 * Two halves, in the shape the stock software already uses for exactly this
 * job (re/findings/ignition.md section 13.2: the low-octane detector computes
 * an s8 delta in a slow task and `zwbas_per_bank` consumes it
 * segment-synchronously):
 *
 *   producer, 10 ms      ff_zw_update(), called from ff_finish() at the end
 *                        of EVERY periodic activation.  It reads nmot_w, rl_w
 *                        and FFCAL001, and writes the two core fields
 *                        `ff_state.fzw_q8` and `ff_state.dzw_e`.
 *   consumer, per event  ff_zw_hook in src/hooks.S, ten instructions in place
 *                        of the `add r3,r3,r10` at 0x41D40C inside
 *                        `zwgru_build`.  It re-does the add, checks the state
 *                        block's magic, loads `dzw_e` and adds it.  No C is
 *                        called on that path at all.
 *
 * Where the offset lands, and why there (ignition.md section 11):
 *
 *      KFZW + the base deltas  ->  [ 0x41D40C: + dzw_e ]  ->  s8 clamp
 *          ->  bank offsets and the KNOCK RETARD (0x41D10C)
 *          ->  zwmin / zwout and the -54..+58.5 degCA clamp (0x41D464)
 *
 * so knock control, the minimum-angle maps and the output clamp all still act
 * on top of the offset.  `KFZWOP` (the torque model's optimum) is NOT shifted,
 * as docs/05 section 3.4 requires; `KFZWOP - KFZW` is instead the physical
 * budget the calibration has to stay inside (1.5 degCA at 1800 rpm / 35 %,
 * ~9 degCA at 4500 rpm / 90 %).
 *
 * The #37 rule, and the one real difference from ff_fuel.c: on the activation
 * the mode leaves OK/HOLD/OVERRIDE, `dzw_e` is **0**.  No hold, no ramp.  The
 * fuel factor may be held for 60 s because a stale-rich mixture is safe; a
 * stale advance is not.
 *
 * Safety properties this file is written to keep
 *   - it writes exactly two RAM cells, both in our own state block;
 *   - it reads four stock RAM cells (nmot_w, rl_w, dwkrz, the low-octane
 *     latch) and writes none of them;
 *   - `ff_zw_enable` defaults to 0 and `ff_dzw_map` ships all zero, so the
 *     shipped file cannot move the ignition angle either way;
 *   - every loop is fixed-length, there is no division by a calibration value
 *     that can be zero, no floating point and no 64-bit arithmetic;
 *   - the offset is clamped to +-ff_dzw_max and then to +-FF_DZW_HARD_MAX in
 *     code, so a corrupt FFCAL001 cannot ask for more than 12 degCA.
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

void ff_zw_update(void);
void ff_diag_fzw_pct(void);
void ff_diag_dzw(void);
void ff_diag_dwkrz(void);
void ff_diag_zwlatch(void);

/*
 * src/hooks.S addresses these two fields by number.  It includes ff_state.h,
 * so it uses the same names -- but only C can check them against the struct.
 */
_Static_assert(__builtin_offsetof(struct ff_state, magic) == FF_OFF_MAGIC,
               "hooks.S loads the magic from FF_OFF_MAGIC");
_Static_assert(__builtin_offsetof(struct ff_state, dzw_e) == FF_OFF_DZW_E,
               "hooks.S loads dzw_e from FF_OFF_DZW_E");
_Static_assert(__builtin_offsetof(struct ff_state, fzw_q8) == 0x2A,
               "fzw_q8 must stay inside the checksummed core");

/* ----------------------------------------------------------- f_zw(E) ----- */
/*
 * The blend factor, 1/256, interpolated on the same 17-point E grid as
 * `ff_f_of()` in ff_fuel.c: one breakpoint every 6.25 % = 100 counts of
 * 1/16 %.  `ff_fzw_curve[0]` is 0 in every block ffcal001.py will build, so
 * E0 adds nothing whatever `ff_dzw_map` contains.
 */
static u16 ff_fzw_of(u16 e)
{
    u32 i, fr;
    s32 a, b, f;

    if (e >= (u16)FF_E_FILT_MAX) {
        f = (s32)MED9_U8(FF_CAL_BASE + FF_CAL_O_FZW_CURVE + (FF_CURVE_N - 1u));
    } else {
        i = (u32)e / FF_CURVE_STEP;
        fr = (u32)e % FF_CURVE_STEP;
        a = (s32)MED9_U8(FF_CAL_BASE + FF_CAL_O_FZW_CURVE + i);
        b = (s32)MED9_U8(FF_CAL_BASE + FF_CAL_O_FZW_CURVE + i + 1u);
        f = a + ((b - a) * (s32)fr) / (s32)FF_CURVE_STEP;
    }
    if (f < 0)
        f = 0;
    else if (f > (s32)FF_FZW_MAX)
        f = (s32)FF_FZW_MAX;
    return (u16)f;
}

/* ------------------------------------------- the 8-point axis search ----- */
/*
 * `axis_search_u16_hint` (0x40C9CC) with the hint fixed at 0, rewritten as a
 * fixed-trip-count loop: patch code may not contain a loop whose trip count
 * depends on data, and the stock function's `while (axis[idx+1] <= value)`
 * does.  The result is the same key, `(index << 16) | frac`, which is what
 * `interp_2d_s8` expects; `emu/zw_model.py` models the stock function and
 * `tests/test_zw_model.py` proves that model against the real code.
 *
 * Below the first breakpoint and on or above the last one the fraction is 0,
 * which is what keeps ff_interp8() from reading past the end of the map.  A
 * non-monotonic (i.e. corrupt) axis matches nothing and falls through to cell
 * 0 with no fraction -- the safe answer, not an out-of-range one.
 */
static u32 ff_axis8(u32 base, u16 v)
{
    u32 i;
    u16 lo, hi;

    if (v <= MED9_U16(base))
        return 0u;
    if (v >= MED9_U16(base + 2u * (FF_DZW_N - 1u)))
        return (FF_DZW_N - 1u) << 16;
    for (i = 0u; i < FF_DZW_N - 1u; i++) {
        lo = MED9_U16(base + 2u * i);
        hi = MED9_U16(base + 2u * i + 2u);
        if (v >= lo && v < hi && hi > lo)
            return (i << 16) | (((u32)(v - lo) << 16) / (u32)(hi - lo));
    }
    return 0u;
}

/*
 * `interp_2d_s8` (0x40C3B4) over an 8-wide s8 map: `val[iy * 8 + ix]`,
 * bilinear, every intermediate an s32.  The `>> 16` of a negative difference
 * is an arithmetic shift (`srawi`), which is what the stock function does and
 * what emu/models/flexfuel.py's `interp8_s8` reproduces.
 */
static s32 ff_interp8(u32 base, u32 key_y, u32 key_x)
{
    u32 fx = key_x & 0xFFFFu, ix = key_x >> 16;
    u32 fy = key_y & 0xFFFFu, iy = key_y >> 16;
    u32 p = ix + FF_DZW_N * iy;
    s32 v = (s32)MED9_S8(base + p);
    s32 v2;

    if (fx)
        v = v + ((((s32)fx) * ((s32)MED9_S8(base + p + 1u) - v)) >> 16);
    if (fy) {
        v2 = (s32)MED9_S8(base + p + FF_DZW_N);
        if (fx)
            v2 = v2 + ((((s32)fx)
                        * ((s32)MED9_S8(base + p + FF_DZW_N + 1u) - v2)) >> 16);
        v = v + ((((s32)fy) * (v2 - v)) >> 16);
    }
    return (s32)(s8)v;
}

/* ------------------------------------------------- one activation -------- */
void ff_zw_update(void)
{
    u32 key_y, key_x, ceiling;
    s32 d, p, q;
    u8 mode;

    mode = ff_state.mode;
    if (MED9_U8(FF_CAL_BASE + FF_CAL_O_ZW_ENABLE) == 0u
        || ff_state.cal_ok == 0u
        || ff_state.e_filt == 0u
        || (mode != (u8)FF_MODE_OK && mode != (u8)FF_MODE_HOLD
            && mode != (u8)FF_MODE_OVERRIDE)) {
        /* The #37 ignition rule: straight to zero, on this activation. */
        ff_state.fzw_q8 = 0u;
        ff_state.dzw_e = 0;
        return;
    }

    ff_state.fzw_q8 = ff_fzw_of(ff_state.e_filt);

    key_y = ff_axis8(FF_CAL_BASE + FF_CAL_O_DZW_NMOT, MED9_U16(MED9_NMOT_W));
    key_x = ff_axis8(FF_CAL_BASE + FF_CAL_O_DZW_RL, MED9_U16(MED9_RL_W));
    d = ff_interp8(FF_CAL_BASE + FF_CAL_O_DZW_MAP, key_y, key_x);

    /* Round half away from zero, so a symmetric map gives a symmetric result. */
    p = (s32)(u32)ff_state.fzw_q8 * d;
    q = (p >= 0) ? (p + (s32)(FF_FZW_ONE / 2u)) / (s32)FF_FZW_ONE
                 : -((-p + (s32)(FF_FZW_ONE / 2u)) / (s32)FF_FZW_ONE);

    ceiling = MED9_U8(FF_CAL_BASE + FF_CAL_O_DZW_MAX);
    if (ceiling > (u32)FF_DZW_HARD_MAX)
        ceiling = (u32)FF_DZW_HARD_MAX;
    if (q > (s32)ceiling)
        q = (s32)ceiling;
    else if (q < -(s32)ceiling)
        q = -(s32)ceiling;
    ff_state.dzw_e = (s8)q;
}

/* ------------------------------------ the measuring block, group 108 ----- */
/*
 * Same shape as src/ff_diag.c: a non-leaf C function entered with `blrl` from
 * `measuring_var_dispatch`, which hands a (formula, A, B) triple to
 * `measuring_result_emit` and returns.  No trampoline.
 *
 * Fields 1 and 2 report OUR state and check the block header first, exactly as
 * D2's handlers do.  Fields 3 and 4 report STOCK cells, so they do not: a
 * tester chasing knock must still see the retard and the low-octane latch when
 * our block is invalid, and reading six bytes of stock RAM cannot depend on it.
 */
static void ff_emit(u32 formula, u32 a, u32 b)
{
    MED9_FN(void (*)(u32, u32, u32), MED9_MEASURING_RESULT_EMIT)(formula, a, b);
}

static u8 ff_zw_ready(void)
{
    return (ff_state.magic == FF_MAGIC
            && ff_state.length == (u16)FF_LENGTH) ? 1u : 0u;
}

/* An s8 count -> the B byte of formula 0x22, saturating at both ends. */
static u32 ff_zw_byte(s32 counts)
{
    s32 b = counts + (s32)FF_ZW_BIAS;

    if (b < 0)
        b = 0;
    else if (b > 0xFF)
        b = 0xFF;
    return (u32)b;
}

/* id 2192, group 108 field 1: the blend factor f_zw, 0..100 %. */
void ff_diag_fzw_pct(void)
{
    u32 pct;

    if (!ff_zw_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    pct = ((u32)ff_state.fzw_q8 * 100u + (FF_FZW_ONE / 2u)) / FF_FZW_ONE;
    if (pct > 100u)
        pct = 100u;
    ff_emit(FF_FMT_PCT, FF_FMT_A_PCT, pct);
}

/* id 2193, group 108 field 2: the applied offset dzw_e, degCA. */
void ff_diag_dzw(void)
{
    if (!ff_zw_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_ZW, FF_FMT_A_ZW, ff_zw_byte((s32)ff_state.dzw_e));
}

/*
 * id 2194, group 108 field 3: the WORST of the six per-cylinder knock retards
 * (0x7FCE57, s8, <= 0).  The array is stored in firing order and the
 * index -> cylinder map is only COMMUNITY (ignition.md 5.1) -- max() does not
 * care, which is exactly why it is the right aggregation here.  Zero is the
 * acceptance criterion of docs/05 section 3.4.
 */
void ff_diag_dwkrz(void)
{
    s32 worst = (s32)MED9_S8(MED9_DWKRZ);
    u32 i;
    s32 v;

    for (i = 1u; i < 6u; i++) {
        v = (s32)MED9_S8(MED9_DWKRZ + i);
        if (v > worst)
            worst = v;
    }
    ff_emit(FF_FMT_ZW, FF_FMT_A_ZW, ff_zw_byte(worst));
}

/*
 * id 2195, group 108 field 4: the stock low-octane-fuel latch, 0x7FD31B & 3.
 * `zwgru_low_octane_detect` (0x0F436C) sets bit 0 when the mean knock retard
 * has been worse than KFSWKFZK for TSWZK samples and bit 1 when it has
 * recovered past KFSWKFZKR (ignition.md 13.2).  With E85 neither may ever
 * set, and a reading of 0 is the second acceptance signal beside field 3.
 */
void ff_diag_zwlatch(void)
{
    ff_emit(FF_FMT_COUNT, 0u,
            (u32)MED9_U8(MED9_ZW_LOW_OCT_LATCH) & FF_ZW_LATCH_MASK);
}
