# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.2.0)
set(toolchainVersion 14_2_Rel1)
set(picotoolVersion 2.2.0-a4)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
set(PICO_BOARD pico2 CACHE STRING "Board type")

cmake_minimum_required(VERSION 3.13)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(pico_sdk_import.cmake)

project(pico_can_sender C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Initialize the SDK
pico_sdk_init()

# -------------------------------------------------------------
# Library: pico-mcp2515 (CAN)
# -------------------------------------------------------------
add_library(pico_mcp2515
    lib/pico-mcp2515/include/mcp2515/mcp2515.cpp
)
target_include_directories(pico_mcp2515 PUBLIC lib/pico-mcp2515/include)
target_link_libraries(pico_mcp2515 pico_stdlib hardware_spi)

# -------------------------------------------------------------
# Library: Harbys/pico-ssd1306 (OLED)
# -------------------------------------------------------------
add_subdirectory(lib/harbys-ssd1306)

# -------------------------------------------------------------
# Executable
# -------------------------------------------------------------
add_executable(pico_can_sender
    src/main.cpp
    src/pot.cpp
    src/can_hw.cpp
    src/display.cpp
)

# -------------------------------------------------------------
# Include Directories
# -------------------------------------------------------------
target_include_directories(pico_can_sender PRIVATE
    src
)

# -------------------------------------------------------------
# Link Libraries
# -------------------------------------------------------------
target_link_libraries(pico_can_sender
    pico_stdlib
    hardware_adc
    hardware_spi
    hardware_i2c
    pico_mcp2515   
    pico_ssd1306   
)

# -------------------------------------------------------------
# Output Setup
# -------------------------------------------------------------
pico_enable_stdio_usb(pico_can_sender 1)
pico_enable_stdio_uart(pico_can_sender 0)
pico_add_extra_outputs(pico_can_sender)
