# Pico flex-fuel sensor node

## Overview

Firmware for a Raspberry Pi **Pico 2 (RP2350)** that reads a GM/Continental
flex-fuel sensor, converts its output to ethanol content and fuel temperature,
shows them on an SSD1306 OLED and broadcasts them on the powertrain CAN through
an MCP2515.

The frame specification, the sensor characteristic and the plausibility rules
come from [`docs/05_flexfuel_design.md`](../docs/05_flexfuel_design.md) section 2.

| | |
|---|---|
| Sensor | GM/Continental 13577379 (3/8" barbs) or 13577394, open collector square wave |
| Ethanol | 50 Hz = 0 %, 150 Hz = 100 % (`E% = f - 50`) |
| Fuel temperature | low pulse 1 ms = -40 C, 5 ms = 125 C (`T = 41.5 * low_ms - 81.25`) |
| CAN | 500 kbit/s, identifier **0x0EC**, DLC 8, 10 Hz |
| Measurement | both-edge IRQ capture, 4-sample ring average, 0.25 ms debounce, 250 ms refresh |
| Plausibility | 45-155 Hz valid, 155-200 Hz contaminated, otherwise or no edge for 500 ms fault |

### CAN frame (Zeitronix ECA-2 CAN compatible in bytes 0, 1 and 7)

| Byte | Content |
|---|---|
| 0 | ethanol content 0-100 %, averaged |
| 1 | fuel temperature in C + 40 |
| 2 | raw (un-averaged) frequency / 2, in Hz |
| 3 | rolling counter 0-255 |
| 4-5 | reserved, 0 |
| 6 | firmware version (`FF_FW_VERSION`) |
| 7 | status: 0 OK, 1 sensor fault, 2 contaminated fuel, 3 not ready |

Behaviour when there is no valid measurement, deliberately chosen so that a
consumer which ignores byte 7 never leans out:

* **not ready** (boot, or fewer than 4 clean periods so far): bytes 0, 1, 2 = 0,
  byte 7 = 3.
* **fault** (implausible frequency, or no edge for 500 ms): the **last plausible**
  ethanol content and temperature are repeated in bytes 0 and 1, byte 2 = 0,
  byte 7 = 1. If there never was a plausible reading, bytes 0 and 1 are 0.
* **contaminated**: the measured values are sent (ethanol clamped to 100 %) with
  byte 7 = 2. The ECU is expected to hold its filtered value in this state.

Decode captured frames with
[`tools/ethanol_frame_decode.py`](../tools/ethanol_frame_decode.py); the same
layout is in [`data/ethanol_node.dbc`](../data/ethanol_node.dbc) for SavvyCAN,
cantools and friends.

## Source layout

| File | Role |
|---|---|
| `src/ff_sensor.c` / `.h` | **all** measurement, plausibility, state and frame encoding. Pure C11, no SDK headers, no floating point, so it builds unchanged for the host tests. |
| `src/sensor_input.cpp` | RP2350 both-edge GPIO interrupt, timestamps into a lock-free ring |
| `src/can_hw.cpp` | MCP2515 over SPI0, 500 kbit/s |
| `src/display.cpp` | SSD1306 status page |
| `src/main.cpp` | scheduling: drain edges, update state, transmit 10 Hz, refresh display 4 Hz |
| `test/test_ff_sensor.c` | host tests of `ff_sensor.c` |

## Hardware connection

**IMPORTANT**: the "GPxx" numbers are the **GPIO number used in software**, not
the physical pin number. Use the **Physical Pin** column for wiring.

| Component | Function | GPIO (Code) | Physical Pin (Board) |
| :--- | :--- | :--- | :--- |
| **Flex-fuel sensor** | **Signal (via pull-up + level shift)** | **GP15** | **Pin 20** |
| | Sensor supply | - | 12 V (ignition switched) |
| | Sensor ground | - | vehicle ground, shared with the Pico GND |
| **SSD1306 OLED** | **SDA** | **GP4** (I2C0 SDA) | **Pin 6** |
| | **SCL** | **GP5** (I2C0 SCL) | **Pin 7** |
| | VCC | - | Pin 36 (3V3 Out) |
| | GND | - | Pin 3 (or any GND) |
| **MCP2515 CAN** | **RX / MISO** | **GP16** (SPI0 RX) | **Pin 21** |
| | **CS** | **GP17** (SPI0 CSn)| **Pin 22** |
| | **SCK** | **GP18** (SPI0 SCK)| **Pin 24** |
| | **TX / MOSI** | **GP19** (SPI0 TX) | **Pin 25** |
| | **INT** (unused) | **GP20** | **Pin 26** |
| | VCC | - | 5V Source (req. for TJA1050) |
| | GND | - | Pin 23 (or any GND) |

The sensor pin is set to input with **no internal pull** and input hysteresis
(Schmitt trigger) enabled, so the external network below defines the levels.
Change the pin with `-DFF_SENSOR_GPIO=n` at configure time.

### Flex-fuel sensor: pull-up and level shifting

The sensor's signal output is an **open collector**: it can only pull the line
down, so it needs an external pull-up, and the pull-up value matters.

* **Pull-up: 2.2 kOhm to 3.5 kOhm to +5 V.** 1 kOhm loads the output too hard
  and 10 kOhm gives a rounded, unreliable edge; both are widely reported to
  fail with these sensors. 2.7 kOhm or 3.3 kOhm are good choices.
* The sensor is powered from **12 V** (ignition switched) and needs a solid
  ground shared with the Pico. Take the ground from the sensor connector, not
  from the chassis next to the Pico, or the pulse width (the temperature
  reading) drifts.
* The signal now swings **0 V to 5 V**, which must not reach the Pico's 3.3 V
  input. Two ways to bring it down:
  1. **Resistor divider** (simplest, non-inverting): signal -> 10 kOhm -> node
     -> 18 kOhm -> GND, with the node going to GP15. That gives 5 V * 18/28 =
     3.2 V. Keep the divider close to the Pico. Add a 100 nF capacitor from the
     node to GND only if the signal is noisy; it slows the edges, so leave it
     out unless needed.
  2. **The same 4-channel logic level converter** used for the MCP2515: HV side
     to the pull-up node, LV side to GP15. Note that the cheap MOSFET-based
     I2C converters are bidirectional and slow-ish; at 200 Hz that is irrelevant.
* Do **not** enable an internal pull-up on GP15 - it would fight the divider.
  `sensor_input_init()` explicitly disables the pulls.
* If your input stage **inverts** the signal (some buffer/opto arrangements do),
  set `g_cfg.invert_input = true` in `main.cpp`; the low-pulse measurement then
  still measures the sensor's low time. There is a host test for this path.
* **Mount the sensor in the fuel FEED line.** Return-line mounting traps air
  and produces spurious readings (see the design document).

```
      +5 V
        |
       [2.7k]        pull-up, 2.2k..3.5k
        |
sensor -+---[10k]---+--- GP15 (pin 20)
signal              |
                  [18k]
                    |
                   GND  (shared with the sensor ground and the Pico GND)
```

### MCP2515 CAN module and logic level shifter

Most MCP2515 modules operate at 5 V. You **MUST** use a logic level shifter to
protect the Pico 2 (3.3 V). A "4-channel I2C logic level converter" is fine.

**Level shifter power:**
- **HV (high voltage)**: connect to **5V source**
- **LV (low voltage)**: connect to **Pico Pin 36 (3V3 Out)**
- **GND**: connect to **GND** (both sides share ground)

**Data lines wiring:**

| Pico 2 (3.3V Side) | Shifter LV | Shifter HV | CAN Module (5V Side) |
| :--- | :--- | :--- | :--- |
| **Pin 21** (GP16/RX) | **LV1** | **HV1** | **SO** (MISO) |
| **Pin 22** (GP17/CS) | **LV2** | **HV2** | **CS** |
| **Pin 24** (GP18/SCK)| **LV3** | **HV3** | **SCK** |
| **Pin 25** (GP19/TX) | **LV4** | **HV4** | **SI** (MOSI) |
| **Pin 26** (GP20/INT)*| - | - | INT (unused) |

The firmware assumes an **8 MHz** crystal on the MCP2515 module. If yours has
16 MHz, configure with `-DCAN_XTAL_MHZ=16` (or edit `src/can_hw.h`), otherwise
the bit rate will be wrong by a factor of two and nothing will acknowledge.

---

## Host tests (no Pico, no cross toolchain)

All of the measurement, plausibility and frame logic lives in `src/ff_sensor.c`
and is tested on the host. One command:

```bash
cd pico_can_sender/test
./run_tests.sh
```

or, spelled out (this is exactly what the script runs):

```bash
cd pico_can_sender/test && mkdir -p build && \
cc -std=c11 -Wall -Wextra -Wpedantic -O2 -I../src \
   test_ff_sensor.c ../src/ff_sensor.c -o build/test_ff_sensor && \
./build/test_ff_sensor
```

It covers the design's status table (40, 49, 50, 100, 150, 156, 185 Hz and no
signal), the window boundaries, the temperature characteristic, the debounce,
the dropout/hold/recovery path and the byte-for-byte frame encoding. Exit code
0 means every check passed. Once the project has been configured, `cmake --build
build --target ff_tests` runs the same thing.

---

## Development environment setup

### macOS

No `sudo` is needed anywhere below.

1. **CMake and Ninja** via Homebrew:

   ```bash
   brew install cmake ninja
   ```

2. **Arm GNU toolchain.** `brew install --cask gcc-arm-embedded` works but its
   `.pkg` installer needs an administrator password. The no-sudo route is to
   drop the official tarball where the Raspberry Pi Pico VS Code extension also
   puts it, so both the command line and the extension find it:

   ```bash
   # pick the tarball for your Mac: darwin-arm64 (Apple silicon) or darwin-x86_64
   ARCH=arm64   # or x86_64
   TB=arm-gnu-toolchain-14.2.rel1-darwin-${ARCH}-arm-none-eabi
   curl -fLO "https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/${TB}.tar.xz"
   mkdir -p ~/.pico-sdk/toolchain
   tar -xJf "${TB}.tar.xz" -C ~/.pico-sdk/toolchain
   mv ~/.pico-sdk/toolchain/${TB} ~/.pico-sdk/toolchain/14_2_Rel1
   xattr -dr com.apple.quarantine ~/.pico-sdk/toolchain/14_2_Rel1   # Gatekeeper
   ~/.pico-sdk/toolchain/14_2_Rel1/bin/arm-none-eabi-gcc --version
   ```

   `14_2_Rel1` is the version named in `CMakeLists.txt` (`toolchainVersion`) and
   in `.vscode/settings.json`.

3. **Pico SDK 2.2.0** (the `sdkVersion` in `CMakeLists.txt`) into the
   repository's gitignored `work/` directory:

   ```bash
   cd <repo root>
   git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git work/pico-sdk
   git -C work/pico-sdk submodule update --init --depth 1 lib/tinyusb   # needed by stdio over USB
   ```

   Any other location works too; it is only passed through `PICO_SDK_PATH`.
   `work/` is in the repository `.gitignore`, so the SDK is never committed.

4. **Configure and build**:

   ```bash
   cd <repo root>/pico_can_sender
   export PICO_SDK_PATH="$(cd .. && pwd)/work/pico-sdk"
   export PICO_TOOLCHAIN_PATH="$HOME/.pico-sdk/toolchain/14_2_Rel1"
   export PATH="$PICO_TOOLCHAIN_PATH/bin:$PATH"

   cmake -G Ninja -S . -B build \
         -DPICO_BOARD=pico2 \
         -DPICO_SDK_PATH="$PICO_SDK_PATH" \
         -DPICOTOOL_FETCH_FROM_GIT_PATH="$(cd .. && pwd)/work/picotool"
   cmake --build build
   ```

   The first configure downloads and builds `picotool` (into `work/picotool`, so
   it is reused and not committed). The result is
   `build/pico_can_sender.uf2`.

   `PICOTOOL_FETCH_FROM_GIT_PATH` is optional; without it picotool is built
   inside `build/` and re-downloaded whenever you delete `build/`.

5. **Vendored libraries.** `lib/pico-mcp2515` and `lib/harbys-ssd1306` are
   recorded as gitlinks. If they are empty after a clone:

   ```bash
   cd pico_can_sender/lib
   git clone https://github.com/adamczykpiotr/pico-mcp2515.git pico-mcp2515
   git -C pico-mcp2515 checkout 4506dd0659b37b35eaf411de2d93669da471f6c4
   git clone https://github.com/Harbys/pico-ssd1306.git harbys-ssd1306
   git -C harbys-ssd1306 checkout 2cec467a06bb8cd89363e12091bfd2318f7feab6
   ```

   Both build unmodified against SDK 2.2.0 for the RP2350; no patch is needed.

### Windows

The easiest way is the official **Raspberry Pi Pico VS Code extension** or the
standalone installer.

1. Download the **Raspberry Pi Pico Setup for Windows** (standalone installer)
   from standard sources (GitHub: `raspberrypi/pico-setup-windows`).
2. Run the installer. This will install:
   - Arm GNU Toolchain
   - CMake
   - Ninja
   - Python 3
   - Visual Studio Code (optional)
3. Restart your computer if prompted.

#### VS Code setup (recommended, both platforms)

1. Open **VS Code**.
2. Install the **"Raspberry Pi Pico"** extension (by Raspberry Pi).
3. Open this project folder (`pico_can_sender`) in VS Code.
4. The extension should detect the project. Click "Switch SDK" or "Yes" if asked
   to configure the project.

---

## Build instructions

### Option A: using the VS Code extension

1. Click the **"Pico"** icon in the sidebar (or look at the bottom status bar).
2. Ensure the board is set to **Pico 2** (or `rp2350`).
3. Click **Compile Project** (Build).
4. The output `.uf2` file will be generated in `build/`.

### Option B: command line, Windows (build for Pico 2)

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

### Option C: command line, macOS / Linux

See step 4 of the macOS setup above. In short:

```bash
export PICO_SDK_PATH=<repo root>/work/pico-sdk
export PICO_TOOLCHAIN_PATH=$HOME/.pico-sdk/toolchain/14_2_Rel1
export PATH="$PICO_TOOLCHAIN_PATH/bin:$PATH"
cmake -G Ninja -S . -B build -DPICO_BOARD=pico2 -DPICO_SDK_PATH="$PICO_SDK_PATH"
cmake --build build
```

---

## Flashing the firmware

1. **Unplug** your Pico 2 from USB.
2. **Hold down** the `BOOTSEL` button (the small white button on the Pico).
3. **Plug in** the Pico 2 to your computer via USB while continuing to hold the
   button.
4. **Release** the button once the drive `RP2350` (or `RPI-RP2`) appears.
5. **Drag and drop** `build/pico_can_sender.uf2` onto the drive
   (macOS: `cp build/pico_can_sender.uf2 /Volumes/RP2350/`).
6. The Pico disconnects and restarts running the new program.

## Serial diagnostics

`stdio` goes to the USB CDC port. Once a second the node prints

```
E=50% T=2C f=100.000 Hz low=2000 us status=OK can=ok txerr=0 ovf=0
```

`txerr` counts MCP2515 transmit failures (nothing acknowledging on the bus),
`ovf` counts edges dropped because the capture ring overflowed - both should
stay at 0. On macOS: `screen /dev/tty.usbmodem* 115200` (leave with
`Ctrl-A`, `k`, `y`).

## Bench test with a signal generator

The node can be fully exercised without a sensor: feed GP15 a 0-3.3 V square
wave through the same divider and step it through 40, 49, 50, 100, 150, 156 and
185 Hz, plus signal off. The expected status and byte values for exactly those
points are asserted in `test/test_ff_sensor.c`, so the bench run only has to
confirm that the capture path agrees with the logic. See the test plan posted on
issue #29.
