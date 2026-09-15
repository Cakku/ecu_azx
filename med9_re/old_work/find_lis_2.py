import struct

BINARY_PATH = "passat_azx_flash.bin"

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

print("Searching for 'lis rX, 2' (3C ?? 00 02)...")

count = 0
for i in range(0, len(data), 4):
    word = struct.unpack('>I', data[i:i+4])[0]
    
    # Check lis rD, 2
    # 3C RD 00 02
    if (word & 0xFFE0FFFF) == 0x3C000002:
        rD = (word >> 21) & 0x1F
        print(f"Found 'lis r{rD}, 2' at 0x{i:x}")
        count += 1

print(f"Total found: {count}")
