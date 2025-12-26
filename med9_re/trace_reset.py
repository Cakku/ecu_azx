from unicorn import *
from unicorn.ppc_const import *
from capstone import *

BINARY_PATH = "../passat_azx_ori.bin"
FLASH_BASE = 0x00000000

# Load binary
with open(BINARY_PATH, 'rb') as f:
    data = f.read()

# Setup Unicorn just for disassembly/tracing context
uc = Uc(UC_ARCH_PPC, UC_MODE_PPC32 + UC_MODE_BIG_ENDIAN)
uc.mem_map(FLASH_BASE, 4 * 1024 * 1024)
uc.mem_write(FLASH_BASE, data)

# Setup Capstone for disassembly
cs = Cs(CS_ARCH_PPC, CS_MODE_32 + CS_MODE_BIG_ENDIAN)

def disassemble_at(addr, count=20):
    print(f"--- Disassembly at {hex(addr)} ---")
    code = uc.mem_read(addr, count * 4)
    for i in cs.disasm(code, addr):
        print(f"0x{i.address:x}: {i.mnemonic} {i.op_str}")
        if i.mnemonic == 'b':
            # rudimentary branch following
            # operand is target, but cs gives immediate
            try:
                target = int(i.op_str, 0)
                # If it's a forward branch, maybe follow it if it's the reset handler
                return target
            except:
                pass
    return None

# The vector table at 0x100
# 0x100: ba 0x49c (Absolute Branch)
print("Analyzing Startup Code at 0x49c...")
disassemble_at(0x49c, 100)

