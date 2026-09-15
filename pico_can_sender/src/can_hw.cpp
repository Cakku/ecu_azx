#include "can_hw.h"

#include "ff_sensor.h"
#include "mcp2515/mcp2515.h" /* lib/pico-mcp2515 */
#include "pico/stdlib.h"

/* Pin definitions - unchanged from the original potentiometer demo, see
 * README.md for the wiring table and the level shifter. */
#define CAN_SPI_PORT spi0
#define CAN_CS_PIN 17
#define CAN_MISO_PIN 16
#define CAN_MOSI_PIN 19
#define CAN_SCK_PIN 18
#define CAN_INT_PIN 20 /* not used for transmit only operation */

static MCP2515 can0(CAN_SPI_PORT, CAN_CS_PIN, CAN_MISO_PIN, CAN_MOSI_PIN,
                    CAN_SCK_PIN, 10000000);

static bool s_ready = false;
static uint16_t s_can_id = (uint16_t)FF_CAN_ID_DEFAULT;
static uint32_t s_tx_errors = 0;
static uint32_t s_tx_fail_streak = 0;

bool can_hw_init(uint16_t can_id)
{
    s_can_id = can_id & 0x7FFu;
    s_tx_errors = 0;
    s_tx_fail_streak = 0;
    s_ready = false;

    can0.reset();

#if CAN_XTAL_MHZ == 16
    const CAN_CLOCK clk = MCP_16MHZ;
#else
    const CAN_CLOCK clk = MCP_8MHZ;
#endif

    if (can0.setBitrate(CAN_500KBPS, clk) != MCP2515::ERROR_OK) {
        return false;
    }
    if (can0.setNormalMode() != MCP2515::ERROR_OK) {
        return false;
    }
    s_ready = true;
    return true;
}

void can_hw_set_id(uint16_t can_id) { s_can_id = can_id & 0x7FFu; }

uint16_t can_hw_get_id(void) { return s_can_id; }

bool can_hw_send_frame(const uint8_t data[8])
{
    if (!s_ready) {
        return false;
    }

    struct can_frame frame;
    frame.can_id = s_can_id;
    frame.can_dlc = FF_FRAME_DLC;
    for (unsigned i = 0; i < FF_FRAME_DLC; i++) {
        frame.data[i] = data[i];
    }

    if (can0.sendMessage(&frame) != MCP2515::ERROR_OK) {
        s_tx_errors++;
        s_tx_fail_streak++;
        return false;
    }
    s_tx_fail_streak = 0;
    return true;
}

bool can_hw_is_connected(void)
{
    /* Three failures in a row means nothing is acknowledging on the bus (no
     * other node, wrong bit rate, no termination). */
    return s_ready && s_tx_fail_streak < 3u;
}

uint32_t can_hw_tx_errors(void) { return s_tx_errors; }
