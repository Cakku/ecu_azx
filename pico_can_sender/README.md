# Pico CAN Sender

## Overview
This project reads a potentiometer value (connected to ADC0 / GP26), displays it on an SSD1306 OLED (I2C0), and sends it via CAN Bus using an MCP2515 module (SPI0).

## Hardware Connection
Based on your `pico2_pinout.txt` and project requirements:

### Potentiometer
- **VCC**: 3.3V
- **GND**: GND
- **Wiper**: GP26 (ADC0)

### SSD1306 OLED (I2C)
- **VCC**: 3.3V
- **GND**: GND
- **SDA**: GP4 (I2C0 SDA)
- **SCL**: GP5 (I2C0 SCL)

### MCP2515 CAN Module (SPI)
- **VCC**: 3.3V (Ensure module supports 3.3V logic or use level shifter if 5V)
- **GND**: GND
- **CS**: GP17
- **SCK**: GP18
- **MOSI (SI)**: GP19
- **MISO (SO)**: GP16
- **INT**: GP20 (Optional, used for connection check logic)

## Build Instructions
1.  Ensure you have the Raspberry Pi Pico SDK installed and the environment variables set (PICO_SDK_PATH).
2.  Create a build directory:
    ```bash
    mkdir build
    cd build
    ```
3.  Configure with CMake:
    ```bash
    cmake ..
    ```
4.  Build:
    ```bash
    make
    # or
    ninja
    ```
5.  Flash the `pico_can_sender.uf2` to your device.
