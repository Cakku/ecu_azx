# MED9.1 Ethanol Sensor Integration Plan

## Goal Description
Modify the MED9.1 ECU binary to read a new CAN ID (containing Ethanol content) and adjust fueling variables (global fueling modifier/injection time) based on this data. We will achieve this by reverse engineering the binary to find the CAN handling logic and fueling maps, and possibly a global fueling modifier variable, then patching in a new function compiled from C.
We will use existing MED9Toolchain for patching the binary, when we have developed the new function.

## Proposed Workflow
1. Reverse Engineering (Static Analysis)
We will use Ghidra with the provided ppc_med9 extension to analyze 
passat_azx_ori.bin


Goal: Identify:
- CAN message reception handler (Interrupt or Polling).
- Memory location where CAN data is stored (CAN RAM variables).
- [FOUND] CAN ID Table at 0x2BE00.
- The main fueling calculation function (usually looks up LAMFA or similar maps).
- A suitable "free space" in the ROM to inject our new code.
2. Emulation Environment (Dynamic Analysis)
Instead of running the whole OS, we will create a Python + Unicorn harness.

Components:
- emulator.py: Loads the raw binary into memory.
	- Sets up PowerPC registers (r1, r2, r13 are critical in PPC EABI).
	- Hooks the "Read CAN" function or simply writes values to the identified CAN RAM variables.
	- Runs the "Fueling" function and prints the output (Injection Pulse Width).
- passat_azx_flash.bin: ECU flash binary.
- passat_azx_flash.c: ECU flash decompiled C code.
- ghidra_analysis.json: Ghidra analysis results. Automatically generated memory regions are likely not accurate, mainly used for function identification.
- Med9Toolchain: Patching toolchain for patching the binary. Also includes several address signatures for finding the addresses of the functions we need to patch.
- Med9-Patches: Med9toolchain patches for unknown acu binary.
