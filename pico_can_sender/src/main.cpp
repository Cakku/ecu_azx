#include "pico/stdlib.h"
#include <stdio.h>

// Include our modules
// Since we are compiling as C++, these headers should be safe provided
// corresponding implementations are C++ or extern C
#include "can_hw.h"
#include "display.h"
#include "pot.h"

int main() {
  stdio_init_all();

  // LED Init - Do this FIRST to verify power/code running
  const uint LED_PIN = PICO_DEFAULT_LED_PIN;
  gpio_init(LED_PIN);
  gpio_set_dir(LED_PIN, GPIO_OUT);

  // Start: LED is initialized (OFF by default or explicitly set OFF)
  gpio_put(LED_PIN, 0);
  sleep_ms(1000); // Visual delay before starting

  // 1. Pot Init
  printf("Initializing Pot...\n");
  gpio_put(LED_PIN, 1); // ON = Busy
  pot_init();
  gpio_put(LED_PIN, 0); // OFF = Done
  sleep_ms(500);

  // 2. Display Init
  printf("Initializing Display...\n");
  gpio_put(LED_PIN, 1); // ON = Busy
  display_init();       // I2C and OLED
  gpio_put(LED_PIN, 0); // OFF = Done
  sleep_ms(500);

  // 3. CAN Init
  printf("Initializing CAN...\n");
  gpio_put(LED_PIN, 1); // ON = Busy
  can_hw_init();        // SPI and MCP2515
  gpio_put(LED_PIN, 0); // OFF = Done
  sleep_ms(500);

  printf("Initialization Complete.\n");

  while (true) {
    // Heartbeat
    gpio_put(LED_PIN, 1);

    // 1. Read Pot
    uint8_t pot_pct = pot_read_percentage();

    // 2. Send CAN
    can_hw_send_value(pot_pct);

    // 3. Update Display
    bool can_status = can_hw_is_connected();
    display_update(pot_pct, can_status);

    // 4. Debug Output
    // printf("Pot: %d%%, CAN: %s\n", pot_pct, can_status ? "OK" : "ERR");

    // 5. Rate limit (10Hz)
    gpio_put(LED_PIN, 0); // Blink off
    sleep_ms(100);
  }

  return 0;
}
