/*
 * ff_fuel - the flex-fuel MVP (brief D1, issues #32 and #37).
 *
 * Two hooks, one calibration block, one RAM state block:
 *
 *   periodic (10 ms)   ff_fuel_tick_a / ff_fuel_tick_b
 *       poll the Pico frame on RX slot 15, validate it, run the
 *       OK/HOLD/FAULT machine, filter and slew-limit the ethanol estimate
 *       and publish the fuel factor F.
 *   segment-synchronous  ff_rk_scale
 *       the ONLY thing that runs per injection: rk = min((rk * F) >> 10,
 *       0xFFFF) on RAM 0x803038.  With F = 1024 it does not write rk at all,
 *       so E0 is bit-identical to stock.
 *
 * Layout, offsets and the FFCAL001 contract: src/ff_state.h.
 * Reference model and specification: emu/models/flexfuel.py.
 * Evidence for every stock address: re/findings/{can,injection,scheduler}.md.
 *
 * Safety properties this file is written to keep
 *   - the only stock RAM cell ever written is `rk` (0x803038), and only when
 *     F != 1024;
 *   - the only stock functions called are can_init_mb and can_rx_poll, both on
 *     slot 15, which no stock code touches;
 *   - no loop depends on data: the checksum loops are fixed-length, the
 *     calibration scan runs once per state-block initialisation;
 *   - no floating point, no division by a value that can be zero, no 64-bit
 *     arithmetic (there is no libgcc);
 *   - every calibration value is clamped in code before it is used, so a
 *     corrupt FFCAL001 cannot produce a factor outside [1024, 2048].
 */
#include "../../common/types.h"
#include "../../common/med9_stock.h"
#include "ff_state.h"

struct ff_state ff_state __attribute__((section(".bss.patch_state")));

_Static_assert(sizeof(struct ff_state) == 0x4C,
               "the state block layout in ff_state.h and README.md is 76 bytes"
               " since brief E5 (#36) grew E2's second core");
_Static_assert(FF_LENGTH % 4u == 0u,
               "ff_state_init() clears the block a WORD at a time, so the"
               " length has to be a multiple of four");
_Static_assert(sizeof(struct ff_state) == FF_LENGTH,
               "FF_LENGTH is the block length the header carries");
_Static_assert(FF_CORE_OFF + FF_CORE_LEN == 0x2Cu,
               "the first checksummed core must end where the annex begins");
_Static_assert(FF_CORE2_OFF + FF_CORE2_LEN == FF_LENGTH,
               "the second checksummed core must end where the block does");

void ff_fuel_tick_a(void);
void ff_fuel_tick_b(void);
void ff_rk_scale(void);

/* The calibration, as one activation sees it - already clamped. */
struct ff_cal {
    u16 can_id;
    u16 timeout_ms;
    u16 hold_s;
    u16 tau_ms;
    u16 slew_pct_s;
    u16 tick_ms;
    u8  mode;
    u8  e_override;
    u8  stall_max;
};

/* ------------------------------------------------------------- the block -- */
/*
 * The checksum covers TWO ranges since E2 (#35) grew the struct: D1's core
 * +08..+2B and E2's appended core +40..+43.  The annex in between has other
 * writers and carries no control value, which is the whole reason for the
 * split (see the header).  Summing them in this order is part of the contract
 * with `emu/models/flexfuel.py`, which concatenates the same two slices.
 */
static u16 ff_core_csum(void)
{
    const volatile u8 *p = (const volatile u8 *)((u32)&ff_state + FF_CORE_OFF);
    const volatile u8 *q = (const volatile u8 *)((u32)&ff_state + FF_CORE2_OFF);
    u32 s = 0u;
    u32 i;

    for (i = 0u; i < FF_CORE_LEN; i++)
        s += p[i];
    for (i = 0u; i < FF_CORE2_LEN; i++)
        s += q[i];
    return (u16)~s;
}

static u8 ff_cal_ok(void)
{
    u32 len, i, s;

    if (MED9_U32(FF_CAL_MAGIC0) != 0x46464341u)      /* "FFCA" */
        return 0u;
    if (MED9_U32(FF_CAL_MAGIC1) != 0x4C303031u)      /* "L001" */
        return 0u;
    if (MED9_U16(FF_CAL_BASE + FF_CAL_O_VERSION) != FF_CAL_VERSION)
        return 0u;
    len = MED9_U16(FF_CAL_BASE + FF_CAL_O_LENGTH);
    if (len < FF_CAL_LENGTH || len > 0x400u)
        return 0u;
    s = 0u;
    for (i = 0u; i < len - 2u; i++)
        s += MED9_U8(FF_CAL_BASE + i);
    if ((u16)~s != MED9_U16(FF_CAL_BASE + len - 2u))
        return 0u;
    return 1u;
}

/*
 * Nothing zeroes a patch's .bss and the cold start does not fill
 * 0x7FF770-0x7FFFEB (re/findings/ram.md section 3.2), so the contents are
 * undefined at power-on.  This runs whenever the header does not describe our
 * block: at every cold start, and after any corruption.
 */
static void ff_state_init(void)
{
    volatile u32 *p = (volatile u32 *)&ff_state;
    u32 i;

    for (i = 0u; i < FF_LENGTH / 4u; i++)
        p[i] = 0u;

    ff_state.magic = FF_MAGIC;
    ff_state.length = (u16)FF_LENGTH;
    ff_state.mode = (u8)FF_MODE_FAULT;    /* FAULT until the first good frame */
    ff_state.f_q10 = (u16)FF_F_MIN;       /* E0, bit-identical to stock       */
    ff_state.fst_q10 = (u16)FF_FST_ONE;   /* E2: the same, for the start      */
    ff_state.status = 0xFFu;              /* "no frame seen"                  */
    ff_state.cal_ok = ff_cal_ok();
    ff_state.cal_mode = ff_state.cal_ok
                      ? MED9_U8(FF_CAL_BASE + FF_CAL_O_MODE) : 0u;

    /* Arm TouCAN C message buffer 6.  Nothing else in the image arms slot 15
     * (re/findings/can.md section 7 step 4) and the call is idempotent, so
     * doing it here rather than editing can_rx_arm_all costs nothing. */
    if (ff_state.cal_ok && ff_state.cal_mode == 1u)
        MED9_FN(void (*)(u32), MED9_CAN_INIT_MB)(15u);

    /* D2 (#38): seed e_filt and e_key from EEP_CONF block 8 before the first
     * frame.  It only reads the block manager's RAM mirror, so it costs a
     * memcpy of one byte and cannot block.  A store that was never written
     * (0xFF) or is out of range leaves both at 0, i.e. E0. */
    ff_persist_init();
    ff_diag_publish();
    ff_state.csum = ff_core_csum();
}

/*
 * The tail of every activation, on every path out of ff_tick(): recompute the
 * ignition offset, refresh what the measuring-block handlers read (annex, so
 * it may happen here) and re-checksum the core.
 *
 * E1 (#34): `ff_zw_update()` belongs here and not in the mode-1 branch,
 * because it has to run on the activation that leaves OK/HOLD as well - that
 * is what makes the #37 ignition rule ("straight to gasoline, no hold, no
 * ramp") true by construction rather than by a branch somebody has to
 * remember.  It writes only `dzw_e` and `fzw_q8`, both core, both above, so
 * the checksum below still covers them.
 *
 * E5 (#36): `ff_rail_update()` is here for the third time and for the third
 * reason of the same shape: `prail_add` follows the #37 rule for anything that
 * ADDS (pressure, this time), so it has to be 0 on the activation the mode
 * leaves OK/HOLD/OVERRIDE.  Its three diagnostics run on every path too, which
 * is what makes them usable while the adder is disabled -- which is how the
 * file ships.  It writes only the five core-2 fields, so the checksum below
 * covers them.
 *
 * E2 (#35): `ff_start_update()` is here for the same reason and carries BOTH
 * #37 rules at once - its fuel half follows `e_filt` (so it inherits the hold
 * and the decay) and its ignition half drops to 0 on the activation the mode
 * leaves OK/HOLD/OVERRIDE.  It writes only `fst_q10` and `zwst_add`, which are
 * the second checksummed range, so the checksum below covers them too.
 */
static void ff_finish(void)
{
    ff_zw_update();
    ff_start_update();
    ff_rail_update();
    ff_diag_publish();
    ff_state.csum = ff_core_csum();
}

/* ------------------------------------------------------- the calibration -- */
static void ff_cal_load(struct ff_cal *c)
{
    c->can_id     = MED9_U16(FF_CAL_BASE + FF_CAL_O_CAN_ID);
    c->timeout_ms = MED9_U16(FF_CAL_BASE + FF_CAL_O_TIMEOUT);
    c->hold_s     = MED9_U16(FF_CAL_BASE + FF_CAL_O_HOLD_S);
    c->tau_ms     = MED9_U16(FF_CAL_BASE + FF_CAL_O_TAU_MS);
    c->slew_pct_s = MED9_U16(FF_CAL_BASE + FF_CAL_O_SLEW);
    c->tick_ms    = MED9_U16(FF_CAL_BASE + FF_CAL_O_TICK_MS);
    c->mode       = MED9_U8(FF_CAL_BASE + FF_CAL_O_MODE);
    c->e_override = MED9_U8(FF_CAL_BASE + FF_CAL_O_E_OVR);
    c->stall_max  = MED9_U8(FF_CAL_BASE + FF_CAL_O_STALL_MAX);

    if (c->tick_ms == 0u)
        c->tick_ms = 1u;
    else if (c->tick_ms > 1000u)
        c->tick_ms = 1000u;
    if (c->tau_ms < c->tick_ms)
        c->tau_ms = c->tick_ms;
    if (c->slew_pct_s == 0u)
        c->slew_pct_s = 1u;
    else if (c->slew_pct_s > 100u)
        c->slew_pct_s = 100u;
    if (c->stall_max == 0u)
        c->stall_max = 1u;
    if (c->e_override > 100u)
        c->e_override = 100u;
}

/* F(E) from ff_F_curve: 17 points, one every 6.25 % = 100 counts of 1/16 %. */
static u16 ff_f_of(u16 e)
{
    u32 i, fr;
    s32 a, b, f;

    if (e >= (u16)FF_E_FILT_MAX) {
        f = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_F_CURVE
                          + 2u * (FF_CURVE_N - 1u));
    } else {
        i = (u32)e / FF_CURVE_STEP;
        fr = (u32)e % FF_CURVE_STEP;
        a = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_F_CURVE + 2u * i);
        b = (s32)MED9_U16(FF_CAL_BASE + FF_CAL_O_F_CURVE + 2u * (i + 1u));
        f = a + ((b - a) * (s32)fr) / (s32)FF_CURVE_STEP;
    }
    if (f < (s32)FF_F_MIN)
        f = (s32)FF_F_MIN;
    else if (f > (s32)FF_F_MAX)
        f = (s32)FF_F_MAX;
    return (u16)f;
}

/*
 * Move E one activation towards `target` (1/16 %).
 *
 * The pair (e_filt, e_frac) is one value in 1/16384 %: at 10 ms the 2 %/s slew
 * limit is 0.32 counts of 1/16 %, which would truncate to zero and freeze the
 * filter without the sub-count.  `filtered` selects the first-order filter
 * (OK / bench override); the FAULT decay is a plain ramp at the slew limit,
 * which is what docs/05 section 3.2 asks for.
 */
static void ff_move(u16 target, u8 filtered, const struct ff_cal *c)
{
    s32 now = (s32)((u32)ff_state.e_filt * FF_FRAC + (u32)ff_state.e_frac);
    s32 d = (s32)((u32)target * FF_FRAC) - now;
    s32 step, slew;

    if (d == 0)
        return;
    if (filtered) {
        step = (d * (s32)(u32)c->tick_ms) / (s32)(u32)c->tau_ms;
        if (step == 0)
            step = (d > 0) ? 1 : -1;
    } else {
        step = d;
    }
    slew = (s32)(((u32)c->slew_pct_s * 16u * FF_FRAC * (u32)c->tick_ms) / 1000u);
    if (step > slew)
        step = slew;
    else if (step < -slew)
        step = -slew;

    now += step;
    if (now < 0)
        now = 0;
    else if (now > (s32)(FF_E_FILT_MAX * FF_FRAC))
        now = (s32)(FF_E_FILT_MAX * FF_FRAC);
    ff_state.e_filt = (u16)((u32)now / FF_FRAC);
    ff_state.e_frac = (u16)((u32)now % FF_FRAC);
}

/* ------------------------------------------------------- one activation --- */
static void ff_tick(u8 src)
{
    struct ff_cal c;
    const volatile u8 *rx = (const volatile u8 *)MED9_CAN_RX_BUF_SPARE0;
    u32 dlc;
    u8 bad, new_mode;

    if (ff_state.magic != FF_MAGIC
        || ff_state.length != (u16)FF_LENGTH
        || ff_state.csum != ff_core_csum())
        ff_state_init();

    /*
     * Which of the two 10 ms hooks owns the tick.  Exactly one task set is
     * live (re/findings/scheduler.md section 11.7) and the dump cannot say
     * which, so both are hooked.  The first caller takes ownership; ownership
     * moves only after FF_OWNER_SWITCH consecutive calls from the other source
     * with none from the owner in between, which covers both the real case
     * (set A runs until 0x11DAF4 switches to set B) and the impossible one
     * (both fire, and the rate stays exactly one tick per raster period).
     */
    ff_state.src_seen = (u8)(ff_state.src_seen | src);
    if (ff_state.src_owner == 0u)
        ff_state.src_owner = src;
    if (ff_state.src_owner != src) {
        if (ff_state.src_foreign < 0xFFu)
            ff_state.src_foreign = (u8)(ff_state.src_foreign + 1u);
        if (ff_state.src_foreign < (u8)FF_OWNER_SWITCH) {
            ff_finish();
            return;
        }
        ff_state.src_owner = src;
        ff_state.src_foreign = 0u;
    } else {
        ff_state.src_foreign = 0u;
    }

    /* `cal_ok` is latched by ff_state_init(): FFCAL001 is flash, it cannot
     * change while the engine runs, and its checksum is 230 bytes long.  The
     * mode byte is re-read every activation so that a logger sees what the
     * code is actually acting on. */
    ff_state.ticks = ff_state.ticks + 1u;
    ff_cal_load(&c);
    ff_state.cal_mode = ff_state.cal_ok ? c.mode : 0u;

    /* mode 0, or no usable calibration: F is forced to 1.000 and no CAN. */
    if (!ff_state.cal_ok || ff_state.cal_mode == 0u) {
        ff_state.mode = (u8)FF_MODE_OFF;
        ff_state.f_q10 = (u16)FF_F_MIN;
        ff_finish();
        return;
    }

    /* mode 2: bench override, no sensor and no CAN needed. */
    if (ff_state.cal_mode == 2u) {
        ff_state.mode = (u8)FF_MODE_OVERRIDE;
        ff_state.age_ticks = 0u;
        ff_move((u16)((u32)c.e_override * 16u), 1u, &c);
        ff_state.f_q10 = ff_f_of(ff_state.e_filt);
        ff_finish();
        return;
    }

    /* mode 1: the real thing. */
    dlc = MED9_FN(u32 (*)(u32), MED9_CAN_RX_POLL)(15u);
    if (dlc == 8u) {
        bad = 0u;
        /* The id echo proves the slot really carries our frame; without the
         * flash edit at 0x2BD8C or without can_init_mb it will not. */
        if (MED9_U32(MED9_CAN_RX_BUF_SPARE0 - 4u) != (u32)c.can_id)
            bad = 1u;
        if (rx[3] == ff_state.frame_ctr) {
            if (ff_state.stall < 0xFFu)
                ff_state.stall = (u8)(ff_state.stall + 1u);
        } else {
            ff_state.stall = 0u;
        }
        ff_state.frame_ctr = rx[3];
        ff_state.status = rx[7];
        ff_state.e_raw = rx[0];
        ff_state.t_fuel = rx[1];
        ff_state.fw_ver = rx[6];
        if (rx[0] > 100u || rx[7] == 1u || rx[7] == 3u
            || ff_state.stall >= c.stall_max)
            bad = 1u;
        /* A latch, not an event: the frame rate is 10 Hz and the raster is
         * 100 Hz, so the condition has to survive the nine activations in
         * between (see the note in ff_state.h). */
        ff_state.frame_bad = bad;
        if (!bad) {
            ff_state.age_ticks = 0u;
            if (ff_state.frames < 0xFFFFu)
                ff_state.frames = (u16)(ff_state.frames + 1u);
        }
    }

    if (ff_state.age_ticks < 0xFFFFu)
        ff_state.age_ticks = (u16)(ff_state.age_ticks + 1u);

    if (ff_state.frame_bad || ff_state.frames == 0u
        || (u32)ff_state.age_ticks * (u32)c.tick_ms > (u32)c.timeout_ms)
        new_mode = (u8)FF_MODE_FAULT;
    else if (ff_state.status == 2u)
        new_mode = (u8)FF_MODE_HOLD;
    else
        new_mode = (u8)FF_MODE_OK;

    if (new_mode == (u8)FF_MODE_FAULT && ff_state.mode != (u8)FF_MODE_FAULT) {
        u32 hold;
        if (ff_state.faults < 0xFFFFu)
            ff_state.faults = (u16)(ff_state.faults + 1u);
        hold = ((u32)c.hold_s * 1000u) / (u32)c.tick_ms;
        ff_state.hold_ticks = (hold > 0xFFFFu) ? 0xFFFFu : (u16)hold;
    }
    ff_state.mode = new_mode;

    if (new_mode == (u8)FF_MODE_OK) {
        ff_move((u16)((u32)ff_state.e_raw * 16u), 1u, &c);
    } else if (new_mode == (u8)FF_MODE_FAULT) {
        if (ff_state.hold_ticks != 0u)
            ff_state.hold_ticks = (u16)(ff_state.hold_ticks - 1u);
        else
            ff_move(ff_state.e_key, 0u, &c);
    }
    /* HOLD: E is frozen, so F is frozen with it. */

    ff_state.f_q10 = ff_f_of(ff_state.e_filt);
    ff_finish();
    /* D2 (#38): only mode 1 persists.  ff_finish() has just refreshed
     * `diag_e_pct`, which is the rounded percent the store keeps, and the
     * store writes annex fields only, so the checksum above still holds. */
    ff_persist_tick(c.tick_ms);
}

/* Called by the HOOK_TAIL trampolines in src/hooks.S. */
void ff_fuel_tick_a(void)
{
    ff_tick((u8)FF_SRC_A);
}

void ff_fuel_tick_b(void)
{
    ff_tick((u8)FF_SRC_B);
}

/*
 * The segment-synchronous half, in place of the `bl rksplit` at 0x42247C.
 *
 * `rk` (0x803038) is u16 with no implicit fraction and exactly one writer
 * upstream of this point (re/findings/injection.md section 6).  F = 1024 takes
 * the early return and does not write rk at all, which is what makes E0
 * bit-identical.  A state block that does not carry our magic - the first few
 * milliseconds after power-up - is the same case.
 */
void ff_rk_scale(void)
{
    u32 rk, f;

    ff_state.rk_calls = ff_state.rk_calls + 1u;
    if (ff_state.magic != FF_MAGIC)
        return;
    f = ff_state.f_q10;
    if (f <= FF_F_MIN)
        return;
    if (f > FF_F_MAX)
        f = FF_F_MAX;
    rk = (u32)MED9_U16(MED9_RK);
    rk = (rk * f) >> 10;
    if (rk > 0xFFFFu)
        rk = 0xFFFFu;
    MED9_U16(MED9_RK) = (u16)rk;
}
