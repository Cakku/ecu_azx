import struct

BINARY_PATH = "passat_azx_flash.bin"
START_RANGE = 0x2bc34
END_RANGE = 0x2bc60

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

print(f"Searching for pointers in range {hex(START_RANGE)} - {hex(END_RANGE)}...")

for i in range(0, len(data), 4):
    word = struct.unpack('>I', data[i:i+4])[0]
    
    # Check if word is a pointer into our range
    if START_RANGE <= word < END_RANGE:
        # Filter out self-references or nearby code? 
        # But data pointers usually are in data sections.
        print(f"Found Pointer to {hex(word)} at File Offset 0x{i:x}")
