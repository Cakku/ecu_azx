# patch.mk - the build rules every MED9.1.1 patch shares.
#
# A patch Makefile is three lines:
#
#     NAME := ff_counter
#     include ../common/patch.mk
#
# Sources are picked up from src/ (*.c and *.S).  Placement comes from
# patch.json's "build" section, so the descriptor and the linker can never
# disagree; any of them can still be overridden on the command line:
#
#     make PATCH_FLASH=0x00160000
#
# Targets
#     all     build/<name>.{o,elf,bin,lss,sym}
#     check   build + assert the stock header is current, no r2/r13 use, no
#             small-data section, the blob is exactly the linked flash size and
#             .bss fits the declared RAM block
#     dump    disassemble the raw blob at its link address (blobdis --check-sda)
#     gen     regenerate patch.json's "changes" from the blob and the symbols
#     apply   apply the patch to a copy of the stock dump under work/
#     clean
#
# Nothing here writes to an ECU, and nothing here writes to data/.
# See docs/06_patch_pipeline.md.

COMMON_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
REPO       := $(COMMON_DIR)../..

ifndef NAME
$(error set NAME before including patch.mk)
endif

PYTHON     ?= $(REPO)/.venv/bin/python3
STOCK      ?= $(REPO)/data/passat_azx_ori.bin
WORK       ?= $(REPO)/work
PATCH_JSON ?= patch.json
BUILD      ?= build

TOOLCHAIN  ?= llvm
LLVM_DIR   ?= /Users/carlo/toolchains/LLVM-23.1.1-macOS-ARM64
DEVKITPPC  ?= /opt/devkitpro/devkitPPC

# ------------------------------------------------- placement from patch.json --
# One source of truth (docs/06_patch_pipeline.md section 1).  `?=` so that
# `make PATCH_FLASH=...` still wins.
_jq = $(shell $(PYTHON) -c "import json,sys;print(json.load(open('$(PATCH_JSON)'))['build'].get('$(1)',''))" 2>/dev/null)

PATCH_FLASH    ?= $(call _jq,flash)
PATCH_RAM      ?= $(call _jq,ram)
PATCH_RAM_SIZE ?= $(call _jq,ram_size)
# Flatten: `?=` leaves a recursive variable, which would re-run the interpreter
# on every one of the half-dozen uses below.
PATCH_FLASH    := $(strip $(PATCH_FLASH))
PATCH_RAM      := $(strip $(PATCH_RAM))
PATCH_RAM_SIZE := $(strip $(PATCH_RAM_SIZE))

ifeq ($(strip $(PATCH_FLASH)),)
$(error no "build".flash in $(PATCH_JSON) and no PATCH_FLASH on the command line)
endif
ifeq ($(strip $(PATCH_RAM)),)
$(error no "build".ram in $(PATCH_JSON) and no PATCH_RAM on the command line)
endif
ifeq ($(strip $(PATCH_RAM_SIZE)),)
PATCH_RAM_SIZE := 0
endif

# ------------------------------------------------------------------ sources --
SRC_C ?= $(wildcard src/*.c)
SRC_S ?= $(wildcard src/*.S)
# Assembly first: the trampolines are the patch's entry points, so they land at
# the front of the blob and the hook target is easy to find in a hex dump.
OBJS  := $(patsubst src/%.S,$(BUILD)/%.o,$(SRC_S)) $(patsubst src/%.c,$(BUILD)/%.o,$(SRC_C))

ELF := $(BUILD)/$(NAME).elf
BIN := $(BUILD)/$(NAME).bin
LSS := $(BUILD)/$(NAME).lss
SYM := $(BUILD)/$(NAME).sym

# ---------------------------------------------------------------- toolchain --
# LLVM has no libgcc for PowerPC: avoid 64-bit division and float conversion.
# It reserves r2/r13 on 32-bit SVR4 and emits no small data by itself, which
# `check` verifies rather than assumes (docs/03_tooling.md section 3.1).
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

# devkitPPC flags from docs/03_tooling.md section 3; -msdata=none -G0
# -ffixed-r2 -ffixed-r13 keep GCC off the ECU's SDA bases.
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

CFLAGS  += $(EXTRA_CFLAGS)
LDFLAGS += -T $(COMMON_DIR)patch.ld \
           --defsym=PATCH_FLASH=$(PATCH_FLASH) \
           --defsym=PATCH_RAM=$(PATCH_RAM) \
           --defsym=PATCH_RAM_SIZE=$(PATCH_RAM_SIZE) \
           $(EXTRA_LDFLAGS)

# ------------------------------------------------------------------- rules --
.PHONY: all check dump gen apply clean
all: $(BIN) $(LSS) $(SYM)

$(BUILD):
	@mkdir -p $(BUILD)

# Every object depends on every header, the shared trampoline macros and this
# file: a stale object compiled against an older ff_state.h is exactly the bug
# the integration of wave E pair 2 hit (six `cmplwi r4,0x40` in handlers whose
# .c had not changed while FF_LENGTH had become 0x44).  Coarse, but a patch is
# a dozen files and the rebuild is a second (2026-09-17).
HDRS := $(wildcard src/*.h) $(wildcard $(COMMON_DIR)*.h) $(COMMON_DIR)hooks.S \
        $(COMMON_DIR)patch.mk $(PATCH_JSON)

$(BUILD)/%.o: src/%.c $(HDRS) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/%.o: src/%.S $(HDRS) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJS) $(COMMON_DIR)patch.ld
	$(LD) $(LDFLAGS) -o $@ $(OBJS)

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(LSS): $(ELF)
	$(OBJDUMP) -d $< > $@

$(SYM): $(ELF)
	$(NM) -n $< > $@

# `dump` is the check that matters before flashing: disassemble the raw bytes
# exactly as the CPU will fetch them, not the ELF.  llvm-objdump has no
# `-b binary`, so this goes through the project's capstone helper.
dump: $(BIN)
	$(PYTHON) $(REPO)/tools/blobdis.py $(BIN) --addr $(PATCH_FLASH) --check-sda

check: all
	@echo "== stock header is current =="
	@$(PYTHON) $(REPO)/tools/gen_stock_header.py --check
	@echo "== sections =="
	@$(READELF) -S $(ELF) | grep -E 'sdata|srodata|\.data|\.text|\.rodata|\.bss' || true
	@echo "== blob size vs linked flash size =="
	@$(PYTHON) $(REPO)/tools/patch_gen.py --check-size $(NAME) \
	    --blob $(BIN) --sym $(SYM) --ram-size $(PATCH_RAM_SIZE)
	@echo "== r2 / r13 usage in the emitted code (must be empty) =="
	@if $(OBJDUMP) -d $(ELF) | grep -nE '\b(r2|r13)\b'; then \
	    echo "FAIL: the patch touches an ECU small-data base register"; exit 1; \
	 else echo "OK: r2 and r13 are never referenced"; fi
	@echo "== small data sections (must be empty) =="
	@if $(READELF) -S $(ELF) | grep -qE '\.sdata|\.sbss|\.srodata|\.sdata2'; then \
	    echo "FAIL: small-data section emitted"; exit 1; \
	 else echo "OK: no .sdata/.sbss/.srodata/.sdata2"; fi
	@echo "== link addresses =="
	@cat $(SYM)

gen: all
	$(PYTHON) $(REPO)/tools/patch_gen.py . --stock $(STOCK)

apply: gen
	@mkdir -p $(WORK)
	$(PYTHON) $(REPO)/tools/patch_apply.py $(STOCK) . -o $(WORK)/$(NAME).bin

clean:
	rm -rf $(BUILD)
