import json
import sys
import argparse

def hex_to_bytes(hex_str):
    return bytes.fromhex(hex_str)

def patch_binary(file_path, patch_data):
    with open(file_path, "rb") as f:
        binary = bytearray(f.read())

    for diff in patch_data["diffs"]:
        comment = diff.get("comment", "")
        addr = int(diff["addr"], 16) - 0x00400000
        value_old = hex_to_bytes(diff["value_old"])
        value_new = hex_to_bytes(diff["value_new"])



        print(f"Applying patch: {comment}")
        print(f"Address: {diff['addr']}, Length: {len(value_old)}")

        if binary[addr:addr+len(value_old)] != value_old:
            raise ValueError(f"Original value mismatch at {diff['addr']}")

        binary[addr:addr+len(value_new)] = value_new

    patched_file = file_path + ".patched"
    with open(patched_file, "wb") as f:
        f.write(binary)

    print(f"Patching complete. Output written to {patched_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Binary patching utility")
    parser.add_argument("binary", help="Path to binary file to patch")
    parser.add_argument("patch", help="Path to JSON patch file")
    args = parser.parse_args()

    with open(args.patch, "r") as f:
        patch_data = json.load(f)

    patch_binary(args.binary, patch_data)