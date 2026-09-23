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
# G1 (#39): the relocated OBD B2 list in flash carries the address of PID
# 0x52's RAM record as a plain u32 (patch.json build.data, "u32_syms"), so the
# linker has to know it by name.  It is a field of struct ff_state, not an
# object of its own, hence the expression; src/ff_obd.c _Static_asserts that
# FF_OFF_OBD52 (0x4C) is the field's offset, and tests/test_ff_obd_patch.py
# checks the exported value against ff_state + FF_OFF_OBD52.  It has to be set
# BEFORE the include: patch.mk expands EXTRA_LDFLAGS into a simply-expanded
# LDFLAGS at include time.
EXTRA_LDFLAGS += --defsym=ff_obd_pid52_rec=ff_state+0x4C

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

