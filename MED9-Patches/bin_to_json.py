import json
import sys
import os

def make_patch_json(file_path):
    if not os.path.isfile(file_path):
        return json.dumps({"error": "File not found"})

    with open(file_path, 'rb') as f:
        data = f.read()

    length = len(data)
    hex_data = data.hex()
    value_new = f"0x{hex_data.upper()}"
    value_old = f"0x{'FF' * length}"

    patch = {
        "name": "Patch 1",
        "comment": "",
        "diffs": [
            {
                "addr": "0x0",
                "value_old": value_old,
                "value_new": value_new
            }
        ]
    }

    return json.dumps(patch, indent=2)

# Usage
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python script.py <file_path>")
    else:
        print(make_patch_json(sys.argv[1]))