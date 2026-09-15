import struct

BINARY_PATH = "passat_azx_flash.bin"

# Common VW/Audi CAN IDs (Standard 11-bit)
# We search for them as 32-bit (0x00000XXX) or Shifted (ID << 18 for some registers)
# MPC5xx TouCAN ID register format:
# STD_ID (11 bits) starts at bit 18? (Bit 0 is MSB in PPC documentation usually, but let's assume 'int' layout)
# 
# Usually in C-structs they are just 32-bit integers: 0x00000280
# Or 16-bit: 0x0280

IDS_TO_FIND = [
    0x280, # Motor 1
    0x288, # Motor 2
    0x380, # Trans
    0x480, # ABS?
    0x7DF, # OBD Func
    0x7E0, # OBD Phys
    0x571, # Battery? 
    0x575  # Key?
]

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

print("Searching for CAN IDs...")

for can_id in IDS_TO_FIND:
    # 1. Search for simple 32-bit Big Endian
    packed = struct.pack('>I', can_id)
    offset = 0
    while True:
        try:
            loc = data.index(packed, offset)
            # Filter low addresses (interrupt vectors) or highly unlikely code areas if possible
            if loc > 0x2000: 
                print(f"Found 0x{can_id:03x} (32-bit) at 0x{loc:x}")
            offset = loc + 1
        except ValueError:
            break

    # 2. Search for 16-bit Big Endian
    packed_16 = struct.pack('>H', can_id)
    offset = 0
    while True:
        try:
            loc = data.index(packed_16, offset)
            if loc > 0x2000:
                # To reduce noise, maybe check alignment or surrounding data? 
                # Pure 16-bit search might be too noisy.
                pass
                # print(f"Found 0x{can_id:03x} (16-bit) at 0x{loc:x}")
            offset = loc + 1
        except ValueError:
            break

    # 3. Search for Shifted ID ( TouCAN Message Buffer ID Register format )
    # On MPC5xx, ID is in bits 0-10 of the register (assuming standard ID).  
    # 0   ... 10 | 11 ... 
    # ID         |
    # Wait, PPC bit numbering: 0 is MSB.
    # Standard ID (11 bits) is at bits 18-28. 
    # 0000 0000 000X XXXX XXXX XX00 0000 0000
    # ID << 18 ?
    # Let's try checking specific register values (ID << 18)
    
    val_shifted = (can_id & 0x7FF) << 18
    packed_shifted = struct.pack('>I', val_shifted)
    offset = 0
    while True:
        try:
            loc = data.index(packed_shifted, offset)
            if loc > 0x2000:
                print(f"Found 0x{can_id:03x} (Shifted Reg Format 0x{val_shifted:08x}) at 0x{loc:x}")
            offset = loc + 1
        except ValueError:
            break
