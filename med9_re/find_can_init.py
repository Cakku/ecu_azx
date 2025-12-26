# Search for writes to CAN Control Registers
# MPC5xx TouCAN Module Base usually: 0x30C000 (CAN A), 0x30C800 (CAN B), 0x30D000 (CAN C)
# or 0xFFF000 relative etc.

# We look for "lis rX, 0x30C0" (or similar) followed by "stb/sth/stw rY, offset(rX)"

import struct
from capstone import *

BINARY_PATH = "../passat_azx_ori.bin"
FLASH_BASE = 0x00000000

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

cs = Cs(CS_ARCH_PPC, CS_MODE_32 + CS_MODE_BIG_ENDIAN)
cs.detail = True

print("Searching for CAN Configuration sequences...")

# Heuristic: Scan for instructions loading 0x30C0, 0x30C8, 0x30D0
# (Common MPC555/565 CAN bases, usually mapped there in Bosch ECUs)

patterns = [0x30C0, 0x30C8, 0x30D0, 0xC0C0, 0xC0C8]

for i in range(0, len(data), 4):
    word = struct.unpack('>I', data[i:i+4])[0]
    
    # Check for 'lis rX, value'
    if (word >> 26) == 15: # addis rD, rA, SIMM
        rA = (word >> 16) & 0x1F
        if rA == 0: # treat as 'lis'
            imm = word & 0xFFFF
            if imm in patterns:
                rD = (word >> 21) & 0x1F
                print(f"Found CAN Base Load at {hex(i)}: lis r{rD}, {hex(imm)}")
                
                # Disassemble next few instructions to see usage
                try:
                    code_chunk = data[i:i+40] # 10 instructions
                    print("Context:")
                    for inst in cs.disasm(code_chunk, i):
                        print(f"  {hex(inst.address)}: {inst.mnemonic} {inst.op_str}")
                except:
                    pass
