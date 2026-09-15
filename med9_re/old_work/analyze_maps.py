#!/usr/bin/env python3
"""
Analyze passat_azx_flash.c to find potential map/table data structures
that could be related to fueling calculations.
"""

import re
import json
from collections import defaultdict

BINARY_PATH = "passat_azx_flash.bin"
C_FILE = "passat_azx_flash.c"

# Common MED9 variable name patterns
FUEL_PATTERNS = [
    r'ti[_\w]*',  # Injection time (German: "Tiefe Injection")
    r'kr[_\w]*',  # Correction factor
    r'lamfa[_\w]*',  # Lambda factor
    r'lamb[_\w]*',  # Lambda
    r'zw[_\w]*',  # Ignition angle (Zündzeitpunkt Winkel)
    r'rl[_\w]*',  # Air mass/load
    r'nmot[_\w]*',  # Engine RPM
    r'te[_\w]*',  # Temperature
]

print("Analyzing decompiled code for map accesses...")
print("=" * 70)

# Read and analyze C file for map/table patterns
with open(C_FILE, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Find all DAT_ references with array accesses
map_pattern = re.compile(r'_?DAT_([0-9a-f]{8})\[')
matches = map_pattern.findall(content)

# Count occurrences of each map address
map_counts = defaultdict(int)
for addr in matches:
    map_counts[addr] += 1

print(f"\nFound {len(set(matches))} unique potential maps/tables")
print(f"\nTop 20 most frequently accessed data arrays (potential maps):")
print("-" * 70)
print(f"{'Address':<12} {'Count':<8} {'Decimal Address'}")
print("-" * 70)

for addr, count in sorted(map_counts.items(), key=lambda x: x[1], reverse=True)[:20]:
    dec_addr = int(addr, 16)
    print(f"0x{addr}  {count:>6}   {dec_addr}")

# Find functions with high number of map accesses
print(f"\n\nSearching for functions with many array/map accesses...")
print("=" * 70)

# Simple function extraction (look for void FUN_ patterns)
func_pattern = re.compile(r'void (FUN_[0-9a-f]{8})\(void\)', re.MULTILINE)
functions = func_pattern.finditer(content)

func_map_usage = []
for func_match in functions:
    func_name = func_match.group(1)
    func_addr = func_name.split('_')[1]
    func_start = func_match.start()
    
    # Find the end of this function (next function or end of file)
    next_func = func_pattern.search(content, func_match.end())
    func_end = next_func.start() if next_func else len(content)
    
    func_body = content[func_start:func_end]
    
    # Count map accesses in this function
    map_accesses = len(map_pattern.findall(func_body))
    
    if map_accesses > 10:  # Only show functions with significant map usage
        func_map_usage.append((func_addr, func_name, map_accesses))

print(f"\nTop 15 functions by map/table access count:")
print("-" * 70)
print(f"{'Address':<12} {'Function':<20} {'Map Accesses'}")
print("-" * 70)

for addr, name, count in sorted(func_map_usage, key=lambda x: x[2], reverse=True)[:15]:
    print(f"0x{addr}  {name:<20} {count:>6}")

print("\n" + "=" * 70)
print("Analysis complete. Check addresses in Ghidra for detailed inspection.")
