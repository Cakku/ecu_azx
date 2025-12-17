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
  sleep_ms(1000); // Give time for USB to attach if needed

  printf("Initializing...\n");

  // Initialize all subsystems
  pot_init();
  display_init(); // I2C and OLED
  can_hw_init();  // SPI and MCP2515

  printf("Initialization Complete.\n");

  while (true) {
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
    sleep_ms(100);
  }

  return 0;
}
