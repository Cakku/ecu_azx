import struct
# Try to import unicorn, if not available this script will need to be run in an environment with it
try:
    from unicorn import *
    from unicorn.ppc_const import *
except ImportError:
    print("Unicorn engine not found. Please install it: pip install unicorn")
    exit(1)

# MED9.1 Memory Map (Approximation/Standard for MPC5xx)
# Flash (ROM): 0x000000 -> 0x200000 (2MB) - or varying based on specific ECU
# RAM:         0x3F8000 -> 0x400000 (32KB approx) - check specific datasheet/map
# Percipherals: 0xC00000+

# Load the binary
BINARY_PATH = "../passat_azx_ori.bin"
FLASH_BASE = 0x00000000
RAM_BASE   = 0x003F8000
RAM_SIZE   = 0x00010000 # 64KB - generous allocation for emulation

def load_binary(uc, path):
    with open(path, 'rb') as f:
        data = f.read()
    # Align to 4k page if necessary (Unicorn requirement usually)
    # Map memory for ROM: 0x0 to 0x3F8000 (Start of RAM)
    # This covers the 2.5MB binary
    uc.mem_map(FLASH_BASE, 0x3F8000) 
    uc.mem_write(FLASH_BASE, data)
    print(f"Loaded binary to {hex(FLASH_BASE)}, size: {len(data)}")

def setup_emulator():
    print("Initializing Unicorn Engine for PPC32...")
    uc = Uc(UC_ARCH_PPC, UC_MODE_PPC32 + UC_MODE_BIG_ENDIAN)


    # Map RAM
    print(f"Mapping RAM at {hex(RAM_BASE)} size {hex(RAM_SIZE)}")
    uc.mem_map(RAM_BASE, RAM_SIZE)

    # Map IO (0xC0000000)
    IO_BASE = 0xC0000000
    IO_SIZE = 0x10000
    uc.mem_map(IO_BASE, IO_SIZE)
    print(f"Mapping IO at {hex(IO_BASE)}")

    # Map External RAM (Stack is here)
    EXT_RAM_BASE = 0x7F0000
    EXT_RAM_SIZE = 0x20000 # 128KB
    uc.mem_map(EXT_RAM_BASE, EXT_RAM_SIZE)
    print(f"Mapping External RAM at {hex(EXT_RAM_BASE)}")

    # Map Dummy Flash (0x400000 - 0x600000) for calibration data access
    uc.mem_map(0x400000, 0x200000)

    # Load ROM
    load_binary(uc, BINARY_PATH)

    # Setup Registers
    # Values found from reset vector trace (0x1004):
    # r1 (Stack): 0x7FEFFC (Derived from 0x800000 - 0x1004)
    # r13 (SDA): 0x7FFFF0
    # r2 (SDA2): 0x17FF0
    
    uc.reg_write(UC_PPC_REG_1, 0x7FEFFC)
    uc.reg_write(UC_PPC_REG_13, 0x7FFFF0)
    uc.reg_write(UC_PPC_REG_2, 0x17FF0)
    uc.reg_write(UC_PPC_REG_9, 1) # Force "True" for potential checks

    return uc

def hook_mem_access(uc, access, address, size, value, user_data):
    if access == UC_MEM_WRITE:
        print(f">>> Write IO: @0x{address:x}, value=0x{value:x}, size={size}")
    else:
        print(f">>> Read IO: @0x{address:x}, size={size}")

def run_function(uc, start_addr, end_addr=None):
    print(f"Starting emulation at 0x{start_addr:x}")
    try:
        # Hook for IO
        uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, hook_mem_access, begin=0xC0000000, end=0xC0010000)
        
        # Run until error or end_addr
        uc.emu_start(start_addr, end_addr if end_addr else start_addr + 0x1000)
    except UcError as e:
        print(f"Unicorn Error: {e}")
        pc = uc.reg_read(UC_PPC_REG_PC)
        print(f"PC at crash: {hex(pc)}")

if __name__ == "__main__":
    uc = setup_emulator()
    
    # Test run around a potential CAN access found by find_can_init.py
    # 0x49440: lis r31, 0xc000
    target_addr = 0x49440
    print(f"Testing execution from {hex(target_addr)}...")
    run_function(uc, target_addr, target_addr + 0x100)

