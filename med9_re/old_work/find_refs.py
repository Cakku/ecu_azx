import struct

BINARY_PATH = "passat_azx_flash.bin"
TARGET_ADDR = 0x2bc00 

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

print(f"Searching for references to {hex(TARGET_ADDR)}...")

# 1. Search for direct pointer (0x0002BE00)
packed = struct.pack('>I', TARGET_ADDR)
offset = 0
while True:
    try:
        loc = data.index(packed, offset)
        print(f"Found Pointer at 0x{loc:x}")
        offset = loc + 1
    except ValueError:
        break

# 2. Search for 'lis rX, 0x2' followed by 'addi rY, rX, 0xbe00'
# 0x2 = 0x0002.
# lis opcode: 15 (001111). 
# lis rD, SIMM -> addis rD, 0, SIMM
# 0011 11 RD R0 SIMM
# 3C RD 00 02

for i in range(0, len(data), 4):
    word = struct.unpack('>I', data[i:i+4])[0]
    
    # Check for lis rD, 0x2
    if (word & 0xFFE0FFFF) == 0x3C000002: # Mask out rD
        rD = (word >> 21) & 0x1F
        # Found 'lis rD, 2'. Now search forward for 'addi r?, rD, 0xbe00'
        # addi opcode: 14 (001110)
        # addi rT, rA, SIMM
        # 0011 10 RT RA SIMM
        # 38 RT RA be00
        
        # Scan next 20 instructions
        for j in range(1, 40): 
            if i + j*4 >= len(data): break
            next_word = struct.unpack('>I', data[i+j*4:i+j*4+4])[0]
            
            # Check common memory access/arithmetic with SIMM
            # addi(14), lwz(32), stw(36), lbz(34), stb(38), lhz(40), sth(44)
            # ori(24) handled separately
            op = next_word >> 26
            if op in [14, 32, 36, 34, 38, 40, 44]:
                rA = (next_word >> 16) & 0x1F
                if rA == rD:
                    imm = next_word & 0xFFFF
                    # logic for signed immediate
                    simm = imm if imm < 0x8000 else imm - 0x10000
                    calc_addr = (0x20000) + simm
                    if calc_addr == TARGET_ADDR:
                        op_names = {14:'addi', 32:'lwz', 36:'stw', 34:'lbz', 38:'stb', 40:'lhz', 44:'sth'}
                        print(f"Found Split Reference (Base 0x2 + {op_names.get(op)}) at 0x{i:x}: lis r{rD}, 2 ... {op_names.get(op)} r{(next_word>>21)&0x1F}, {hex(imm)}(r{rA})")

            # Check ori (24) -> Unsigned add
            if op == 24:
                rA = (next_word >> 16) & 0x1F
                if rA == rD: # rS in ORI is rA field
                    imm = next_word & 0xFFFF
                    calc_addr = (0x20000) | imm
                    if calc_addr == TARGET_ADDR:
                        print(f"Found Split Load (Base 0x2 + ori) at 0x{i:x}: lis r{rD}, 2 ... ori r{(next_word>>21)&0x1F}, r{rA}, {hex(imm)}")

    # Check for lis rD, 0x3 (For sign extension adjustment case)
    if (word & 0xFFE0FFFF) == 0x3C000003:
        rD = (word >> 21) & 0x1F
        for j in range(1, 40):
            if i + j*4 >= len(data): break
            next_word = struct.unpack('>I', data[i+j*4:i+j*4+4])[0]
            op = next_word >> 26
            if op in [14, 32, 36, 34, 38, 40, 44]:
                rA = (next_word >> 16) & 0x1F
                if rA == rD:
                    imm = next_word & 0xFFFF
                    simm = imm if imm < 0x8000 else imm - 0x10000
                    calc_addr = (0x30000) + simm
                    if calc_addr == TARGET_ADDR:
                         op_names = {14:'addi', 32:'lwz', 36:'stw', 34:'lbz', 38:'stb', 40:'lhz', 44:'sth'}
                         print(f"Found Split Reference (Base 0x3 + {op_names.get(op)}) at 0x{i:x}: lis r{rD}, 3 ... {op_names.get(op)} r{(next_word>>21)&0x1F}, {hex(imm)}(r{rA})")
