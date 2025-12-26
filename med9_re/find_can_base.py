import struct

# Opcode for 'lis' (addis rD, r0, SIMM) is 15 (001111)
# 15 << 26 = 0x3C000000
# We are looking for 3C ?? 30 ??

BINARY_PATH = "../passat_azx_ori.bin"
with open(BINARY_PATH, 'rb') as f:
    data = f.read()

print("Searching for CAN Peripheral Access (0x30xxxx)...")

for i in range(0, len(data), 4):
    word = struct.unpack('>I', data[i:i+4])[0]
    
    # Check for 'lis rX, 0x30C0' or similar (Touareg CAN A is often 0x30C000)
    # 0x3C is opcode for 'lis' (addis to r0)
    if (word & 0xFFFF0000) >> 16 != 0x3C60 and (word & 0xFFFF0000) >> 16 != 0x3C80 and (word & 0xFFFF0000) >> 16 != 0x3CA0:
        # Check generalized 'lis' opcode 0x3C
        # 0x3C000000 mask
        if (word & 0xFC000000) == 0x3C000000:
            imm = word & 0xFFFF
            # Valid CAN bases usually start with 30
            if (imm & 0xFF00) == 0x3000:
                print(f"Found potential CAN base access at 0x{i:x}: {hex(word)} (lis r{(word >> 21) & 0x1F}, {hex(imm)})")
