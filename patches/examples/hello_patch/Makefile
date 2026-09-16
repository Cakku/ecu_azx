# hello_patch - toolchain proof (issue #5) and framework template (issue #25).
#
#   make                  build with the LLVM toolchain (default on this Mac)
#   make TOOLCHAIN=gcc    build with devkitPPC, if it is installed
#   make check            build, then assert the stock header is current, r2/r13
#                         are untouched, no small-data section was emitted and
#                         the blob is exactly the linked flash size
#   make dump             disassemble the raw blob at its link address
#   make gen              regenerate patch.json's `changes` from the blob
#   make apply            apply to a copy of the stock dump under work/
#   make clean
#
# Placement comes from patch.json ("build".flash / .ram / .ram_size) and can be
# overridden without editing anything:
#   make PATCH_FLASH=0x00160000 PATCH_RAM=0x00806100
#
# Nothing here writes to an ECU.  See docs/06_patch_pipeline.md for how a blob
# becomes bytes in flash (and docs/04_re_guidelines.md section 6 for the rules
# that come before that).

NAME := hello
include ../../common/patch.mk
