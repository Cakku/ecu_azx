/*
 * ff_fuel: eight hook words, in four groups.
 *
 *   D1, below      three HOOK_TAIL trampolines that replace a `bl`
 *   E1, middle     ff_zw_hook, one instruction patch (0x41D40C)
 *   E2             ff_st_hook_a / ff_st_hook_b / ff_zwst_hook, three more
 *                  instruction patches (0x41A680, 0x41A808, 0x431384)
 *   E5, bottom     ff_prail_hook, one instruction patch (0x45845C)
 *
 * Seven of the eight are in the ON-CHIP flash 0x404000-0x47FFFF; only D1's
 * task-set-B raster hook 0x12067C is in external flash.  The patch README's
 * hook table is the authority on that count.
 *
 * All three sites in the FIRST group are `bl` whose whole EABI volatile set is
 * provably dead, so all three use HOOK_TAIL (16-byte frame, saves LR only) and
 * tail-branch with
 * `ba` to the function the site originally called, which therefore still runs,
 * in the same task, in the same order.  One flash word changes per site.
 *
 *   0x42247C  bl rksplit (0x41C3A0), engine-synchronous task_segment_a
 *             (0x4223B0).  re/findings/injection.md section 6.2: the task body
 *             is a flat list of argument-less `bl`, so r3-r12, CR, CTR and XER
 *             are dead and LR is free.  ff_rk_scale scales `rk` at 0x803038
 *             and the `ba` hands control to rksplit exactly as the `bl` did -
 *             with r1 and LR unchanged, so rksplit's `blr` returns into the
 *             task.
 *
 *   0x432940  bl nop_leaf_bd9e4 (0xBD9E4), task set A's 10 ms raster
 *             (0x4328E4).  re/findings/scheduler.md section 8.1: the target is
 *             a one-instruction empty function whose only caller in the whole
 *             image is this site, and both neighbours are argument-less `bl`.
 *
 *   0x12067C  bl clr_ram_7FE889_800E18 (0x11F02C), task set B's 10 ms raster
 *             (0x1205A0).  re/findings/scheduler.md section 8 - C1's site,
 *             re-used unchanged (ff_counter and ff_fuel are never co-flashed).
 *
 * Both raster tasks are hooked because exactly one task set is live and the
 * dump cannot say which (scheduler.md section 11.7).  ff_fuel_tick_a/b differ
 * only in the source byte they pass, and the arbitration in ff_fuel.c keeps
 * the tick rate at one per raster period even if both ever fired.
 */
#include "../../common/med9_stock.h"
#include "../../common/hooks.S"
#include "ff_state.h"

    HOOK_TAIL ff_fuel_rk_hook, ff_rk_scale,    MED9_RKSPLIT
    HOOK_TAIL ff_fuel_hook_a,  ff_fuel_tick_a, MED9_NOP_LEAF_BD9E4
    HOOK_TAIL ff_fuel_hook_b,  ff_fuel_tick_b, MED9_CLR_RAM_7FE889_800E18

/*
 * ---------------------------------------------------------------------------
 * ff_zw_hook - the ignition blend's consumer (brief E1, issue #34).
 *
 * The fourth hook is NOT a trampoline in the sense of the three above: there
 * is no stock call to tail-branch to, because the word it replaces is not a
 * branch.  This is hook technique 3 of docs/06_patch_pipeline.md section 4 -
 * an instruction patch - and the displaced instruction is re-done here.
 *
 *   0041D404  add   r3,r11,r12
 *   0041D408  extsb r10,r10
 *   0041D40C  add   r3,r3,r10        <-- becomes `bl ff_zw_hook`
 *   0041D410  cmpwi r3,0x7f          ; the stock s8 clamp, still runs
 *   ...
 *   0041D430  stb   r3,-0x2cdb(r13)  ; zwgru = 0x7FD315
 *
 * Why no frame and no register saves (re/findings/ignition.md section 11.1,
 * VERIFIED-STATIC):
 *
 *   - live at the site: r3 (the accumulator), r10 (the last term) and r1.
 *     r11 and r12 were both consumed by 0x41D3FC / 0x41D404 and are dead, and
 *     r0 is dead too; this code writes only r11 and r12 and leaves r10 alone.
 *   - LR: `zwgru_build` saved it into 0xC(r1) in its own prologue at 0x41D394
 *     and reloads it at 0x41D42C, so the `bl` clobbers a value nobody will
 *     read again.  Nothing has to be saved, and r1 is never touched.
 *   - CR0: `cmpwi r3,0x7f` at 0x41D410 writes it before anything reads it, so
 *     the `cmplwi` below is free.  CTR and XER are not touched at all.
 *   - the stock s8 clamp at 0x41D410-0x41D428 runs AFTER this, so an offset
 *     that would overflow the byte saturates exactly as stock does.
 *
 * Ten instructions, and the seven after the `bnelr` only run once the state
 * block carries our magic.  RAM is addressed absolutely through r11, never
 * through r2 or r13 (`tools/blobdis.py --check-sda` enforces it); the
 * `PATCH_RAM@ha` / `@l` pair is the linker's, so the block can move without
 * touching this file.  Both offsets are ff_state.h's own, and src/ff_ign.c
 * asserts them against `struct ff_state`.
 * ---------------------------------------------------------------------------
 */
    .section .text.ff_zw_hook, "ax", @progbits
    .globl ff_zw_hook
    .type  ff_zw_hook, @function
    .align 2
ff_zw_hook:
    add   3, 3, 10                              /* the displaced instruction */
    lis   11, PATCH_RAM@ha
    lwz   12, (PATCH_RAM + FF_OFF_MAGIC)@l(11)  /* ff_state.magic            */
    xoris 12, 12, 0x4646
    cmplwi 12, 0x3031                           /* "FF01"?                   */
    bnelr                                       /* no valid state -> stock   */
    lbz   12, (PATCH_RAM + FF_OFF_DZW_E)@l(11)  /* ff_state.dzw_e, s8        */
    extsb 12, 12
    add   3, 3, 12
    blr                                         /* back to 0x41D410          */
    .size ff_zw_hook, . - ff_zw_hook

/*
 * ---------------------------------------------------------------------------
 * The three E2 stubs (brief E2, issue #35): start enrichment and start
 * ignition.  Like ff_zw_hook above, none of them replaces a branch, so there
 * is nothing to tail-branch to and the displaced STORE is re-done here --
 * hook technique 3 of docs/06_patch_pipeline.md section 4.
 *
 *   0041A67C  addi  r31,r3,0
 *   0041A680  sth   r31,0x303c(r13)   <-- becomes `bl ff_st_hook_a`
 *   0041A684  addi  r11,r1,0x18       ; the epilogue, already
 *
 *   0041A804  bl    0x410060
 *   0041A808  sth   r3,0x303c(r13)    <-- becomes `bl ff_st_hook_b`
 *   0041A80C  lbz   r12,-0x16d0(r13)
 *
 *   00431380  li    r31,0x7f
 *   00431384  stb   r31,0x20a6(r13)   <-- becomes `bl ff_zwst_hook`
 *   00431388  lwz   r0,0x14(r1)       ; the epilogue, already
 *
 * Note the register difference the two %ESSTT twins force: the low-pressure
 * module publishes out of **r31** and the high-pressure twin out of **r3**.
 * That is the only thing separating ff_st_hook_a from ff_st_hook_b; they share
 * every other instruction.
 *
 * Why no frame and no register saves (re/findings/start.md section 9.2,
 * VERIFIED-STATIC).  All three functions use the EABI save/restore millicode
 * (`_savegpr_*` 0x0B8234/0x0B8238, `_restgpr_*_x` 0x0B81D8/0x0B81DC) or
 * open-code it, so **LR is already in the caller's frame at 4(old_sp)** and the
 * register holds a dead return address at every one of the three words; a `bl`
 * into these stubs clobbers nothing. r0, r11 and r12 are dead at all three
 * sites, CR0 is dead (nothing reads it before the next writer), CTR and XER
 * are untouched and r1 is never written here.
 *
 * The value register itself (r31, or r3 at the second site) is NEVER modified,
 * although it is dead after the store too: the stubs copy it into r0 and work
 * there, so the displaced instruction stays a pure read of its operand.
 *
 * Both stock cells are addressed ABSOLUTELY, out of med9_stock.h, with an
 * explicit `lis` of the high half -- 0x80302C and 0x802096 both have a low
 * half below 0x8000, so no `@ha` carry correction and no relocation is
 * involved.  RAM in our own block goes through the linker's
 * `PATCH_RAM@ha` / `@l` pair exactly as ff_zw_hook does.  r2 and r13 appear
 * nowhere; `tools/blobdis.py --check-sda` enforces it.
 * ---------------------------------------------------------------------------
 */

/*
 * ff_st_hook_a / ff_st_hook_b - S1, the cranking lever.
 *
 * Publish `min((v * fst_q10) >> 10, 0xFFFF)` instead of `v`.  Four things send
 * it down the untouched path, and each of them stores the very halfword the
 * stock `sth` would have stored:
 *
 *   - **`B_stend` (0x7FE921) is set**, i.e. the start is over.  See the
 *     paragraph below: this one is not optional;
 *   - the state block does not carry our magic (the first milliseconds after
 *     power-up, or a corrupted block);
 *   - `fst_q10 <= 1024`, which is the shipped value, every activation in
 *     which `ff_st_enable` is 0, and any corrupt value that would LEAN the
 *     cranking mixture out;
 *   - implicitly, `ff_st_enable = 0`, because the producer then pins
 *     `fst_q10` at exactly 1024.
 *
 * ### Why the B_stend gate is in the stub and not left to the stock code
 *
 * `re/findings/start.md` section 3 reads the low-pressure twin as
 * `if (B_stend) { 0x80302C = 0x400; return; }` and infers that the hooked
 * store cannot run outside the start.  **That inference is wrong, and the
 * disassembly says so** (corrected in start.md section 9.3):
 *
 *   0041A2D0  lbz   r12,-0x16cf(r13)   ; B_stend 0x7FE921
 *   0041A2D4  cmpwi r12,0
 *   0041A2D8  beq   0x41a2e4           ; not finished -> the real computation
 *   0041A2DC  li    r31,0x400          ; finished -> the neutral 1.0 ...
 *   0041A2E0  b     0x41a680           ; ... published THROUGH THIS STORE
 *
 * The compiler shared the epilogue: the early-out does not return, it jumps to
 * 0x41A680 with r31 = 0x400.  Without the gate the stub would multiply the
 * ECU's explicit "no start enrichment" by `f_st` on every segment of the
 * running engine - the one thing this feature must never do.  The
 * high-pressure twin is built differently (`bne 0x41a824` at 0x41A6A4 skips
 * its store), but both stubs carry the gate: it is the same condition the
 * stock code tests, in the same cell, and it makes "S1 acts only during the
 * start" true of the STORE rather than of one path to it.
 *
 * `tools/find_branch_refs.py data/passat_azx_ori.bin 0x41A680 0x41A808
 * 0x431384 --no-ptr` lists every branch into the three words: 0x41A2E0
 * (this early-out), 0x41A7E0 and 0x431374 (both ordinary computation paths).
 *
 * `clrlwi` before the multiply takes exactly the 16 bits the stock `sth`
 * would have stored, so the scaled path cannot disagree with the neutral one
 * about what "the value" is.  `mullw` of two 16-bit quantities cannot wrap a
 * 32-bit register, so the only bound needed afterwards is the 0xFFFF
 * saturation `re/findings/start.md` section 3.3 asks for.
 *
 * Cost, per start-module activation of the segment task, for ff_st_hook_a
 * (ff_st_hook_b is one instruction cheaper - it falls through instead of
 * branching to the shared tail):
 *
 *     9   the start is over, B_stend set     <- the case that runs all day
 *    14   no valid state block
 *    17   fst_q10 = 1024, the shipped value
 *    23   scaling (25 when the 0xFFFF saturation fires)
 */
    .section .text.ff_st_hook, "ax", @progbits
    .globl ff_st_hook_a
    .type  ff_st_hook_a, @function
    .align 2
ff_st_hook_a:
    mr    0, 31                                 /* site 0x41A680: v in r31   */
    b     .Lff_st_publish
    .size ff_st_hook_a, . - ff_st_hook_a

    .globl ff_st_hook_b
    .type  ff_st_hook_b, @function
ff_st_hook_b:
    mr    0, 3                                  /* site 0x41A808: v in r3    */
.Lff_st_publish:
    lis   11, 0x80                              /* 0x800000, the stock half  */
    lbz   12, (MED9_B_STEND - 0x800000)(11)     /* B_stend 0x7FE921          */
    cmpwi 12, 0
    bne   .Lff_st_store                         /* start over -> stock       */
    lis   11, PATCH_RAM@ha
    lwz   12, (PATCH_RAM + FF_OFF_MAGIC)@l(11)  /* ff_state.magic            */
    xoris 12, 12, 0x4646
    cmplwi 12, 0x3031                           /* "FF01"?                   */
    bne   .Lff_st_store                         /* no valid state -> stock   */
    lhz   12, (PATCH_RAM + FF_OFF_FST_Q10)@l(11)
    cmplwi 12, FF_FST_ONE
    ble   .Lff_st_store                         /* neutral or bad -> stock   */
    clrlwi 0, 0, 16                             /* the halfword sth stores   */
    mullw 12, 12, 0                             /* <= 0xFFFF * 0xFFFF        */
    srwi  12, 12, 10                            /* Q10 -> plain              */
    cmplwi 12, 0xFFFF
    ble   .Lff_st_ok
    li    12, -1
    clrlwi 12, 12, 16                           /* saturate at 0xFFFF        */
.Lff_st_ok:
    mr    0, 12
.Lff_st_store:
    lis   11, (MED9_KSTA_ADAPTED >> 16)
    sth   0, (MED9_KSTA_ADAPTED & 0xFFFF)(11)   /* 0x80302C                  */
    blr
    .size ff_st_hook_b, . - ff_st_hook_b

/*
 * ff_zwst_hook - Z1, the start ignition angle.
 *
 * `zwstt` = clamp_s8(stock + zwst_add).  The byte read from the state block is
 * range-checked UNSIGNED against FF_ZWST_HARD_MAX, which rejects every
 * negative byte as well (0x80..0xFF read as 128..255) and is what lets the
 * stub get away with only the upper s8 clamp: the sum of an s8 and a value in
 * 0..8 cannot go below -128.  The stock clamp that used to protect this store
 * runs BEFORE it (0x431368-0x431380), not after, which is exactly why the
 * clamp is re-done here and not left to the ECU.
 *
 * The producer is proved to run first: `ff_fuel`'s task-set-A tick hook is at
 * 0x432940 and `zwstt_build` is called from 0x432B04, with 114 consecutive
 * argument-less `bl` and no branch in between, in the same 10 ms task
 * (re/findings/start.md section 9.1).  The magic check is kept anyway, for the
 * case where `ff_fuel` is not running at all.
 *
 * Cost: 9 instructions when the block is invalid or the byte is out of range,
 * 16 when it adds, 17 when the +127 clamp fires - once per 10 ms activation,
 * and only while the start is in progress (`zwstt_build` returns at 0x4312B0
 * without reaching the store once 0x7FECCA is set, which is why THIS stub
 * needs no B_stend gate of its own).
 */
    .section .text.ff_zwst_hook, "ax", @progbits
    .globl ff_zwst_hook
    .type  ff_zwst_hook, @function
    .align 2
ff_zwst_hook:
    mr    0, 31                                 /* the stock s8 in r31       */
    lis   11, PATCH_RAM@ha
    lwz   12, (PATCH_RAM + FF_OFF_MAGIC)@l(11)  /* ff_state.magic            */
    xoris 12, 12, 0x4646
    cmplwi 12, 0x3031                           /* "FF01"?                   */
    bne   .Lff_zwst_store                       /* no valid state -> stock   */
    lbz   12, (PATCH_RAM + FF_OFF_ZWST_ADD)@l(11)
    cmplwi 12, FF_ZWST_HARD_MAX                 /* 0 <= zwst_add <= 8 ?      */
    bgt   .Lff_zwst_store                       /* out of range -> stock     */
    extsb 0, 0
    add   0, 0, 12
    cmpwi 0, 127                                /* the stock s8 clamp        */
    ble   .Lff_zwst_store
    li    0, 127
.Lff_zwst_store:
    lis   11, (MED9_ZWSTT >> 16)
    stb   0, (MED9_ZWSTT & 0xFFFF)(11)          /* 0x802096                  */
    blr
    .size ff_zwst_hook, . - ff_zwst_hook

/*
 * ---------------------------------------------------------------------------
 * ff_prail_hook - R1, the rail-pressure setpoint adder (brief E5, issue #36).
 *
 * Like the four stubs above it this replaces an ordinary instruction, not a
 * branch, so there is nothing to tail-branch to and the displaced STORE is
 * re-done here -- hook technique 3 of docs/06_patch_pipeline.md section 4.
 *
 *   00458458  addi  r30,r3,0          ; the fall-through map paths
 *   0045845C  sth   r30,0x3200(r13)   <-- becomes `bl ff_prail_hook`
 *   00458460  lis   r31,0x5d          ; KLPRMAX, i.e. the clamp of rail.md 3.3
 *   ...
 *   00458480  lhz   r30,0x3200(r13)   ; the clamp RE-READS the cell
 *
 * `prsoll_raw` (0x8031F0) has exactly one store in the running engine and this
 * is it.  Adding here puts the offset inside every stock limit that follows:
 * PRSOLMN 7000 = 35.0 bar, **KLPRMAX 22000 = 110.0 bar**, and the pump-volume
 * rate limiter.  They still bind because 0x458480 fetches the cell from RAM
 * again instead of re-using r30 -- which is the entire safety argument for
 * this site (re/findings/rail.md section 12.1).
 *
 * Why no frame, no register saves and no gate (rail.md section 12.1,
 * VERIFIED-STATIC):
 *
 *   - live at the word: r30 (the value), r28 = 0xFFFF, r26 = CWPRSOL, r27 and
 *     r1.  This stub writes r0, r11 and r12 and nothing else; r30 is never
 *     modified, although 0x458480 reloads it from the cell anyway.
 *   - LR: `hdrpsol_main`'s prologue is `addi r11,r1,0` / `stwu` / `mflr r0` /
 *     `bl 0xB8228`, and the EABI save millicode's tail at 0x0B8244 is
 *     `stw r0,4(r11)`.  LR is in the caller's frame before this word runs, so
 *     the `bl` clobbers a dead value and nothing has to be saved.
 *   - CR0: nothing between 0x458460 and 0x458488 reads it, and 0x458488's
 *     `cmpw r3,r30` writes it before the `bgt` at 0x45848C.  CTR and XER are
 *     not touched at all.
 *   - **No gate.**  Three `b` reach this word (0x458260, 0x4583B4, 0x45843C)
 *     and four paths fall through from 0x458458; all seven are map-selection
 *     paths that mean "here is a new setpoint".  The one path that must NOT be
 *     modified -- `0x7FD04D & 1`, "hold the previous setpoint" -- branches
 *     PAST the store (`bne 0x458460` at 0x45826C), so the stub never runs on
 *     it.  That is the opposite of E2's 0x41A680, whose early-out branched
 *     INTO the store and forced a `B_stend` test inside the stub.
 *
 * **No mask before the add, either**, and that is a fact about the ECU rather
 * than an optimisation: r30 is a clean 0..0xFFFF halfword on every path into
 * the word.  `interp_2d_u16` (0x40C444) ends with `clrlwi r3,r8,0x10`, the two
 * `KFPRSOLOFF` paths clamp explicitly to 0 and 0xFFFF (0x458418-0x458438 and
 * 0x458390-0x4583B0), and the `CWPRSOL & 1` path is an `lhz`.  The stock path
 * stores r30 itself, so with a bad state block the byte pattern written is
 * identical whatever r30's upper half contains.
 *
 * Cost, per activation of the 20 ms task 0x45CAC4 that reaches the word:
 *
 *     8   no valid state block      <- the first milliseconds after power-up
 *    12   adding (prail_add = 0 included: it adds zero and stores)
 *    13   the 0xFFFF saturation, which no shipped calibration can reach
 *
 * Twelve is the brief's budget and the reason the stub does NOT re-clamp
 * `prail_add` against FF_PRAIL_HARD_MAX: the producing side clamps it twice,
 * it lives inside the checksummed core so a corrupted byte re-initialises the
 * whole block on the next 10 ms activation, and KLPRMAX bounds the result four
 * instructions later whatever this stub computes.  A fourth check on a path
 * that runs 50 times a second would buy nothing.
 * ---------------------------------------------------------------------------
 */
    .section .text.ff_prail_hook, "ax", @progbits
    .globl ff_prail_hook
    .type  ff_prail_hook, @function
    .align 2
ff_prail_hook:
    lis   11, PATCH_RAM@ha
    lwz   12, (PATCH_RAM + FF_OFF_MAGIC)@l(11)  /* ff_state.magic            */
    xoris 12, 12, 0x4646
    cmplwi 12, 0x3031                           /* "FF01"?                   */
    bne   .Lff_prail_stock                      /* no valid state -> stock   */
    lhz   12, (PATCH_RAM + FF_OFF_PRAIL_ADD)@l(11)
    add   12, 30, 12                            /* both operands <= 0xFFFF   */
    cmplwi 12, 0xFFFF
    ble   .Lff_prail_store
    li    12, -1                                /* sth keeps the low 16 bits */
.Lff_prail_store:
    lis   11, (MED9_PRSOLL_RAW >> 16)
    sth   12, (MED9_PRSOLL_RAW & 0xFFFF)(11)    /* 0x8031F0                  */
    blr
.Lff_prail_stock:
    lis   11, (MED9_PRSOLL_RAW >> 16)
    sth   30, (MED9_PRSOLL_RAW & 0xFFFF)(11)    /* exactly the stock store    */
    blr
    .size ff_prail_hook, . - ff_prail_hook
