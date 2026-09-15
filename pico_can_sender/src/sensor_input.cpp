#include "sensor_input.h"

#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

/* Power of two so the index wrap is a mask. 128 edges is 320 ms of margin at
 * the 200 Hz top of the plausibility window. */
#define SI_RING_LEN 128u
#define SI_RING_MASK (SI_RING_LEN - 1u)

static volatile uint32_t s_head;
static volatile uint32_t s_tail;
static volatile uint64_t s_ts[SI_RING_LEN];
static volatile uint8_t s_lvl[SI_RING_LEN];
static volatile uint32_t s_overflow;
static unsigned s_gpio = FF_SENSOR_GPIO;

static void si_irq(uint gpio, uint32_t events)
{
    if (gpio != s_gpio) {
        return;
    }

    const uint64_t now = time_us_64();
    const uint32_t head = s_head;
    const uint32_t next = (head + 1u) & SI_RING_MASK;

    if (next == s_tail) {
        s_overflow++; /* main loop is too slow / the input is oscillating */
        return;
    }

    uint8_t level;
    const uint32_t both = GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL;
    if ((events & both) == both) {
        /* Two edges coalesced into one interrupt: only the resulting level is
         * still knowable. The lost edge is shorter than the debounce window
         * anyway, so ff_on_edge() would have rejected it. */
        level = gpio_get(gpio) ? 1u : 0u;
    } else {
        level = (events & GPIO_IRQ_EDGE_RISE) ? 1u : 0u;
    }

    s_ts[head] = now;
    s_lvl[head] = level;
    __dmb();
    s_head = next;
}

void sensor_input_init(unsigned gpio)
{
    s_gpio = gpio;
    s_head = 0;
    s_tail = 0;
    s_overflow = 0;

    gpio_init(gpio);
    gpio_set_dir(gpio, GPIO_IN);
    /* No internal pull: the sensor's open collector is pulled up externally to
     * 5 V through 2.2-3.5 kOhm and divided down to 3.3 V. An internal pull
     * would fight the divider. */
    gpio_disable_pulls(gpio);
    gpio_set_input_hysteresis_enabled(gpio, true);

    gpio_set_irq_enabled_with_callback(
        gpio, GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL, true, &si_irq);
}

uint32_t sensor_input_drain(ff_state_t *st)
{
    uint32_t n = 0;
    uint32_t tail = s_tail;

    while (tail != s_head) {
        const uint64_t t = s_ts[tail];
        const bool high = s_lvl[tail] != 0u;
        __dmb();
        tail = (tail + 1u) & SI_RING_MASK;
        s_tail = tail;
        ff_on_edge(st, t, high);
        n++;
    }
    return n;
}

uint32_t sensor_input_overflows(void) { return s_overflow; }

bool sensor_input_level(void) { return gpio_get(s_gpio) != 0; }
