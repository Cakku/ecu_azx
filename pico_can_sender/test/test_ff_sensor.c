/*
 * test_ff_sensor.c - host test for the flex-fuel measurement/state logic.
 *
 * Covers the frequency/status table of docs/05_flexfuel_design.md section 6
 * ("Pico status paths ... 40, 49, 50, 100, 150, 156, 185 Hz, no signal") plus
 * the plausibility window boundaries, the temperature characteristic, the
 * debounce, the dropout/recovery path and the CAN frame encoding.
 *
 * Build and run (macOS / any POSIX host):
 *     cc -std=c11 -Wall -Wextra -O2 -I../src test_ff_sensor.c ../src/ff_sensor.c \
 *        -o build/test_ff_sensor && ./build/test_ff_sensor
 * or simply:
 *     ./run_tests.sh
 *
 * The simulator feeds synthetic edge timestamps, exactly like the GPIO
 * interrupt does on the Pico: a falling edge starts the low pulse, the rising
 * edge ends it, the next falling edge closes the period.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ff_sensor.h"

/* ------------------------------------------------------------- assertions */

static int g_checks;
static int g_failures;

static void check_i(long actual, long expected, const char *what)
{
    g_checks++;
    if (actual != expected) {
        g_failures++;
        printf("    FAIL  %-34s got %ld, expected %ld\n", what, actual, expected);
    }
}

static void check_str(const char *actual, const char *expected, const char *what)
{
    g_checks++;
    if (strcmp(actual, expected) != 0) {
        g_failures++;
        printf("    FAIL  %-34s got \"%s\", expected \"%s\"\n", what, actual,
               expected);
    }
}

/* -------------------------------------------------------------- simulator */

typedef struct {
    ff_state_t st;
    uint64_t now;
} sim_t;

static uint32_t period_us_for(uint32_t freq_mhz)
{
    return (uint32_t)((1000000000ull + freq_mhz / 2u) / freq_mhz);
}

static void sim_init(sim_t *s, const ff_config_t *cfg)
{
    memset(s, 0, sizeof(*s));
    s->now = 1000000ull; /* start away from 0 to catch t==0 assumptions */
    ff_init(&s->st, cfg, s->now);
}

/* Drive a square wave: low for low_us out of every period, for duration_ms.
 * glitch_us != 0 injects a rising+falling glitch pair that many microseconds
 * after every falling edge (used for the debounce test). */
static void sim_run_ex(sim_t *s, uint32_t freq_mhz, uint32_t low_us,
                       uint32_t duration_ms, uint32_t glitch_us)
{
    uint32_t period = period_us_for(freq_mhz);
    uint64_t end = s->now + (uint64_t)duration_ms * 1000ull;
    uint64_t next_fall = s->now;
    uint64_t next_rise = s->now + low_us;
    uint64_t next_tick = s->now + 1000ull;

    while (s->now < end) {
        uint64_t t = next_tick;
        if (next_fall < t) {
            t = next_fall;
        }
        if (next_rise < t) {
            t = next_rise;
        }
        if (t > end) {
            t = end;
        }
        s->now = t;

        if (t == next_fall) {
            ff_on_edge(&s->st, t, false);
            if (glitch_us != 0u) {
                ff_on_edge(&s->st, t + glitch_us, true);
                ff_on_edge(&s->st, t + 2u * glitch_us, false);
            }
            next_fall += period;
        }
        if (t == next_rise) {
            ff_on_edge(&s->st, t, true);
            next_rise += period;
        }
        if (t == next_tick) {
            next_tick += 1000ull;
        }
        ff_update(&s->st, s->now);
    }
}

static void sim_run(sim_t *s, uint32_t freq_mhz, uint32_t low_us,
                    uint32_t duration_ms)
{
    sim_run_ex(s, freq_mhz, low_us, duration_ms, 0u);
}

/* Inverted wiring: the level passed to ff_on_edge is flipped. */
static void sim_run_inverted(sim_t *s, uint32_t freq_mhz, uint32_t low_us,
                             uint32_t duration_ms)
{
    uint32_t period = period_us_for(freq_mhz);
    uint64_t end = s->now + (uint64_t)duration_ms * 1000ull;
    uint64_t next_fall = s->now;
    uint64_t next_rise = s->now + low_us;

    while (s->now < end) {
        uint64_t t = (next_fall < next_rise) ? next_fall : next_rise;
        if (t > end) {
            t = end;
        }
        s->now = t;
        if (t == next_fall) {
            ff_on_edge(&s->st, t, true); /* inverted: sensor low -> pin high */
            next_fall += period;
        }
        if (t == next_rise) {
            ff_on_edge(&s->st, t, false);
            next_rise += period;
        }
        ff_update(&s->st, s->now);
    }
}

/* No edges at all for duration_ms, ff_update called every millisecond. */
static void sim_idle(sim_t *s, uint32_t duration_ms)
{
    uint32_t i;
    for (i = 0; i < duration_ms; i++) {
        s->now += 1000ull;
        ff_update(&s->st, s->now);
    }
}

/* ------------------------------------------------- 1. pure conversion math */

static void test_conversions(void)
{
    ff_config_t cfg;
    ff_config_default(&cfg);

    printf("1. Conversions\n");

    check_i(ff_mhz_from_period_us(10000), 100000, "f(10000us)");
    check_i(ff_mhz_from_period_us(20000), 50000, "f(20000us)");
    check_i(ff_mhz_from_period_us(0), 0, "f(0us)");

    check_i(ff_ethanol_pct_from_mhz(50000), 0, "E(50.000Hz)");
    check_i(ff_ethanol_pct_from_mhz(49000), 0, "E(49.000Hz) clamped");
    check_i(ff_ethanol_pct_from_mhz(75000), 25, "E(75.000Hz)");
    check_i(ff_ethanol_pct_from_mhz(100000), 50, "E(100.000Hz)");
    check_i(ff_ethanol_pct_from_mhz(150000), 100, "E(150.000Hz)");
    check_i(ff_ethanol_pct_from_mhz(185000), 100, "E(185.000Hz) clamped");

    /* T = 41.5 * low_ms - 81.25 */
    check_i(ff_temp_c_from_low_us(1000), -40, "T(1.0ms)");
    check_i(ff_temp_c_from_low_us(1500), -19, "T(1.5ms)");
    check_i(ff_temp_c_from_low_us(2000), 2, "T(2.0ms)");
    check_i(ff_temp_c_from_low_us(3000), 43, "T(3.0ms)");
    check_i(ff_temp_c_from_low_us(4000), 85, "T(4.0ms)");
    check_i(ff_temp_c_from_low_us(5000), 125, "T(5.0ms) clamped to 125");

    check_i(ff_status_from_mhz(&cfg, 44999), FF_STATUS_FAULT, "status 44.999Hz");
    check_i(ff_status_from_mhz(&cfg, 45000), FF_STATUS_OK, "status 45.000Hz");
    check_i(ff_status_from_mhz(&cfg, 155000), FF_STATUS_OK, "status 155.000Hz");
    check_i(ff_status_from_mhz(&cfg, 155001), FF_STATUS_CONTAMINATED,
            "status 155.001Hz");
    check_i(ff_status_from_mhz(&cfg, 200000), FF_STATUS_CONTAMINATED,
            "status 200.000Hz");
    check_i(ff_status_from_mhz(&cfg, 200001), FF_STATUS_FAULT, "status 200.001Hz");

    check_str(ff_status_name(FF_STATUS_OK), "OK", "name OK");
    check_str(ff_status_name(FF_STATUS_FAULT), "FAULT", "name FAULT");
    check_str(ff_status_name(FF_STATUS_CONTAMINATED), "CONTAM", "name CONTAM");
    check_str(ff_status_name(FF_STATUS_NOT_READY), "INIT", "name INIT");
}

/* ---------------------------------- 2. the design's frequency/status table */

typedef struct {
    const char *label;
    uint32_t freq_mhz;
    uint32_t low_us;
    uint8_t exp_pct;
    int16_t exp_temp_c;
    ff_status_t exp_status;
    uint8_t exp_b0, exp_b1, exp_b2, exp_b7;
} tbl_case_t;

static void run_table(const char *title, const tbl_case_t *cases, size_t n)
{
    size_t i;
    printf("%s\n", title);
    printf("    %-12s %-9s %-8s %-6s %-7s  %s\n", "signal", "status", "E[%]",
           "T[C]", "f/2", "frame");
    for (i = 0; i < n; i++) {
        const tbl_case_t *c = &cases[i];
        sim_t s;
        uint8_t d[8];
        const ff_output_t *o;
        char what[96];

        sim_init(&s, NULL);
        if (c->freq_mhz == 0u) {
            sim_idle(&s, 1000);
        } else {
            sim_run(&s, c->freq_mhz, c->low_us, 1000);
        }
        o = ff_output(&s.st);
        ff_build_frame(o, 0x5A, d);

        printf("    %-12s %-9s %-8u %-6d %-7u  %02X %02X %02X %02X %02X %02X %02X %02X\n",
               c->label, ff_status_name(o->status), o->ethanol_pct,
               o->fuel_temp_c, d[2], d[0], d[1], d[2], d[3], d[4], d[5], d[6],
               d[7]);

        snprintf(what, sizeof(what), "%s status", c->label);
        check_i(o->status, c->exp_status, what);
        snprintf(what, sizeof(what), "%s ethanol %%", c->label);
        check_i(o->ethanol_pct, c->exp_pct, what);
        snprintf(what, sizeof(what), "%s temperature", c->label);
        check_i(o->fuel_temp_c, c->exp_temp_c, what);
        snprintf(what, sizeof(what), "%s frame byte0", c->label);
        check_i(d[0], c->exp_b0, what);
        snprintf(what, sizeof(what), "%s frame byte1", c->label);
        check_i(d[1], c->exp_b1, what);
        snprintf(what, sizeof(what), "%s frame byte2", c->label);
        check_i(d[2], c->exp_b2, what);
        snprintf(what, sizeof(what), "%s frame byte3", c->label);
        check_i(d[3], 0x5A, what);
        snprintf(what, sizeof(what), "%s frame byte4", c->label);
        check_i(d[4], 0, what);
        snprintf(what, sizeof(what), "%s frame byte5", c->label);
        check_i(d[5], 0, what);
        snprintf(what, sizeof(what), "%s frame byte6", c->label);
        check_i(d[6], FF_FW_VERSION, what);
        snprintf(what, sizeof(what), "%s frame byte7", c->label);
        check_i(d[7], c->exp_b7, what);
    }
}

static void test_design_table(void)
{
    /* All rows are driven with a 2.0 ms low pulse -> T = 41.5*2 - 81.25 = 1.75
     * -> 2 C -> byte1 = 42. A fresh state is used per row, so on FAULT there is
     * no plausible reading to hold and byte0/byte1 read 0 / -40 C. */
    static const tbl_case_t cases[] = {
        {"40 Hz", 40000, 2000, 0, -40, FF_STATUS_FAULT, 0, 0, 0, 1},
        /* byte2 is round(f/2): 49/2 = 24.5 and 50/2 = 25.0 both encode as 25,
         * which is simply the 2 Hz resolution of that byte. */
        {"49 Hz", 49000, 2000, 0, 2, FF_STATUS_OK, 0, 42, 25, 0},
        {"50 Hz", 50000, 2000, 0, 2, FF_STATUS_OK, 0, 42, 25, 0},
        {"100 Hz", 100000, 2000, 50, 2, FF_STATUS_OK, 50, 42, 50, 0},
        {"150 Hz", 150000, 2000, 100, 2, FF_STATUS_OK, 100, 42, 75, 0},
        {"156 Hz", 156000, 2000, 100, 2, FF_STATUS_CONTAMINATED, 100, 42, 78, 2},
        {"185 Hz", 185000, 2000, 100, 2, FF_STATUS_CONTAMINATED, 100, 42, 93, 2},
        {"no signal", 0, 0, 0, -40, FF_STATUS_FAULT, 0, 0, 0, 1},
    };
    run_table("2. Design table (docs/05_flexfuel_design.md section 6)", cases,
              sizeof(cases) / sizeof(cases[0]));
}

static void test_window_boundaries(void)
{
    static const tbl_case_t cases[] = {
        {"44.9 Hz", 44900, 2000, 0, -40, FF_STATUS_FAULT, 0, 0, 0, 1},
        {"45 Hz", 45000, 2000, 0, 2, FF_STATUS_OK, 0, 42, 23, 0},
        {"155 Hz", 155000, 2000, 100, 2, FF_STATUS_OK, 100, 42, 77, 0},
        {"200 Hz", 200000, 2000, 100, 2, FF_STATUS_CONTAMINATED, 100, 42, 100, 2},
        {"201 Hz", 201000, 2000, 0, -40, FF_STATUS_FAULT, 0, 0, 0, 1},
    };
    run_table("3. Plausibility window boundaries", cases,
              sizeof(cases) / sizeof(cases[0]));
}

/* --------------------------------------------------- 4. temperature sweep */

static void test_temperature(void)
{
    static const struct {
        uint32_t low_us;
        int16_t exp_c;
        uint8_t exp_b1;
    } cases[] = {
        {1000, -40, 0}, {1500, -19, 21}, {2000, 2, 42},
        {3000, 43, 83}, {4000, 85, 125}, {5000, 125, 165},
    };
    size_t i;

    printf("4. Fuel temperature (100 Hz carrier, T = 41.5*low_ms - 81.25)\n");
    printf("    %-10s %-8s %s\n", "low pulse", "T[C]", "byte1");
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        sim_t s;
        uint8_t d[8];
        const ff_output_t *o;
        char what[64];

        sim_init(&s, NULL);
        sim_run(&s, 100000u, cases[i].low_us, 1000);
        o = ff_output(&s.st);
        ff_build_frame(o, 0, d);
        printf("    %-10.1f %-8d %u\n", cases[i].low_us / 1000.0, o->fuel_temp_c,
               d[1]);
        snprintf(what, sizeof(what), "T at %u us", cases[i].low_us);
        check_i(o->fuel_temp_c, cases[i].exp_c, what);
        snprintf(what, sizeof(what), "byte1 at %u us", cases[i].low_us);
        check_i(d[1], cases[i].exp_b1, what);
        snprintf(what, sizeof(what), "status at %u us", cases[i].low_us);
        check_i(o->status, FF_STATUS_OK, what);
    }
}

/* ------------------------------------------------ 5. startup / timeout etc */

static void test_not_ready_and_timeout(void)
{
    sim_t s;
    const ff_output_t *o;
    uint8_t d[8];

    printf("5. Startup, dropout and recovery\n");

    /* Fresh state, no edges: NOT_READY until the 500 ms timeout expires. */
    sim_init(&s, NULL);
    sim_idle(&s, 100);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_NOT_READY, "100 ms after boot, no signal");
    ff_build_frame(o, 0, d);
    check_i(d[7], 3, "byte7 == 3 (not ready)");
    check_i(d[0], 0, "byte0 == 0 when not ready");
    check_i(d[1], 0, "byte1 == 0 when not ready");
    check_i(d[2], 0, "byte2 == 0 when not ready");

    sim_idle(&s, 450); /* 550 ms total */
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_FAULT, "550 ms after boot, no signal");

    /* Signal present but fewer than FF_RING_LEN samples: still NOT_READY. */
    sim_init(&s, NULL);
    sim_run(&s, 50000u, 2000u, 40); /* 50 Hz: only 2 periods in 40 ms */
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_NOT_READY, "2 periods only -> not ready");

    /* 100 Hz, then the signal dies: FAULT, holding the last plausible value. */
    sim_init(&s, NULL);
    sim_run(&s, 100000u, 2000u, 1000);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_OK, "100 Hz -> OK");
    check_i(o->ethanol_pct, 50, "100 Hz -> E50");

    sim_idle(&s, 400);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_OK, "400 ms dropout still OK (< 500 ms)");

    sim_idle(&s, 150); /* 550 ms without an edge */
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_FAULT, "550 ms dropout -> FAULT");
    check_i(o->ethanol_pct, 50, "FAULT holds the last plausible E%");
    ff_build_frame(o, 7, d);
    check_i(d[0], 50, "byte0 held at 50 on FAULT");
    check_i(d[1], 42, "byte1 held at 2 C on FAULT");
    check_i(d[2], 0, "byte2 == 0 on FAULT");
    check_i(d[7], 1, "byte7 == 1 on FAULT");
    check_i(s.st.timeouts, 1, "one timeout counted");

    /* Recovery on a different blend. */
    sim_run(&s, 150000u, 3000u, 1000);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_OK, "recovered to OK");
    check_i(o->ethanol_pct, 100, "recovered E100");
    check_i(o->fuel_temp_c, 43, "recovered T 43 C");

    /* An implausible frequency faults without waiting for the timeout, and it
     * keeps holding the last plausible reading. */
    sim_run(&s, 40000u, 3000u, 600);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_FAULT, "40 Hz after E100 -> FAULT");
    check_i(o->ethanol_pct, 100, "40 Hz after E100 holds E100");
}

/* ------------------------------------------------------------ 6. debounce */

static void test_debounce(void)
{
    sim_t s;
    const ff_output_t *o;

    printf("6. Debounce (0.25 ms) and inverted input\n");

    /* 100 us glitch pair after every falling edge must be swallowed. */
    sim_init(&s, NULL);
    sim_run_ex(&s, 100000u, 2000u, 1000, 100u);
    o = ff_output(&s.st);
    check_i(o->status, FF_STATUS_OK, "glitched 100 Hz still OK");
    check_i(o->ethanol_pct, 50, "glitched 100 Hz still E50");
    check_i(o->fuel_temp_c, 2, "glitched 100 Hz still 2 C");
    check_i(s.st.edges_rejected > 100, 1, "glitches were rejected");

    /* Inverted input stage produces the same measurement. */
    {
        ff_config_t cfg;
        ff_config_default(&cfg);
        cfg.invert_input = true;
        sim_init(&s, &cfg);
        sim_run_inverted(&s, 100000u, 2000u, 1000);
        o = ff_output(&s.st);
        check_i(o->status, FF_STATUS_OK, "inverted input OK");
        check_i(o->ethanol_pct, 50, "inverted input E50");
        check_i(o->fuel_temp_c, 2, "inverted input 2 C");
    }
}

/* ------------------------------------------------ 7. frame / configuration */

static void test_frame_and_config(void)
{
    ff_config_t cfg;
    sim_t s;
    uint8_t d[8];
    int i;

    printf("7. Frame layout and runtime configuration\n");

    ff_config_default(&cfg);
    check_i(cfg.can_id, FF_CAN_ID_DEFAULT, "default CAN id");
    check_i(cfg.can_id, 0x0EC, "default CAN id == 0x0EC");
    check_i(cfg.debounce_us, 250, "debounce 0.25 ms");
    check_i(cfg.update_ms, 250, "update 250 ms");
    check_i(cfg.signal_timeout_ms, 500, "timeout 500 ms");

    /* Runtime override of the identifier. */
    cfg.can_id = 0x123;
    sim_init(&s, &cfg);
    check_i(s.st.cfg.can_id, 0x123, "runtime CAN id override");

    /* Rolling counter wraps through every value and lands in byte 3. */
    ff_config_default(&cfg);
    sim_init(&s, &cfg);
    sim_run(&s, 100000u, 2000u, 1000);
    for (i = 0; i < 256; i++) {
        ff_build_frame(ff_output(&s.st), (uint8_t)i, d);
        if (d[3] != (uint8_t)i) {
            check_i(d[3], i, "rolling counter in byte 3");
            break;
        }
    }
    g_checks++; /* the loop above counts as one check when it completes */
    ff_build_frame(ff_output(&s.st), 0xFF, d);
    check_i(d[3], 0xFF, "counter 255");
    check_i(d[4], 0, "byte4 reserved 0");
    check_i(d[5], 0, "byte5 reserved 0");
    check_i(d[6], FF_FW_VERSION, "byte6 firmware version");
}

int main(void)
{
    printf("ff_sensor host tests - flex-fuel node measurement logic\n");
    printf("=======================================================\n\n");

    test_conversions();
    printf("\n");
    test_design_table();
    printf("\n");
    test_window_boundaries();
    printf("\n");
    test_temperature();
    printf("\n");
    test_not_ready_and_timeout();
    printf("\n");
    test_debounce();
    printf("\n");
    test_frame_and_config();
    printf("\n");

    printf("=======================================================\n");
    printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
