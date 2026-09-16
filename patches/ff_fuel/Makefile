# ff_fuel - the flex-fuel MVP (brief D1, issues #32 and #37).
#
#   make check    build + assert no r2/r13 use, no small data, sizes agree
#   make dump     disassemble the raw blob at its link address
#   make gen      regenerate patch.json's `changes` from the blob and FFCAL001
#   make apply    apply to a copy of the stock dump under work/
#
# Placement comes from patch.json ("build".flash / .ram / .ram_size).
# Nothing here writes to an ECU.  See README.md and docs/06_patch_pipeline.md.

NAME := ff_fuel
include ../common/patch.mk

# FFCAL001 is data, not code: it is built by the patch's own generator and
# referenced from patch.json's `build.data`, so it has to exist before `gen`
# reads it.  This is the only thing ff_fuel adds to the shared rules.
CAL_JSON := ffcal001.json
CAL_GEN  := ffcal001.py
CAL_BIN  := $(BUILD)/ffcal001.bin

$(CAL_BIN): $(CAL_GEN) $(CAL_JSON) | $(BUILD)
	$(PYTHON) $(CAL_GEN) -o $@

all: $(CAL_BIN)
gen: $(CAL_BIN)
