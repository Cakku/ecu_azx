#!/bin/bash

# Simple wrapper to check if any traffic is visible on can0
if ! ip link show can0 | grep -q "UP"; then
    echo "Error: can0 interface is not UP. Run ./setup_can_interface.sh first."
    exit 1
fi

echo "Listening for traffic on can0... (Press Ctrl+C to stop)"
candump can0
