#Ghidra Export Script
#@author Antigravity
#@category Data Export
#@keybinding 
#@menupath 
#@toolbar 

import json
from ghidra.program.model.symbol import SymbolType

output_file = "/Users/carlo/ecu_azx/med9_re/ghidra_analysis.json"
data = {
    "functions": [],
    "memory_map": [],
    "comments": []
}

# 1. Export Functions
fm = currentProgram.getFunctionManager()
funcs = fm.getFunctions(True) # True = iterate forward
for f in funcs:
    func_data = {
        "name": f.getName(),
        "entry": f.getEntryPoint().toString(),
        "end": f.getBody().getMaxAddress().toString(),
        "size": f.getBody().getNumAddresses()
    }
    data["functions"].append(func_data)

# 2. Export Memory Blocks
mem = currentProgram.getMemory()
blocks = mem.getBlocks()
for b in blocks:
    block_data = {
        "name": b.getName(),
        "start": b.getStart().toString(),
        "end": b.getEnd().toString(),
        "size": b.getSize()
    }
    data["memory_map"].append(block_data)

# 3. Export Comments (EOL, PRE, POST, PLATE)
listing = currentProgram.getListing()
# Iterate over all defined data/instructions with comments
# This can be slow on huge binaries, but for 2MB it's okay.
# Optimization: Just dump specific interesting ranges if needed.
# For now, let's just stick to functions and blocks to be fast.

# Write to file
with open(output_file, 'w') as f:
    json.dump(data, f, indent=2)

print("Export completed to " + output_file)
