#include "display.h"
#include "hardware/i2c.h"
#include "pico/stdlib.h"
#include <cstdio>

// Harbys library includes
#include "shapeRenderer/ShapeRenderer.h"
#include "ssd1306.h"
#include "textRenderer/TextRenderer.h"


// I2C Pin definitions
#define DISP_I2C_PORT i2c0
#define DISP_SDA_PIN 4
#define DISP_SCL_PIN 5
#define DISP_ADDR 0x3C
#define DISP_WIDTH 128
#define DISP_HEIGHT 64

// Create the display object
pico_ssd1306::SSD1306 display(DISP_I2C_PORT, DISP_ADDR,
                              pico_ssd1306::Size::W128xH64);

void display_init(void) {
  // I2C Init
  i2c_init(DISP_I2C_PORT, 400000); // 400kHz
  gpio_set_function(DISP_SDA_PIN, GPIO_FUNC_I2C);
  gpio_set_function(DISP_SCL_PIN, GPIO_FUNC_I2C);
  gpio_pull_up(DISP_SDA_PIN);
  gpio_pull_up(DISP_SCL_PIN);

  // Display Init
  display.setOrientation(false); // 0 = false (not flipped)
  display.turnOn();
}

void display_update(uint8_t pot_val, bool can_status) {
  display.clear();

  // Header
  pico_ssd1306::drawText(&display, font_8x8, "CAN Sender", 0, 0);

  // Status
  if (can_status) {
    pico_ssd1306::drawText(&display, font_8x8, "OK", 80, 0);
  } else {
    pico_ssd1306::drawText(&display, font_8x8, "ERR", 80, 0);
  }

  // CAN ID Info
  pico_ssd1306::drawText(&display, font_8x8, "ID: 0x123", 0, 16);

  // Value Text
  char buf[16];
  sprintf(buf, "Val: %d%%", pot_val);
  pico_ssd1306::drawText(&display, font_8x8, buf, 0, 32);

  // Gauge Bar
  // Original: x=0, y=48, w=128, h=10
  // Harbys: x0, y0, x1, y1
  // x1 = x0 + w - 1 = 0 + 128 - 1 = 127
  // y1 = y0 + h - 1 = 48 + 10 - 1 = 57
  pico_ssd1306::drawRect(&display, 0, 48, 127, 57);

  // Draw Fill
  if (pot_val > 100)
    pot_val = 100;

  uint8_t fill_w = (pot_val * 128) / 100;
  if (fill_w > 0) {
    // fill_w is width. x1 = 0 + fill_w - 1
    // Clip x1 to 127
    uint8_t x1 = fill_w - 1;
    if (x1 > 127)
      x1 = 127;
    pico_ssd1306::fillRect(&display, 0, 48, x1, 57);
  }

  display.sendBuffer();
}
