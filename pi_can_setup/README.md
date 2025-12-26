# Raspberry Pi Zero W CAN Bus Sniffer Setup

This guide allows you to turn a Raspberry Pi Zero W (or any Pi) into a wireless CAN Bus interface compatible with **SavvyCAN**.

## 0. Initial Setup (Prerequisites)

**The Raspberry Pi Zero W does NOT have internal storage.** You MUST install an Operating System on the microSD card.

1.  **Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)** on your computer.
2.  Insert your microSD card into your computer.
3.  Open Raspberry Pi Imager:
    *   **Device**: Choose "Raspberry Pi Zero".
    *   **OS**: Choose **Raspberry Pi OS (other)** -> **Raspberry Pi OS Lite (32-bit)**.
        *   *Note: The "Lite" version is best as it has no desktop GUI, which makes it faster and more reliable for this headless application.*
    *   **Storage**: Select your SD card.
4.  **Important - Enable SSH & WiFi**:
    *   Don't click "Next" yet! Press **Ctrl+Shift+X** (or click the gear icon/Next settings) to open customization.
    *   **Enable SSH**: Select "Use password authentication". Set a username (e.g., `pi`) and password.
    *   **Configure Wireless LAN**: Enter your WiFi SSID and Password.
5.  Write the OS to the card.
6.  Insert the card into the Pi Zero W and power it on. It will connect to your WiFi automatically.

## 1. Hardware Connection (Wiring)

Connect the MCP2515 CAN Bus module to the Raspberry Pi Zero W's GPIO header.
**Note**: The MCP2515 usually requires 5V input, but its logic lines are often 5V. The Pi Zero is 3.3V logic. 
*   **Ideally**: Use a logic level shifter between the Pi and MCP2515 (as you did with the Pico).
*   **Or**: Many MCP2515 modules work fine with 3.3V logic on SI/SO/SCK if powered by 3.3V (but check your specific module specs, reliability may vary). SAFEST is 3.3V power if the module supports it, or level shifter.

| MCP2515 Pin | Pi Zero W Pin | Function |
| :--- | :--- | :--- |
| **VCC** | **Pin 1** (3.3V) or **Pin 2** (5V)* | Power *Check module voltage reqs* |
| **GND** | **Pin 6** (GND) | Ground |
| **CS** | **Pin 24** (GPIO 8 / CE0) | Chip Select |
| **SO** (MISO)| **Pin 21** (GPIO 9) | SPI Data Out (from MCP) |
| **SI** (MOSI)| **Pin 19** (GPIO 10) | SPI Data In (to MCP) |
| **SCK** | **Pin 23** (GPIO 11) | SPI Clock |
| **INT** | **Pin 22** (GPIO 25) | Interrupt |

**Warning**: If powering MCP2515 with 5V, ensure the SO (MISO) line going *into* the Pi does not exceed 3.3V, or you risk damaging the Pi.

## 2. Enable SPI & Overlay

You must edit the boot configuration to load the drivers for the MCP2515.

1.  SSH into your Raspberry Pi.
2.  Edit the config file: `sudo nano /boot/config.txt`
3.  Add the following lines at the end of the file:

    ```ini
    dtparam=spi=on
    dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25
    dtparam=interrupt=25
    ```

    *   `oscillator=8000000`: Match this to the crystal on your MCP2515 board (usually 8MHz or 16MHz). Check the silver rectangle component on your board. If it says "8.000", use 8000000. If "16.000", use 16000000.
    *   `interrupt=25`: This matches the wiring above (Pin 22 / GPIO 25).

4.  Reboot the Pi: `sudo reboot`

## 3. Verify Hardware

After rebooting, check if the CAN interface exists:

```bash
ls /sys/bus/spi/devices/spi0.0/net
# Should show "can0"
```

Or run `ip link show can0`.

## 4. Install Software

We have provided two scripts to automate the software setup.

1.  **Install socketcand**:
    ```bash
    chmod +x install_socketcand.sh
    ./install_socketcand.sh
    ```

2.  **Configure Interface & Auto-Start**:
    ```bash
    chmod +x setup_can_interface.sh
    # Usage: ./setup_can_interface.sh [bitrate]
    # Example for 500kbps:
    sudo ./setup_can_interface.sh 500000
    ```

    This script creates a service that automatically brings up `can0` and starts `socketcand` on boot.

## 5. Connect with SavvyCAN

1.  Open **SavvyCAN** on your PC.
2.  Go to **Connection** -> **Open Connection Window**.
3.  Click **Add New Device Connection**.
4.  Select **Network Connection**.
5.  **IP Address**: The IP of your Raspberry Pi.
6.  **Port**: `29536` (Default for socketcand).
7.  Click **Create New Connection**.

You should now see raw CAN frames appearing in SavvyCAN!
