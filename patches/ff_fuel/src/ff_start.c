/*
 * ff_start - the start enrichment `f_st(E, tmst)` and the start-ignition
 * offset (brief E2, issue #35; the #37 rules for both halves).
 *
 * Same two-part shape as ff_ign.c, and for the same reason: the values are
 * produced once per raster in C and consumed by hand-written stubs at the
 * exact instruction that publishes the stock cell.
 *
 *   producer, 10 ms      ff_start_update(), called from ff_finish() at the
 *                        end of EVERY periodic activation.  It reads `tmst`,
 *                        `e_filt` and FFCAL001 and writes the two core fields
 *                        `ff_state.fst_q10` and `ff_state.zwst_add`.
 *   consumers, 3 stubs   src/hooks.S:
 *                          ff_st_hook_a   0x41A680, `sth r31,0x303C(r13)`
 *                          ff_st_hook_b   0x41A808, `sth r3,0x303C(r13)`
 *                          ff_zwst_hook   0x431384, `stb r31,0x20A6(r13)`
 *                        No C runs on any of those paths.
 *
 * Where the two levers sit (re/findings/start.md sections 3.3, 5 and 9):
 *
 *   %ESSTT  ->  [0x41A680 / 0x41A808: x f_st]  ->  ksta_adapted 0x80302C
 *       ->  gk_rk 0x41AA88  ->  rk  ->  rk2ti, the injection window `dwi`
 *
 *   zwstt_build -> [0x431384: + zwst_add] -> zwstt 0x802096
 *       ->  zwbas_per_bank 0x41D22C REPLACES the whole per-bank angle with it
 *       ->  zwmin (which %ZWMIN takes from the same cell) -> zwout
 *
 * The asymmetry of issue #37, one more time, and it is sharper here than
 * anywhere else in the patch:
 *
 *   - `fst_q10` follows `e_filt`'s own rules.  In FAULT the estimate is held
 *     for `ff_hold_s` and then decays to `e_key`, and `f_st` simply reads
 *     whatever `e_filt` is, exactly as `ff_f_of()` does for F.  A stale-rich
 *     cranking mixture is safe.
 *   - `zwst_add` is 0 on the very activation the mode leaves OK/HOLD/OVERRIDE.
 *     No hold, no ramp.  And it matters more here than for the running blend
 *     of E1, because during the start the KNOCK RETARD IS BYPASSED
 *     (start.md section 5): a start advance that outlives its ethanol estimate
 *     has nothing downstream to take it back.
 *
 * Safety properties this file is written to keep
 *   - it writes exactly two RAM cells, both in our own state block;
 *   - it reads one stock RAM cell (`tmst`) and writes none;
 *   - `ff_st_enable` and `ff_zwst_enable` default to 0, `ff_fst_map` ships all
 *     1024 and `ff_fzwst_curve` all 0, so the shipped file cannot move either
 *     the cranking quantity or the start angle even with both enables set;
 *   - row 0 of `ff_fst_map` is the E0 row and ffcal001.py refuses to build a
 *     block whose row 0 is not exactly 1024, which is what makes E0
 *     bit-identical the way `ff_F_curve[0] == 1024` does for F;
 *   - every loop is fixed-length, there is no division by a calibration value
 *     that can be zero, no floating point and no 64-bit arithmetic;
 *   - each map corner is clamped to FF_FST_HARD_MAX **before** it is
 *     interpolated, which is also what bounds the intermediate product to
 *     2560 * 65535 = 0x0A00_0000 and keeps the whole computation inside s32.
 *
 * Deviation from the brief, with its reason (docs/agent_briefs/E2 asks for
 * one): there is **no `ff_zwst_gain`**.  The brief's formula spells
 * `fzwst(e_filt) * ff_zwst_gain`, but its own FFCAL001 v3 append list does not
 * budget an offset for a gain, and a gain multiplying a free-form six-point
 * curve adds no expressive power - it is a second place to get a start
 * advance wrong, in the one feature where nothing downstream corrects a
 * mistake.  `ff_fzwst_curve` is therefore directly in s8 counts of
 * 0.75 degCA and `ff_zwst_max` is the only ceiling above it.
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

void ff_start_update(void);
void ff_diag_fst_pct(void);
void ff_diag_zwst(void);
void ff_diag_tmst(void);
void ff_diag_ksta(void);

/*
 * src/hooks.S addresses these two fields by number.  It includes ff_state.h,
 * so it uses the same names -- but only C can check them against the struct.
 */
_Static_assert(__builtin_offsetof(struct ff_state, fst_q10) == FF_OFF_FST_Q10,
               "hooks.S loads fst_q10 from FF_OFF_FST_Q10");
_Static_assert(__builtin_offsetof(struct ff_state, zwst_add) == FF_OFF_ZWST_ADD,
               "hooks.S loads zwst_add from FF_OFF_ZWST_ADD");
_Static_assert(__builtin_offsetof(struct ff_state, fst_q10) == FF_CORE2_OFF,
               "the E2 fields must start the second checksummed range");
_Static_assert(sizeof(struct ff_state) == FF_LENGTH,
               "FF_LENGTH must describe the whole block");

/* ------------------------------------------------- the 6-point axes ------ */
/*
 * `ff_axis6()` is `ff_ign.c`'s `ff_axis8()` over a u8 breakpoint list: the
 * stock `axis_search_*` behaviour (index and a 1/65536 fraction) written as a
 * fixed-trip-count loop, because patch code may not contain a loop whose trip
 * count depends on data.
 *
 * `mul` is what puts the axis and the value into the same units.  The tmst
 * axis is in `tmst` counts and is searched with `mul` = 1; the E axis is in
 * whole percent while `e_filt` is in 1/16 %, so it is searched with `mul` = 16
 * and the comparison stays exact - no rounding of the input, and a breakpoint
 * at 85 % is exactly `e_filt` 1360.
 *
 * Below the first breakpoint and on or above the last one the fraction is 0,
 * which is what keeps ff_interp6() from reading past the end of the map.  A
 * non-monotonic (i.e. corrupt) axis matches nothing and falls through to cell
 * 0 with no fraction - the safe answer, not an out-of-range one.
 */
static u32 ff_axis6(u32 base, u32 v, u32 mul)
{
    u32 i, lo, hi;

    if (v <= (u32)MED9_U8(base) * mul)
        return 0u;
    if (v >= (u32)MED9_U8(base + (FF_FST_N - 1u)) * mul)
        return (FF_FST_N - 1u) << 16;
    for (i = 0u; i < FF_FST_N - 1u; i++) {
        lo = (u32)MED9_U8(base + i) * mul;
        hi = (u32)MED9_U8(base + i + 1u) * mul;
        if (v >= lo && v < hi && hi > lo)
            return (i << 16) | (((v - lo) << 16) / (hi - lo));
    }
    return 0u;
}

/* One clamped corner of ff_fst_map, so nothing downstream can overflow. */
static s32 ff_fst_cell(u32 p)
{
    u32 v = MED9_U16(FF_CAL_BASE + FF_CAL_O_FST_MAP + 2u * p);

    if (v > FF_FST_HARD_MAX)
        v = FF_FST_HARD_MAX;
    return (s32)v;
}

/*
 * Bilinear over the 6-wide u16 map, `val[iy * 6 + ix]` with y = E and
 * x = tmst - the same index order and the same arithmetic as the stock
 * `interp_2d_s8` (0x40C3B4) that ff_ign.c's `ff_interp8()` reproduces, only
 * over u16 cells.  Every intermediate is an s32 and every corner is already
 * clamped to 2560, so the widest product is 2560 * 65535 and no step can wrap.
 */
static u32 ff_interp6(u32 key_y, u32 key_x)
{
    u32 fx = key_x & 0xFFFFu, ix = key_x >> 16;
    u32 fy = key_y & 0xFFFFu, iy = key_y >> 16;
    u32 p = ix + FF_FST_N * iy;
    s32 v = ff_fst_cell(p);
    s32 v2;

    if (fx)
        v = v + ((((s32)fx) * (ff_fst_cell(p + 1u) - v)) >> 16);
    if (fy) {
        v2 = ff_fst_cell(p + FF_FST_N);
        if (fx)
            v2 = v2 + ((((s32)fx)
                        * (ff_fst_cell(p + FF_FST_N + 1u) - v2)) >> 16);
        v = v + ((((s32)fy) * (v2 - v)) >> 16);
    }
    if (v < (s32)FF_FST_ONE)
        v = (s32)FF_FST_ONE;
    return (u32)v;
}

/*
 * The start-advance curve: six s8 counts on the SAME E axis as the map's rows,
 * linearly interpolated.  s8 cells, so the product is at most 255 * 65535 and
 * the s32 is never near its limit.
 */
static s32 ff_fzwst_of(u32 key_e)
{
    u32 f = key_e & 0xFFFFu, i = key_e >> 16;
    s32 a = (s32)MED9_S8(FF_CAL_BASE + FF_CAL_O_FZWST_CURVE + i);
    s32 b;

    if (f == 0u)
        return a;
    b = (s32)MED9_S8(FF_CAL_BASE + FF_CAL_O_FZWST_CURVE + i + 1u);
    return a + ((((s32)f) * (b - a)) >> 16);
}

/* ------------------------------------------------- one activation -------- */
void ff_start_update(void)
{
    u32 key_e, key_t, ceiling, f;
    s32 adv, zceil;
    u8 mode, tmst;

    mode = ff_state.mode;
    tmst = MED9_U8(MED9_TMST);

    /*
     * The FUEL half.  It runs in OK, HOLD, FAULT and OVERRIDE, i.e. exactly
     * where ff_tick() computes `f_q10` from `e_filt`, and is neutral in INIT
     * and OFF, i.e. exactly where ff_tick() forces `f_q10` to 1024.  There is
     * no separate hold or decay here: `e_filt` already carries both.
     */
    if (MED9_U8(FF_CAL_BASE + FF_CAL_O_ST_ENABLE) == 0u
        || ff_state.cal_ok == 0u
        || ff_state.e_filt == 0u
        || mode == (u8)FF_MODE_INIT || mode == (u8)FF_MODE_OFF) {
        ff_state.fst_q10 = (u16)FF_FST_ONE;
    } else {
        key_e = ff_axis6(FF_CAL_BASE + FF_CAL_O_FST_E_AXIS,
                         (u32)ff_state.e_filt, 16u);
        key_t = ff_axis6(FF_CAL_BASE + FF_CAL_O_FST_T_AXIS, (u32)tmst, 1u);
        f = ff_interp6(key_e, key_t);

        ceiling = MED9_U16(FF_CAL_BASE + FF_CAL_O_FST_MAX);
        if (ceiling > FF_FST_HARD_MAX)
            ceiling = FF_FST_HARD_MAX;
        if (ceiling < FF_FST_ONE)
            ceiling = FF_FST_ONE;
        if (f > ceiling)
            f = ceiling;
        ff_state.fst_q10 = (u16)f;
    }

    /*
     * The IGNITION half: the #37 rule, straight to zero on this activation.
     * `ff_zwst_tmax` is a tmst COUNT, and `>=` means a block whose tmax is 0
     * disables the advance everywhere rather than enabling it everywhere.
     */
    if (MED9_U8(FF_CAL_BASE + FF_CAL_O_ZWST_ENABLE) == 0u
        || ff_state.cal_ok == 0u
        || ff_state.e_filt == 0u
        || (mode != (u8)FF_MODE_OK && mode != (u8)FF_MODE_HOLD
            && mode != (u8)FF_MODE_OVERRIDE)
        || tmst >= MED9_U8(FF_CAL_BASE + FF_CAL_O_ZWST_TMAX)) {
        ff_state.zwst_add = 0;
        return;
    }

    key_e = ff_axis6(FF_CAL_BASE + FF_CAL_O_FST_E_AXIS,
                     (u32)ff_state.e_filt, 16u);
    adv = ff_fzwst_of(key_e);

    zceil = (s32)(u32)MED9_U8(FF_CAL_BASE + FF_CAL_O_ZWST_MAX);
    if (zceil > FF_ZWST_HARD_MAX)
        zceil = FF_ZWST_HARD_MAX;
    if (adv < 0)                        /* the start angle is never retarded */
        adv = 0;
    else if (adv > zceil)
        adv = zceil;
    ff_state.zwst_add = (s8)adv;
}

/* ------------------------------------- the measuring block, group 69 ----- */
/*
 * Same shape as src/ff_diag.c and src/ff_ign.c: a non-leaf C function entered
 * with `blrl` from `measuring_var_dispatch`, which hands a (formula, A, B)
 * triple to `measuring_result_emit` and returns.  No trampoline.
 *
 * Fields 1 and 2 report OUR state and check the block header first.  Fields 3
 * and 4 report STOCK cells, so they do not: a tester watching a cold start
 * must still see `tmst` and the cranking factor when our block is invalid,
 * and reading three bytes of stock RAM cannot depend on it.
 */
static void ff_emit(u32 formula, u32 a, u32 b)
{
    MED9_FN(void (*)(u32, u32, u32), MED9_MEASURING_RESULT_EMIT)(formula, a, b);
}

static u8 ff_st_ready(void)
{
    return (ff_state.magic == FF_MAGIC
            && ff_state.length == (u16)FF_LENGTH) ? 1u : 0u;
}

/* id 2188, group 69 field 1: our own start factor f_st, 100..250 %. */
void ff_diag_fst_pct(void)
{
    u32 pct;

    if (!ff_st_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    pct = ((u32)ff_state.fst_q10 * 100u + (FF_FST_ONE / 2u)) / FF_FST_ONE;
    if (pct > 0xFFu)                    /* unreachable: FF_FST_HARD_MAX = 250 % */
        pct = 0xFFu;
    ff_emit(FF_FMT_PCT, FF_FMT_A_PCT, pct);
}

/* id 2189, group 69 field 2: the applied start advance, degCA. */
void ff_diag_zwst(void)
{
    s32 b;

    if (!ff_st_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    b = (s32)ff_state.zwst_add + (s32)FF_ZW_BIAS;
    if (b < 0)
        b = 0;
    else if (b > 0xFF)
        b = 0xFF;
    ff_emit(FF_FMT_ZW, FF_FMT_A_ZW, (u32)b);
}

/*
 * id 2190, group 69 field 3: `tmst` 0x8021F6 in whole degrees C.  A stock
 * cell, so no header check.  The count is 0.75 degC per LSB with a -48 degC
 * offset (start.md section 2); the conversion rounds to nearest, and formula
 * 0x05 with A = 10 reports `B - 100` degrees.  The u8 count spans
 * -48..+143 degC, so B spans 52..243 and neither clamp below can fire - they
 * are there because a diagnostic handler must not be able to emit a byte it
 * did not intend.
 */
void ff_diag_tmst(void)
{
    s32 c = (s32)(u32)MED9_U8(MED9_TMST);
    s32 b = ((c * 3 + 2) / 4) - FF_TMST_BIAS + (s32)FF_DEGC_BIAS;

    if (b < 0)
        b = 0;
    else if (b > 0xFF)
        b = 0xFF;
    ff_emit(FF_FMT_DEGC, FF_FMT_A_DEGC, (u32)b);
}

/*
 * id 2191, group 69 field 4: `ksta_adapted` 0x80302C, the cell the two S1
 * stubs publish - stock value times `f_st` - as a RAW u16 count with
 * 1024 = 1.0.
 *
 * Why a count and not a percent.  The brief asks for a percent and says to
 * fall back to a count formula if it does not fit, and it does not: the stock
 * factor alone is 22.8x at -30 degC (start.md section 6), i.e. 2280 %, and
 * formula 0x21's `B` is one byte.  Formula 0x36 emits `(A << 8) | B`, so the
 * whole 16-bit cell is reported exactly as the ECU holds it - no saturation
 * anywhere in the range, and the number the tester reads is the same number
 * emu/start_model.py and the tick-by-tick comparison work in.  Divide by 1024
 * for the factor; `patches/ff_fuel/test/procedure_e2.md` says so on the line
 * that reads it.
 */
void ff_diag_ksta(void)
{
    u32 v = MED9_U16(MED9_KSTA_ADAPTED);

    ff_emit(FF_FMT_COUNT, (v >> 8) & 0xFFu, v & 0xFFu);
}
