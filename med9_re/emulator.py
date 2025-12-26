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
    # Map excessive memory for ROM to handle 2MB
    uc.mem_map(FLASH_BASE, 4 * 1024 * 1024) 
    uc.mem_write(FLASH_BASE, data)
    print(f"Loaded binary to {hex(FLASH_BASE)}, size: {len(data)}")

def setup_emulator():
    print("Initializing Unicorn Engine for PPC32...")
    uc = Uc(UC_ARCH_PPC, UC_MODE_PPC32 + UC_MODE_BIG_ENDIAN)

    # Map RAM
    print(f"Mapping RAM at {hex(RAM_BASE)} size {hex(RAM_SIZE)}")
    uc.mem_map(RAM_BASE, RAM_SIZE)

    # Load ROM
    load_binary(uc, BINARY_PATH)

    # Setup Registers
    # r1: Stack Pointer
    stack_top = RAM_BASE + RAM_SIZE - 0x100
    uc.reg_write(UC_PPC_REG_R1, stack_top)
    
    # r2: SDA2 Base (Read-only small data) - often 0x00xxxxx or similar in MED9
    # r13: SDA Base (Read-write small data)
    # These need to be found from the binary (usually first few instructions sets them)
    # valid values are critical for global variable access
    uc.reg_write(UC_PPC_REG_R2, 0x0) # Placeholder
    uc.reg_write(UC_PPC_REG_R13, 0x0) # Placeholder

    return uc

def hook_block(uc, address, size, user_data):
    print(f">>> Tracing basic block at 0x{address:x}, block size = 0x{size:x}")

def run_function(uc, start_addr, end_addr=None):
    print(f"Starting emulation at 0x{start_addr:x}")
    try:
        # Hook for tracing
        # uc.hook_add(UC_HOOK_BLOCK, hook_block)
        
        # Run until error or end_addr
        # If end_addr is None, runs "forever" or untill hook stops it
        uc.emu_start(start_addr, end_addr if end_addr else start_addr + 0x1000)
    except UcError as e:
        print(f"Unicorn Error: {e}")
        pc = uc.reg_read(UC_PPC_REG_PC)
        print(f"PC at crash: {hex(pc)}")

if __name__ == "__main__":
    uc = setup_emulator()
    
    # TODO: Find the address of the fueling function or CAN handler
    # target_addr = 0x...
    # run_function(uc, target_addr)
    print("Emulator setup complete. Ready to run target functions.")
