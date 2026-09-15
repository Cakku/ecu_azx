BINARY_PATH = "../passat_azx_ori.bin"
MIN_SIZE = 256 # Bytes

with open(BINARY_PATH, 'rb') as f:
    data = f.read()

free_ranges = []
current_start = -1
count = 0

for i, byte in enumerate(data):
    if byte == 0xFF:
        if current_start == -1:
            current_start = i
        count += 1
    else:
        if count >= MIN_SIZE:
            free_ranges.append((current_start, count))
        current_start = -1
        count = 0

# Check last
if count >= MIN_SIZE:
    free_ranges.append((current_start, count))

print(f"Found {len(free_ranges)} blocks of free space (min {MIN_SIZE} bytes)")
for start, size in free_ranges[:20]: # Show first 20
    print(f"Address: {hex(start)} - Size: {size}")
