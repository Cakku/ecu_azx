#include "display.h"
#include "hardware/i2c.h"
#include "pico/stdlib.h"


// Include likely path for default ssd1306 library
// If this fails, we will adjust.
#include "ssd1306.h"

// I2C Pin definitions
#define DISP_I2C_PORT i2c0
#define DISP_SDA_PIN 4
#define DISP_SCL_PIN 5
#define DISP_ADDR 0x3C
#define DISP_WIDTH 128
#define DISP_HEIGHT 64

// Create the display object
// Note: Depending on the specific library header, the namespace might be
// different. daschr/pico-ssd1306 uses `pico_ssd1306` namespace typically.
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
  display.setOrientation(0); // landscape
                             // display.invertDisplay(); // Optional
}

void display_update(uint8_t pot_val, bool can_status) {
  display.clear();

  // Header
  display.drawText(0, 0, "CAN Sender");

  // Status
  if (can_status) {
    display.drawText(80, 0, "OK");
  } else {
    display.drawText(80, 0, "ERR");
  }

  // CAN ID Info
  display.drawText(0, 16, "ID: 0x123");

  // Value Text
  char buf[16];
  sprintf(buf, "Val: %d%%", pot_val);
  display.drawText(0, 32, buf);

  // Gauge Bar
  // Draw frame
  display.drawRect(0, 48, 128, 10); // x, y, w, h

  // Draw Fill
  if (pot_val > 100)
    pot_val = 100;
  uint8_t fill_w = (pot_val * 128) / 100;
  if (fill_w > 0) {
    display.fillRect(0, 48, fill_w, 10);
  }

  display.sendBuffer();
}
