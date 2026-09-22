# ff_counter - Flash 1, the no-op counter patch (issue #27).
#
#   make check    build + assert no r2/r13 use, no small data, sizes agree
#   make dump     disassemble the raw blob at its link address
#   make gen      regenerate patch.json's `changes` from the blob
#   make apply    apply to a copy of the stock dump under work/
#
# Two build variants (brief F1, 2026-09-22 - README.md has the table):
#
#   make                    HOOKS=both     both 10 ms rasters: 0x432940 in task
#                                          set A (ON-CHIP flash) and 0x12067C
#                                          in set B.  This is the default and
#                                          the variant patch.json describes.
#   make HOOKS=external     the set-B word 0x12067C alone - the pre-F1 patch,
#                           byte for byte.  Described by patch.external.json,
#                           built into build/external/, applied to
#                           work/external/ff_counter.bin.  Useful only if
#                           scheduler.md 11.8 turns out to be wrong AND the
#                           on-chip array cannot be written.
#
# Each variant has its own build directory and its own patch.json, so nothing
# can be built from a stale object of the other one.  `make clean` cleans the
# selected variant; `make clean && make HOOKS=external clean` cleans both.
#
# Placement comes from the variant's patch.json ("build".flash / .ram /
# .ram_size).  Nothing here writes to an ECU.  See README.md and
# docs/06_patch_pipeline.md.

NAME  := ff_counter
HOOKS ?= both

ifeq ($(HOOKS),both)
  PATCH_JSON   := patch.json
  EXTRA_CFLAGS := -DFF_HOOKS_BOTH=1
else ifeq ($(HOOKS),external)
  PATCH_JSON   := patch.external.json
  # Keep the default variant's output: a `make HOOKS=external apply` must not
  # silently overwrite work/ff_counter.bin, which is the image meant for Flash 1.
  WORK         := ../../work/external
else
  $(error HOOKS must be `both` (the default) or `external`, not `$(HOOKS)`)
endif

BUILD := build/$(HOOKS)

include ../common/patch.mk
