#!/bin/bash

if [ "$EUID" -ne 0 ]
  then echo "Please run as root (sudo)"
  exit
fi

if [ -z "$1" ]
  then
    echo "Usage: ./setup_can_interface.sh [bitrate]"
    echo "Example: ./setup_can_interface.sh 500000"
    exit 1
fi

BITRATE=$1

echo "Setting up CAN interface with bitrate $BITRATE..."

# Create systemd service file
cat > /etc/systemd/system/socketcand.service <<EOL
[Unit]
Description=Socketcand Daemon
After=network.target

[Service]
ExecStartPre=/sbin/ip link set can0 type can bitrate $BITRATE
ExecStartPre=/sbin/ip link set can0 up
ExecStart=/usr/local/bin/socketcand -i can0 -l 0.0.0.0
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOL

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling and starting socketcand service..."
systemctl enable socketcand
systemctl start socketcand

echo "Service started. status:"
systemctl status socketcand --no-pager
