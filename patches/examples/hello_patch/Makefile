# hello_patch - toolchain proof for MED9.1.1 flash patches (issue #5).
#
#   make                  build with the LLVM toolchain (default on this Mac)
#   make TOOLCHAIN=gcc    build with devkitPPC, if it is installed
#   make check            build, then assert r2/r13 are untouched and no
#                         small-data section was emitted
#   make dump             disassemble the raw blob at its link address
#   make clean
#
# Override the placement without editing the linker script:
#   make PATCH_FLASH=0x00146000 PATCH_RAM=0x00806100
#
# Nothing here writes to an ECU.  See docs/06_patch_pipeline.md for how a blob
# becomes bytes in flash (and docs/04_re_guidelines.md section 6 for the rules
# that come before that).

TOOLCHAIN   ?= llvm
LLVM_DIR    ?= /Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64
DEVKITPPC   ?= /opt/devkitpro/devkitPPC

PATCH_FLASH ?= 0x00145000
PATCH_RAM   ?= 0x00806000

NAME        := hello
BUILD       := build
OBJ         := $(BUILD)/$(NAME).o
ELF         := $(BUILD)/$(NAME).elf
BIN         := $(BUILD)/$(NAME).bin
LSS         := $(BUILD)/$(NAME).lss
SYM         := $(BUILD)/$(NAME).sym

# ---------------------------------------------------------------- LLVM ----
# LLVM has no libgcc for PowerPC: avoid 64-bit division and float conversion.
# It reserves r2 and r13 on 32-bit SVR4 by itself and emits no small data,
# which `make check` verifies rather than assumes.
LLVM_CC      := $(LLVM_DIR)/bin/clang
LLVM_LD      := $(LLVM_DIR)/bin/ld.lld
LLVM_OBJCOPY := $(LLVM_DIR)/bin/llvm-objcopy
LLVM_OBJDUMP := $(LLVM_DIR)/bin/llvm-objdump
LLVM_NM      := $(LLVM_DIR)/bin/llvm-nm
LLVM_READELF := $(LLVM_DIR)/bin/llvm-readelf

LLVM_CFLAGS := --target=powerpc-unknown-eabi -mcpu=603e \
               -msoft-float -ffreestanding -fno-builtin -nostdlib \
               -fno-pic -fno-stack-protector -fno-asynchronous-unwind-tables \
               -fno-common -fomit-frame-pointer \
               -Os -std=c99 -Wall -Wextra -Werror

# ------------------------------------------------------------ devkitPPC ----
# Flags from docs/03_tooling.md section 3.  -mcpu=505 is the MPC5xx entry;
# -msdata=none -G0 -ffixed-r2 -ffixed-r13 keep GCC off the ECU's SDA bases.
GCC_CC      := $(DEVKITPPC)/bin/powerpc-eabi-gcc
GCC_LD      := $(DEVKITPPC)/bin/powerpc-eabi-ld
GCC_OBJCOPY := $(DEVKITPPC)/bin/powerpc-eabi-objcopy
GCC_OBJDUMP := $(DEVKITPPC)/bin/powerpc-eabi-objdump
GCC_NM      := $(DEVKITPPC)/bin/powerpc-eabi-nm
GCC_READELF := $(DEVKITPPC)/bin/powerpc-eabi-readelf

GCC_CFLAGS := -mcpu=505 -mbig-endian -meabi -msdata=none -G0 \
              -ffixed-r2 -ffixed-r13 -mno-relocatable -mstrict-align \
              -msoft-float -ffreestanding -fno-builtin -nostdlib \
              -nostartfiles -fno-pic -fno-stack-protector \
              -fno-asynchronous-unwind-tables -fno-common \
              -Os -std=c99 -Wall -Wextra -Werror

ifeq ($(TOOLCHAIN),llvm)
CC      := $(LLVM_CC)
LD      := $(LLVM_LD)
OBJCOPY := $(LLVM_OBJCOPY)
OBJDUMP := $(LLVM_OBJDUMP)
NM      := $(LLVM_NM)
READELF := $(LLVM_READELF)
CFLAGS  := $(LLVM_CFLAGS)
LDFLAGS := -m elf32ppc --no-dynamic-linker --discard-locals
else
CC      := $(GCC_CC)
LD      := $(GCC_LD)
OBJCOPY := $(GCC_OBJCOPY)
OBJDUMP := $(GCC_OBJDUMP)
NM      := $(GCC_NM)
READELF := $(GCC_READELF)
CFLAGS  := $(GCC_CFLAGS)
LDFLAGS := -m elf32ppc --discard-locals
endif

LDFLAGS += -T $(NAME).ld --defsym=PATCH_FLASH=$(PATCH_FLASH) \
           --defsym=PATCH_RAM=$(PATCH_RAM)

.PHONY: all check dump clean
all: $(BIN) $(LSS) $(SYM)

$(BUILD):
	@mkdir -p $(BUILD)

$(OBJ): $(NAME).c | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJ) $(NAME).ld
	$(LD) $(LDFLAGS) -o $@ $(OBJ)

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(LSS): $(ELF)
	$(OBJDUMP) -d $< > $@

$(SYM): $(ELF)
	$(NM) -n $< > $@

# `dump` is the check that matters before flashing: disassemble the raw bytes
# exactly as the CPU will see them, not the ELF.  llvm-objdump has no
# `-b binary`, so this goes through the project's capstone helper.
PYTHON ?= ../../../.venv/bin/python
dump: $(BIN)
	$(PYTHON) ../../../tools/blobdis.py $(BIN) --addr $(PATCH_FLASH) --check-sda

check: all
	@echo "== sections =="
	@$(READELF) -S $(ELF) | grep -E 'sdata|srodata|\.data|\.text|\.rodata|\.bss' || true
	@echo "== blob size =="
	@wc -c < $(BIN)
	@echo "== r2 / r13 usage in the emitted code (must be empty) =="
	@if $(OBJDUMP) -d $(ELF) | grep -nE '\b(r2|r13)\b'; then \
	    echo "FAIL: the patch touches an ECU small-data base register"; exit 1; \
	 else echo "OK: r2 and r13 are never referenced"; fi
	@echo "== small data sections (must be empty) =="
	@if $(READELF) -S $(ELF) | grep -qE '\.sdata|\.sbss|\.srodata|\.sdata2'; then \
	    echo "FAIL: small-data section emitted"; exit 1; \
	 else echo "OK: no .sdata/.sbss/.srodata/.sdata2"; fi
	@echo "== link addresses =="
	@grep -E 'hello_(init|tick|lookup)|__bss' $(SYM)

clean:
	rm -rf $(BUILD)
