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
BINARY_PATH = "passat_azx_flash.bin"
FLASH_BASE = 0x00000000
RAM_BASE   = 0x003F8000
RAM_SIZE   = 0x00010000 # 64KB

def load_binary(uc, path):
    with open(path, 'rb') as f:
        data = f.read()
    # Linear mapping of 2MB Flash
    # Note: RAM at 0x3F8000 effectively "overlays" the flash in real hardware
    # For Unicorn, we map Flash fully, but we must handle the overlap if we map RAM at the same place.
    # MED9.1: Flash 0-0x200000. RAM 0x3F8000 is internal MPC5xx RAM, not unrelated.
    # Actually, 0x000000-0x1FFFFF is external flash. 
    # 0x3F8000 is internal RAM. They do not overlap physically.
    
    uc.mem_map(FLASH_BASE, 2 * 1024 * 1024) 
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
    EXT_RAM_SIZE = 0x10000 # 64KB - Ends at 0x800000
    uc.mem_map(EXT_RAM_BASE, EXT_RAM_SIZE)
    print(f"Mapping External RAM at {hex(EXT_RAM_BASE)}")

    # Map Dummy Flash/RAM (0x500000 - 0x7F0000)
    # Covers calibration data and potential secondary RAM/peripherals at 0x70xxxx
    uc.mem_map(0x500000, 0x2F0000)

    # Extra Region for 0x900000 access
    uc.mem_map(0x800000, 0x200000, UC_PROT_ALL)
    print("Mapping Extra Region at 0x800000")

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
    
    # R9 is loaded from 0x1ca6(r13) at 0x4943c
    # 0x7FFFF0 + 0x1ca6 = 0x801C96
    # We want it to be 1 to pass the check
    uc.mem_write(0x7FFFF0 + 0x1CA6, b'\x01')
    
    # Setup Stack Return Address to detect success
    # The function loads LR from 0x14(r1)
    ret_addr = 0xDEADBEEF
    uc.mem_write(0x7FEFFC + 0x14, struct.pack('>I', ret_addr))

    return uc

def hook_block(uc, address, size, user_data):
    # Retrieve existing counter from user_data (needs to be mutable)
    # But user_data passed to hook_add is fixed.
    # We can use a global or attach to uc object?
    # Simple global for script
    global block_count
    block_count += 1
    if block_count > 100000:
        uc.emu_stop()
        return
    print(f">>> Block: 0x{address:x}")

def hook_mem_access(uc, access, address, size, value, user_data):
    pc = uc.reg_read(UC_PPC_REG_PC)
    if access == UC_MEM_WRITE:
        print(f">>> Write IO: @0x{address:x}, value=0x{value:x}, size={size} (PC={hex(pc)})")
    else:
        # Filter read log if it's too noisy, but for Table it's fine
        if address >= 0x2BE00 and address < 0x2C200:
             print(f">>> Read CAN Table: @0x{address:x}, size={size} (PC={hex(pc)})")
        else:
             print(f">>> Read IO: @0x{address:x}, size={size} (PC={hex(pc)})")

def run_function(uc, start_addr, end_addr=None):
    print(f"Starting emulation at 0x{start_addr:x}")
    global block_count
    block_count = 0
    try:
        # Hook for IO
        uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, hook_mem_access, begin=0xC0000000, end=0xC0010000)
        
        # Hook for CAN ID Table
        uc.hook_add(UC_HOOK_MEM_READ, hook_mem_access, begin=0x2BE00, end=0x2C200)
        
        # Hook for data block
        uc.hook_add(UC_HOOK_MEM_READ, hook_mem_access, begin=0x2bc00, end=0x2c000)

        # Hook for tracing
        uc.hook_add(UC_HOOK_BLOCK, hook_block)
        
        # PATCHES
        # 1. Bypass Startup Check at 0x128: Force Branch to 0x138
        # 0x128: bne 0x138 (40820010). Replace with b 0x138 (48000010).
        uc.mem_write(0x128, b'\x48\x00\x00\x10')
        
        # 2. Bypass HW Loop at 0x1170c: Replace bne 0x116fc with NOP
        # 0x1170c: bne 0x116fc. Replace with nop (60000000).
        uc.mem_write(0x1170c, b'\x60\x00\x00\x00')

        # 3. Bypass HW Loop at 0x117b0: Replace bne 0x117a0 with NOP
        # 0x117b0: bne 0x117a0. Replace with nop (60000000).
        uc.mem_write(0x117b0, b'\x60\x00\x00\x00')

        # Run until error or end_addr
        uc.emu_start(start_addr, end_addr if end_addr else start_addr + 0x100000)
    except UcError as e:
        print(f"Unicorn Error: {e}")
        pc = uc.reg_read(UC_PPC_REG_PC)
        print(f"PC at crash: {hex(pc)}")
        try:
            val = uc.mem_read(pc, 4)
            print(f"Instruction at crash: {val.hex()}")
        except:
            print("Could not read instruction at PC")
        
        # Dump registers
        print("Register Dump:")
        for i in range(32):
            import unicorn.ppc_const as ppc
            reg_id = getattr(ppc, f"UC_PPC_REG_{i}")
            reg_val = uc.reg_read(reg_id)
            print(f"r{i}: {hex(reg_val)}", end="  ")
            if (i+1) % 4 == 0: print("")
        print("")
        r10 = uc.reg_read(UC_PPC_REG_10)
        print(f"Target Check (r10+0x4002): {hex(r10 + 0x4002)}")

if __name__ == "__main__":
    uc = setup_emulator()
    
    # Trace startup to find table read
    target_addr = 0x100 # Reset Vector
    print(f"Tracing startup from {hex(target_addr)} to find CAN Table Access...")
    # Run for a sufficient amount of instructions/time
    run_function(uc, target_addr, target_addr + 0x50000)

