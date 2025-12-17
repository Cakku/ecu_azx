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
set(PICO_BOARD pico CACHE STRING "Board type")

cmake_minimum_required(VERSION 3.13)

include(pico_sdk_import.cmake)

project(pico_can_sender C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Initialize the SDK
pico_sdk_init()

include(FetchContent)

# -------------------------------------------------------------
# Library: pico-mcp2515 (CAN)
# -------------------------------------------------------------
FetchContent_Declare(
    pico_mcp2515
    GIT_REPOSITORY https://github.com/adamczykpiotr/pico-mcp2515.git
    GIT_TAG        master
)
FetchContent_MakeAvailable(pico_mcp2515)

# -------------------------------------------------------------
# Library: pico-ssd1306 (OLED)
# -------------------------------------------------------------
FetchContent_Declare(
    pico_ssd1306
    GIT_REPOSITORY https://github.com/daschr/pico-ssd1306.git
    GIT_TAG        master
)
FetchContent_MakeAvailable(pico_ssd1306)

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
    ${pico_mcp2515_SOURCE_DIR} 
    ${pico_ssd1306_SOURCE_DIR}
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
