# Pico CAN Sender

## Overview
This project reads a potentiometer value (connected to ADC0 / GP26), displays it on an SSD1306 OLED (I2C0), and sends it via CAN Bus using an MCP2515 module (SPI0).

## Hardware Connection
**IMPORTANT**: The "GPxx" numbers refer to the **GPIO Pin Number** used in software, not the physical pin number on the board. Please use the **Physical Pin** column for wiring.

| Component | Function | GPIO (Code) | Physical Pin (Board) |
| :--- | :--- | :--- | :--- |
| **Potentiometer** | **Wiper/Signal** | **GP26** (ADC0) | **Pin 31** |
| | VCC | - | Pin 36 (3V3 Out) |
| | GND | - | Pin 38 (GND) |
| **SSD1306 OLED** | **SDA** | **GP4** (I2C0 SDA) | **Pin 6** |
| | **SCL** | **GP5** (I2C0 SCL) | **Pin 7** |
| | VCC | - | Pin 36 (3V3 Out) |
| | GND | - | Pin 3 (or any GND) |
| **MCP2515 CAN** | **RX / MISO** | **GP16** (SPI0 RX) | **Pin 21** |
| | **CS** | **GP17** (SPI0 CSn)| **Pin 22** |
| | **SCK** | **GP18** (SPI0 SCK)| **Pin 24** |
| | **TX / MOSI** | **GP19** (SPI0 TX) | **Pin 25** |
| | **INT** (Optional)| **GP20** | **Pin 26** |
| | VCC | - | 5V Source (req. for TJA1050) |
| | GND | - | Pin 23 (or any GND) |

### MCP2515 CAN Module & Logic Level Shifter
Most MCP2515 modules operate at 5V. You **MUST** use a Logic Level Shifter to protect the Pico 2 (3.3V).
Your "4-channel I2C Logic Level Converter" is perfect for this.

**Level Shifter Power:**
- **HV (High Voltage)**: Connect to **5V Source**
- **LV (Low Voltage)**: Connect to **Pico Pin 36 (3V3 Out)**
- **GND**: Connect to **GND** (Both sides usually share GND)

**Data Lines Wiring:**

| Pico 2 (3.3V Side) | Shifter LV | Shifter HV | CAN Module (5V Side) |
| :--- | :--- | :--- | :--- |
| **Pin 21** (GP16/RX) | **LV1** | **HV1** | **SO** (MISO) |
| **Pin 22** (GP17/CS) | **LV2** | **HV2** | **CS** |
| **Pin 24** (GP18/SCK)| **LV3** | **HV3** | **SCK** |
| **Pin 25** (GP19/TX) | **LV4** | **HV4** | **SI** (MOSI) |
| **Pin 26** (GP20/INT)*| - | - | INT (Optional) |

---

## 🛠️ Development Environment Setup

### 1. Install the Pico SDK Toolchain (Windows)
The easiest way is to use the official **Raspberry Pi Pico VS Code Extension** or the standalone installer.
1.  Download the **Raspberry Pi Pico Setup for Windows** (standalone installer) from standard sources (GitHub: `raspberrypi/pico-setup-windows`).
2.  Run the installer. This will install:
    - Arm GNU Toolchain
    - CMake
    - Ninja
    - Python 3
    - Visual Studio Code (optional)
3.  Restart your computer if prompted.

### 2. VS Code Setup (Recommended)
1.  Open **VS Code**.
2.  Install the **"Raspberry Pi Pico"** extension (by Raspberry Pi).
3.  Open this project folder (`pico_can_sender`) in VS Code.
4.  The extension should detect the project. Click "Switch SDK" or "Yes" if asked to configure the project.

---

## 🏗️ Build Instructions

### Option A: Using VS Code Extension
1.  Click the **"Pico"** icon in the sidebar (or look at the bottom status bar).
2.  Ensure the Board is set to **Pico 2** (or `rp2350`).
3.  Click **Compile Project** (Build).
4.  The output `.uf2` file will be generated in `build/`.

### Option B: Command Line (Build for Pico 2)

    # Config
    # If the build folder was created with a bad cache, you MUST remove it first:
    Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue

    # Configure (using explicit paths to avoid variable expansion issues)
    & "C:\Users\Cakku\.pico-sdk\cmake\v3.31.5\bin\cmake.exe" `
        -DPICO_SDK_PATH="C:/Users/Cakku/.pico-sdk/sdk/2.2.0" `
        -DCMAKE_MAKE_PROGRAM="C:\Users\Cakku\.pico-sdk\ninja\v1.12.1\ninja.exe" `
        -G "Ninja" -S . -B build

    # Build
    & "C:\Users\Cakku\.pico-sdk\cmake\v3.31.5\bin\cmake.exe" --build build

---

## ⚡ Flashing the Firmware

1.  **Unplug** your Pico 2 from USB.
2.  **Hold down** the `BOOTSEL` button (the small white button on the Pico).
3.  **Plug in** the Pico 2 to your computer via USB while continuing to hold the button.
4.  **Release** the button once the drive `RP2350` (or `RPI-RP2`) appears on your computer.
5.  **Drag and drop** the `pico_can_sender.uf2` file from your `build/` folder onto the drive.
6.  The Pico will automatically disconnect and restart running your new program.
