#!/bin/sh
# Build and run the host tests for the flex-fuel measurement/state logic.
# Usage:  ./run_tests.sh          (from pico_can_sender/test)
# Needs nothing but a C compiler: the Apple clang shipped with the Xcode command
# line tools is enough, no Pico SDK and no cross toolchain.
set -e
cd "$(dirname "$0")"
mkdir -p build
${CC:-cc} -std=c11 -Wall -Wextra -Wpedantic -O2 -I../src \
    test_ff_sensor.c ../src/ff_sensor.c -o build/test_ff_sensor
exec ./build/test_ff_sensor
