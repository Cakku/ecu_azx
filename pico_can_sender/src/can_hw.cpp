#include "can_hw.h"
#include "mcp2515.h" // Verify this path during build
#include "pico/stdlib.h"

// Pin definitions
#define CAN_SPI_PORT spi0
#define CAN_CS_PIN 17
#define CAN_MISO_PIN 16
#define CAN_MOSI_PIN 19
#define CAN_SCK_PIN 18
#define CAN_INT_PIN 20 // Not strictly used for sending, but good to define

MCP2515 can0(CAN_SPI_PORT, CAN_CS_PIN, CAN_MISO_PIN, CAN_MOSI_PIN, CAN_SCK_PIN,
             10000000);

static bool connected = false;

void can_hw_init(void) {
  // Initialize MCP2515
  // Library handles SPI init internally usually, or we might need to do it?
  // The library constructor takes SPI port, so it likely uses it.
  // We should ensure standard initialization.

  can0.reset();

  // Set bitrate to 500kbps (common standard) & 8MHz oscillator (standard for
  // modules) Adjust MCP_8MHZ if your crystal is 16MHz
  if (can0.setBitrate(CAN_500KBPS, MCP_8MHZ) == MCP2515::ERROR_OK) {
    connected = true;
  } else {
    connected = false;
  }

  // Set to Normal mode to send messages
  can0.setNormalMode();
}

void can_hw_send_value(uint8_t value) {
  if (!connected)
    return;

  struct can_frame frame;
  frame.can_id = 0x123; // Send ID
  frame.can_dlc = 1;    // Data Length
  frame.data[0] = value;

  can0.sendMessage(&frame);
}

bool can_hw_is_connected(void) {
  // Basic check: we could try to read a register or just return initialization
  // status
  return connected;
}
