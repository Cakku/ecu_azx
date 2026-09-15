#include "display.h"

#include "hardware/i2c.h"
#include "pico/stdlib.h"

#include <cstdio>

/* Harbys/pico-ssd1306 */
#include "shapeRenderer/ShapeRenderer.h"
#include "ssd1306.h"
#include "textRenderer/TextRenderer.h"

#define DISP_I2C_PORT i2c0
#define DISP_SDA_PIN 4
#define DISP_SCL_PIN 5
#define DISP_ADDR 0x3C
#define DISP_WIDTH 128
#define DISP_HEIGHT 64

static pico_ssd1306::SSD1306 *s_display = nullptr;

void display_init(void)
{
    i2c_init(DISP_I2C_PORT, 400000);
    gpio_set_function(DISP_SDA_PIN, GPIO_FUNC_I2C);
    gpio_set_function(DISP_SCL_PIN, GPIO_FUNC_I2C);
    gpio_pull_up(DISP_SDA_PIN);
    gpio_pull_up(DISP_SCL_PIN);

    /* Instantiated after I2C is up: a global constructor would run before
     * main() and hang on the first transfer. */
    s_display = new pico_ssd1306::SSD1306(DISP_I2C_PORT, DISP_ADDR,
                                          pico_ssd1306::Size::W128xH64);
    s_display->setOrientation(false);
    s_display->turnOn();
}

void display_splash(const char *line1, const char *line2)
{
    if (s_display == nullptr) {
        return;
    }
    s_display->clear();
    pico_ssd1306::drawText(s_display, font_8x8, line1 ? line1 : "", 0, 16);
    pico_ssd1306::drawText(s_display, font_8x8, line2 ? line2 : "", 0, 32);
    s_display->sendBuffer();
}

void display_update(const ff_output_t *out, bool can_ok, uint16_t can_id)
{
    char buf[24];

    if (s_display == nullptr || out == nullptr) {
        return;
    }

    s_display->clear();

    /* Header: identifier being transmitted and the CAN health. */
    snprintf(buf, sizeof(buf), "FF 0x%03X", (unsigned)(can_id & 0x7FFu));
    pico_ssd1306::drawText(s_display, font_8x8, buf, 0, 0);
    pico_ssd1306::drawText(s_display, font_8x8, can_ok ? "CAN OK" : "CAN ER", 76, 0);
    pico_ssd1306::drawLine(s_display, 0, 10, 127, 10);

    /* Ethanol content, the headline number. */
    snprintf(buf, sizeof(buf), "E%3u%%", (unsigned)out->ethanol_pct);
    pico_ssd1306::drawText(s_display, font_12x16, buf, 0, 14);

    /* Fuel temperature. */
    snprintf(buf, sizeof(buf), "T %4d C", (int)out->fuel_temp_c);
    pico_ssd1306::drawText(s_display, font_8x8, buf, 64, 18);

    /* Raw frequency with one decimal. */
    {
        uint32_t f_x10 = (out->freq_avg_mhz + 50u) / 100u;
        snprintf(buf, sizeof(buf), "f %3u.%01u Hz", (unsigned)(f_x10 / 10u),
                 (unsigned)(f_x10 % 10u));
        pico_ssd1306::drawText(s_display, font_8x8, buf, 0, 34);
    }

    /* Status line. */
    snprintf(buf, sizeof(buf), "S %s", ff_status_name(out->status));
    pico_ssd1306::drawText(s_display, font_8x8, buf, 0, 46);

    /* Ethanol bar graph across the bottom. */
    {
        uint8_t pct = out->ethanol_pct > 100u ? 100u : out->ethanol_pct;
        uint8_t fill = (uint8_t)((pct * 127u) / 100u);
        pico_ssd1306::drawRect(s_display, 0, 56, 127, 63);
        if (fill > 0u) {
            pico_ssd1306::fillRect(s_display, 0, 56, fill, 63);
        }
    }

    s_display->sendBuffer();
}
