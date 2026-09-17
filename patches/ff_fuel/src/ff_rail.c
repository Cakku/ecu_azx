/*
 * ff_rail - the rail-pressure setpoint adder and the injection-window
 * diagnostics (brief E5, issue #36; the rail half of the #37 rule).
 *
 * Same two-part shape as ff_ign.c and ff_start.c, and for the same reason: the
 * value is produced once per raster in C and consumed by a hand-written stub
 * at the exact instruction that publishes the stock cell.
 *
 *   producer, 10 ms      ff_rail_update(), called from ff_finish() at the end
 *                        of EVERY periodic activation.  It reads FFCAL001 and
 *                        four stock RAM cells, and writes the five core-2
 *                        fields `prail_add`, `win_margin_min`, `prist_min`,
 *                        `msv_sat_ticks` and `diag_ticks`.
 *   consumer, 20 ms      ff_prail_hook in src/hooks.S, twelve instructions in
 *                        place of the `sth r30,0x3200(r13)` at 0x45845C inside
 *                        `hdrpsol_main`.  No C runs on that path.
 *
 * Where the adder lands (re/findings/rail.md sections 3.2-3.5, 12.1):
 *
 *   KFPRSOL* map bank (+ KFPRSOLOFF x fade)
 *       -> [ 0x45845C: + prail_add, saturating at 0xFFFF ] -> prsoll_raw 0x8031F0
 *       -> PRSOLMN floor 7000 / KLPRMAX ceiling 22000       -> 0x8031F2
 *       -> the pump-volume RATE limiter                     -> 0x8031EE
 *       -> prsoll 0x8031F4 -> %HDR -> %VSTMSV -> %AMSV
 *
 * so the 35.0 bar floor, the **110.0 bar ceiling** and the rate limiter all
 * still act on top of the adder -- and they do so because the clamp re-reads
 * the cell from RAM at 0x458480 rather than re-using the register the store
 * came from.  That is the whole safety argument for this insertion point, and
 * it is why there is no calibration of this patch that can put more pressure
 * in the rail than the stock ECU already allows itself.
 *
 * What the adder is FOR.  `KFPRSOLHOM` tops out at 19000 = 95 bar against a
 * ceiling of 110 bar, so the headroom is +15 bar = +15.8 % pressure = 7.6 %
 * more flow at the same `ti`.  That is a mixture-preparation and injector-duty
 * measure, nothing more: E85 needs about 40 % more fuel MASS and that mass
 * comes from `rk` (ff_fuel.c), not from here (rail.md section 12.1).
 *
 * The #37 rule, rail flavour: `prail_add` is **0 on the very activation the
 * mode leaves OK/HOLD/OVERRIDE**.  No hold and no ramp -- the same side of the
 * line as the ignition blend and the start advance, and for the same reason:
 * a stale-rich mixture is safe, a stale pressure demand is not.  The dangerous
 * failure is not the map, it is the pump (rail.md section 14.4): a setpoint the
 * pump cannot follow lets `prist` fall, and below PRWBHMX = 2600 = 13.0 bar the
 * driver cut-off, the injection-angle clamp and the fault charge limit all arm
 * at once.
 *
 * The three diagnostics answer exactly that question, and they run
 * unconditionally -- with the adder disabled, with a corrupt FFCAL001, on a
 * stock calibration.  They cost nothing and they are the only way to see the
 * injection window at all: `dwi` and `wbho1s` have NO stock measuring id
 * (rail.md section 11).
 *
 * Safety properties this file is written to keep
 *   - it writes exactly five RAM cells, all in our own state block;
 *   - it reads four stock RAM cells and one stock calibration word, and writes
 *     none of them;
 *   - `ff_prail_enable` defaults to 0 and `ff_prail_curve` ships all zero, so
 *     the shipped file cannot move the rail pressure either way;
 *   - every loop is fixed-length, there is no division by a calibration value
 *     that can be zero, no floating point and no 64-bit arithmetic;
 *   - `prail_add` is clamped to `ff_prail_max` and then to FF_PRAIL_HARD_MAX
 *     in code, so a corrupt FFCAL001 cannot ask for more than 30.0 bar -- and
 *     even that is eaten by the stock KLPRMAX ceiling.
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

void ff_rail_update(void);
void ff_diag_prail(void);
void ff_diag_prist_min(void);
void ff_diag_win_margin(void);
void ff_diag_msv_sat(void);

/*
 * src/hooks.S addresses one field by number.  It includes ff_state.h, so it
 * uses the same name -- but only C can check it against the struct.
 */
_Static_assert(__builtin_offsetof(struct ff_state, prail_add) == FF_OFF_PRAIL_ADD,
               "hooks.S loads prail_add from FF_OFF_PRAIL_ADD");
_Static_assert(__builtin_offsetof(struct ff_state, msv_sat_ticks) == 0x43,
               "msv_sat_ticks takes the byte E2 reserved at +0x43");
_Static_assert(FF_CORE2_OFF + FF_CORE2_LEN == FF_LENGTH,
               "every E5 field must sit inside the second checksummed range");

/* ------------------------------------------------------ prail_add(E) ----- */
/*
 * The adder curve, 0.005 bar per count, interpolated on the same 17-point
 * ethanol grid as `ff_f_of()` and `ff_fzw_of()`: one breakpoint every 6.25 % =
 * FF_CURVE_STEP counts of 1/16 %.  `ff_prail_curve[0]` is 0 in every block
 * ffcal001.py will build, so E0 adds nothing whatever the rest of the curve
 * contains.
 *
 * The widest intermediate is (0xFFFF - 0) * 99, well inside s32, and the
 * result is clamped to FF_PRAIL_HARD_MAX before it leaves.
 */
static u16 ff_prail_of(u16 e)
{
    u32 i, fr;
    s32 a, b, f;

    if (e >= (u16)FF_E_FILT_MAX) {
        f = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_PRAIL_CURVE
                          + 2u * (FF_PRAIL_N - 1u));
    } else {
        i = (u32)e / FF_CURVE_STEP;
        fr = (u32)e % FF_CURVE_STEP;
        a = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_PRAIL_CURVE + 2u * i);
        b = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_PRAIL_CURVE + 2u * (i + 1u));
        f = a + ((b - a) * (s32)fr) / (s32)FF_CURVE_STEP;
    }
    if (f < 0)
        f = 0;
    else if (f > FF_PRAIL_HARD_MAX)
        f = FF_PRAIL_HARD_MAX;
    return (u16)f;
}

/* ------------------------------------------- the diagnostic window ------- */
/*
 * How many activations `ff_diag_window_ms` is, with the same physical-units
 * convention the rest of the patch uses: FFCAL001 stores milliseconds and the
 * patch converts with `ff_tick_ms`, itself a calibration value clamped exactly
 * as ff_cal_load() clamps it (1..1000 ms).  A block whose header does not
 * check out gives no usable numbers at all, so the window then falls back to
 * 100 activations -- one second at the real 10 ms raster -- which keeps the
 * diagnostics deterministic on an ECU whose FFCAL001 is corrupt or absent.
 */
static u16 ff_diag_window_ticks(void)
{
    u32 tick, ms, n;

    if (ff_state.cal_ok == 0u)
        return 100u;

    tick = (u32)MED9_U16(FF_CAL_BASE + FF_CAL_O_TICK_MS);
    if (tick < 1u)
        tick = 1u;
    else if (tick > 1000u)
        tick = 1000u;

    ms = (u32)MED9_U16(FF_CAL_BASE + FF_CAL_O_DIAG_WIN_MS);
    n = ms / tick;
    if (n < 1u)
        n = 1u;
    return (u16)n;
}

/* ------------------------------------------------- one activation -------- */
/*
 * The diagnostics are a TUMBLING window with continuous publication, not a
 * sliding one: the three fields show the worst value seen since the current
 * `ff_diag_window_ms` window started, and the window restarts from scratch
 * when it expires.  A true sliding minimum would need a ring buffer of up to
 * `ff_diag_window_ms / ff_tick_ms` samples -- 100 of them at the shipped
 * calibration, 6553 at the worst one a u16 can ask for -- and patch code does
 * not get to allocate that.  The cost of the approximation is that a tester
 * who polls once per second may catch a window a few activations after it
 * restarted; `test/procedure_e5.md` says to watch the field for several
 * seconds rather than to trust one sample, and `diag_ticks` is in the state
 * block so a logger can see exactly where in the window it is.
 */
void ff_rail_update(void)
{
    u32 ceiling, v, vmsvmx;
    s32 margin;
    u8 mode;

    mode = ff_state.mode;

    /*
     * The ADDER.  The #37 rule for anything that adds pressure or advance:
     * straight to zero on this activation, no hold and no ramp.  `e_filt` = 0
     * is E0, where the curve is 0 anyway -- testing it here makes the
     * bit-identical path a branch rather than an interpolation.
     */
    if (MED9_U8(FF_CAL_BASE + FF_CAL_O_PRAIL_ENABLE) == 0u
        || ff_state.cal_ok == 0u
        || ff_state.e_filt == 0u
        || (mode != (u8)FF_MODE_OK && mode != (u8)FF_MODE_HOLD
            && mode != (u8)FF_MODE_OVERRIDE)) {
        ff_state.prail_add = 0u;
    } else {
        v = (u32)ff_prail_of(ff_state.e_filt);

        ceiling = (u32)MED9_U16(FF_CAL_BASE + FF_CAL_O_PRAIL_MAX);
        if (ceiling > (u32)FF_PRAIL_HARD_MAX)
            ceiling = (u32)FF_PRAIL_HARD_MAX;
        if (v > ceiling)
            v = ceiling;
        ff_state.prail_add = (u16)v;
    }

    /*
     * The DIAGNOSTICS.  Unconditional: they observe stock cells and they are
     * what tells the human whether the pump follows and whether the injection
     * still fits the window.  They do not depend on `ff_prail_enable`, on
     * `cal_ok` or on the mode.
     */
    if (ff_state.diag_ticks == 0u
        || ff_state.diag_ticks > ff_diag_window_ticks()) {
        ff_state.win_margin_min = (s16)0x7FFF;
        ff_state.prist_min = 0xFFFFu;
        ff_state.msv_sat_ticks = 0u;
        ff_state.diag_ticks = ff_diag_window_ticks();
    }

    /*
     * margin = wbho1s - dwi - required_margin, all in angle LSB of 3/128 degCA
     * (rail.md sections 8, 11 and 14).  The required margin is read from the
     * RAM cell `awea_angles` writes it into, not from the literal 2144, so it
     * follows both a re-calibrated KLWBHO1SMX and the runtime value.
     */
    margin = (s32)MED9_S16(MED9_WBHO1S_W)
             - (s32)(u32)MED9_U16(MED9_DWI)
             - (s32)((u32)MED9_U8(MED9_WIN_MARGIN_W) * FF_ANGLE_MAP_SCALE);
    if (margin < -32768)
        margin = -32768;
    else if (margin > 32767)
        margin = 32767;
    if (margin < (s32)ff_state.win_margin_min)
        ff_state.win_margin_min = (s16)margin;

    v = (u32)MED9_U16(MED9_PRIST_W);
    if (v < (u32)ff_state.prist_min)
        ff_state.prist_min = (u16)v;

    /*
     * "The pump is out of volume": 0x80316E is `min(request, VMSVMX)`, so it
     * sitting AT VMSVMX is the saturation signal (rail.md sections 5.1, 12.2).
     * VMSVMX is read out of the calibration rather than compared against a
     * literal 5000; a zero there would make the test vacuous, so it is skipped.
     */
    vmsvmx = (u32)MED9_U16(MED9_VMSVMX);
    if (vmsvmx != 0u && (u32)MED9_U16(MED9_VMSV_LIMITED) >= vmsvmx
        && ff_state.msv_sat_ticks < 0xFFu)
        ff_state.msv_sat_ticks = (u8)(ff_state.msv_sat_ticks + 1u);

    ff_state.diag_ticks = (u16)(ff_state.diag_ticks - 1u);
}

/* ------------------------------------ the measuring block, group 109 ----- */
/*
 * Same shape as src/ff_diag.c, src/ff_ign.c and src/ff_start.c: a non-leaf C
 * function entered with `blrl` from `measuring_var_dispatch`, which hands a
 * (formula, A, B) triple to `measuring_result_emit` and returns.  No
 * trampoline.
 *
 * All FOUR fields check the state-block header, because all four report OUR
 * fields -- even the two that are derived from stock cells, because the
 * minimum is ours and a stale minimum would be a lie.  A tester who sees "not
 * available" here should read the stock rail groups (106 field 1 = `prist`,
 * 231 fields 2/3 = `prsoll`/`prist`, measuring_vars.md section 7.3) instead;
 * `test/procedure_e5.md` says so.
 */
static void ff_emit(u32 formula, u32 a, u32 b)
{
    MED9_FN(void (*)(u32, u32, u32), MED9_MEASURING_RESULT_EMIT)(formula, a, b);
}

static u8 ff_rail_ready(void)
{
    return (ff_state.magic == FF_MAGIC
            && ff_state.length == (u16)FF_LENGTH) ? 1u : 0u;
}

/*
 * A 0.005 bar word -> the (A, B) pair of formula 0x53, which the tester reads
 * as `((A << 8) | B) * 0.01` bar.  Halving the raw word is exactly what the
 * two stock handlers at 0x3DB94 (`prist`) and 0x3DBAC (`prsoll`) do, and it is
 * the arithmetic measuring_vars.md section 7.3 cross-checked; `>> 1` of a u16
 * can never exceed the 32767 the display word clamps at.
 */
static void ff_emit_bar(u32 raw)
{
    u32 v = (raw & 0xFFFFu) >> 1;

    ff_emit(FF_FMT_BAR, (v >> 8) & 0xFFu, v & 0xFFu);
}

/* id 2184, group 109 field 1: the applied rail adder, bar. */
void ff_diag_prail(void)
{
    if (!ff_rail_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit_bar((u32)ff_state.prail_add);
}

/* id 2185, group 109 field 2: the worst `prist` of the window, bar. */
void ff_diag_prist_min(void)
{
    if (!ff_rail_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit_bar((u32)ff_state.prist_min);
}

/*
 * id 2186, group 109 field 3: the worst injection-window margin of the window,
 * degCA, through formula 0x22 with A = FF_FMT_A_WIN = 225 -> 2.25 degCA per
 * count, which is exactly FF_WIN_COUNT_LSB = 96 angle LSB (see ff_state.h).
 *
 * The division FLOORS.  C truncates towards zero, which for a NEGATIVE margin
 * would round the answer towards "more room than there is"; the explicit
 * negative branch below turns that into a round towards minus infinity, so the
 * displayed margin is never optimistic.
 */
void ff_diag_win_margin(void)
{
    s32 m, q;

    if (!ff_rail_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    m = (s32)ff_state.win_margin_min;
    q = (m >= 0) ? m / FF_WIN_COUNT_LSB
                 : -((-m + (FF_WIN_COUNT_LSB - 1)) / FF_WIN_COUNT_LSB);
    q += (s32)FF_ZW_BIAS;
    if (q < 0)
        q = 0;
    else if (q > 0xFF)
        q = 0xFF;
    ff_emit(FF_FMT_ZW, FF_FMT_A_WIN, (u32)q);
}

/*
 * id 2187, group 109 field 4: activations of this window in which the MSV
 * volume request sat at VMSVMX, i.e. the pump had nothing left.  A plain
 * count (formula 0x36); anything above 0 on a steady-state pull is the signal
 * rail.md section 12.2 asks for, and the saturation at 255 cannot be reached
 * inside the shipped 1000 ms window (100 activations).
 */
void ff_diag_msv_sat(void)
{
    if (!ff_rail_ready()) {
        ff_emit(FF_FMT_NONE, 0u, 0u);
        return;
    }
    ff_emit(FF_FMT_COUNT, 0u, (u32)ff_state.msv_sat_ticks);
}
