# ff_counter - Flash 1, the no-op counter patch (issue #27).
#
#   make check    build + assert no r2/r13 use, no small data, sizes agree
#   make dump     disassemble the raw blob at its link address
#   make gen      regenerate patch.json's `changes` from the blob
#   make apply    apply to a copy of the stock dump under work/
#
# Placement comes from patch.json ("build".flash / .ram / .ram_size).
# Nothing here writes to an ECU.  See README.md and docs/06_patch_pipeline.md.

NAME := ff_counter
include ../common/patch.mk
