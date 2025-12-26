#!/bin/bash
set -e

echo "Updating apt..."
sudo apt-get update

echo "Installing dependencies..."
sudo apt-get install -y git cmake gcc make autoconf libconfig-dev can-utils

echo "Cloning socketcand..."
if [ -d "socketcand" ]; then
    rm -rf socketcand
fi
git clone https://github.com/linux-can/socketcand.git

echo "Building socketcand..."
cd socketcand
cmake .
make

echo "Installing socketcand..."
sudo make install

echo "Done! socketcand is installed to /usr/local/bin/socketcand"
