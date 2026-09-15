#!/usr/bin/env python3
# Name the fuel-mass -> injection-time chain found by B6 (issue #14).
# @category MED9
# @runtime PyGhidra
"""b6_injection_symbols.py -- apply agent B6's names and plate comments.

Everything named here is documented, with its evidence, in
``re/findings/injection.md`` (issue #14).  The confidence tag travels in the
plate comment, which is what ``export_symbols.py`` reads, so run this first
and ``export_symbols.py`` afterwards to land the rows in ``re/symbols.csv``.

Run it as a Ghidra post-script::

    ./.venv/bin/python -m pyghidra.ghidra_launch \
        --install-dir "$GHIDRA_INSTALL_DIR" \
        ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
        -process passat_azx_ori.bin -noanalysis \
        -scriptPath ghidra_scripts -postScript b6_injection_symbols.py

or standalone::

    ./.venv/bin/python ghidra_scripts/b6_injection_symbols.py \
        --project-dir /tmp/ghidra_B6 --project-name med9
"""
from __future__ import annotations

import os
import sys

_B6 = ("B6 #14, re/findings/injection.md")

# (cpu_addr, kind, name, plate comment)
#   kind: "func" -> make/rename a function, "label" -> a data label
SYMBOLS = [
    # ---- the conversion itself -------------------------------------------
    (0x0AC370, "func", "rk2ti",
     "VERIFIED-STATIC + VERIFIED-DYNAMIC. u16 rk2ti(inhibit, rk_i, frt, tv, "
     "fcorr): ti_raw = (rk_i*frt)>>9 (the mullw at 0x0AC39C is the flex-fuel "
     "multiplication point); ti = ((ti_raw * KLHDEV(ti_raw) * fcorr) >> 30) "
     "+sat tv, clamped to >= TIMINP(0x5C7328) and to 0 when inhibit != 0. "
     "The FR's RK2TI class, angle-synchronous part of %RKTI. Modelled "
     "bit-exactly by emu/models/injection.py. " + _B6 + " sections 3 and 10"),
    (0x0AC4B8, "func", "fkkvs_func",
     "VERIFIED-STATIC + VERIFIED-DYNAMIC. fkkvs_func(rk_i, dp, *tv, *frt, "
     "use_fkkvs, *fcorr): one axis_search_u16_hint on the dp axis 0x5D3DBE "
     "serves both KLTIKRPR (0x5C72F8) and the dead-time curve (0x5C7310); "
     "frt = min((KRKATE * KLTIKRPR(dp)) >> 14, 0xFFFF); fcorr = "
     "FKKVS(ti_eff, nmot) in Q15, or 0x8000 when use_fkkvs == 0. The FR's "
     "FKKVSFUNC class, time-synchronous part of %RKTI. " + _B6 + " section 4"),
    (0x0AC42C, "func", "rkti_dp_angle",
     "VERIFIED-STATIC + VERIFIED-DYNAMIC. clamp(prist - ((KLPBR(|x|) * "
     "scale) >> 17), 0, 0xFFFF): the angle-dependent differential pressure "
     "across the injector (FR KLPBR/KFPBRA). " + _B6 + " section 5.7"),
    (0x455040, "func", "rkti_pre",
     "VERIFIED-STATIC. Per-injection-type driver of fkkvs_func: picks the "
     "active types out of the bit mask at RAM 0x803094, computes dp = "
     "prist(0x8031DA) - (0x7FEFAE >> 7) or via rkti_dp_angle, and fills the "
     "frt/tv/fcorr triples 0x8030D2..0x8030DE / 0x8030EC..0x8030F2. Called "
     "only from task_1000ms_int (bl at 0x45CCEC). " + _B6 + " sections 4, 6.4"),
    (0x430974, "func", "rkti_pre_start",
     "VERIFIED-STATIC. The start-injection variant of rkti_pre: use_fkkvs=0 "
     "(the FR suppresses FKKVS during start via B_stendes), outputs frt to "
     "0x8030C8 and tv to 0x8030F2. Called only from task_100ms_int (bl at "
     "0x432B00). " + _B6 + " section 6.1"),
    (0x41C3A0, "func", "rksplit",
     "VERIFIED-STATIC. Splits rk (RAM 0x803038) over the active injections "
     "into 0x8030B4/B6 (type 0x2000), 0x8030BA/BC (type 0x400), 0x8030B8 "
     "(homogeneous) and 0x8030BE (start), under the type bits of RAM "
     "0x803092; the split fractions are the bytes 0x801D89 / 0x801D8A. "
     "The FR's %RKSPLIT. " + _B6 + " section 6.1"),
    (0x41C4BC, "func", "aes_ti_out",
     "VERIFIED-STATIC. Calls rk2ti for every active injection, writes the "
     "per-injection ti to 0x8030E4/E6/E8/EA and the total to 0x8030C4 (the "
     "VCDS group 002.3 value), and raises the 'ti is at TIMINP' flag "
     "0x7FEA54/0x7FEA55. The FR's %AES. " + _B6 + " sections 2 and 3"),
    (0x41C730, "func", "aes_ti_out_start",
     "VERIFIED-STATIC. The start-injection counterpart of aes_ti_out, called "
     "from the second engine-synchronous task 0x4224BC. " + _B6),
    (0x41B9C0, "func", "awea_ti_to_angle",
     "VERIFIED-STATIC. Converts ti into a crank angle dwi = (ti * "
     "k_nmot[0x803072]) >> 13 (result 0x803088) and clamps the "
     "start/end-of-injection angles - this, not a ti maximum, is the "
     "injection window limit. Window scalars 0x5D396D / 0x5D396E (both 0x00 "
     "in this dataset), runtime terms 0x7FD28E / 0x7FD290. The FR's %AWEA. "
     + _B6 + " section 8"),
    (0x41AA48, "func", "gk_rk",
     "VERIFIED-STATIC. Gemischkontrolle: base fuel mass * fr (0x802DF8 / "
     "0x802E00, Q15) + fra (0x801D1A) * frm (0x801E36 / 0x801E28, Q15), "
     "minus 0x80315C, then the ZGST per-cylinder factor 0x801D8C[cyl]; "
     "publishes rk at RAM 0x803038 with the sth at 0x41ADD4. " + _B6
     + " section 9"),
    (0x4223B0, "func", "task_segment_a",
     "VERIFIED-STATIC. Engine- (segment-) synchronous ERCOSEK task, TCB "
     "entry 5, id 40, priority 0x0A (B1, re/findings/scheduler.md section 4). "
     "Flat list of 60 argument-less bl; the fuelling run is 0x422478 gk -> "
     "0x42247C rksplit -> 0x422480 aes_ti_out -> 0x422484 awea. The bl at "
     "0x42247C is B6's flex-fuel hook site. " + _B6 + " section 6.2"),
    (0x4224BC, "func", "task_segment_b",
     "VERIFIED-STATIC. The second engine-synchronous ERCOSEK task, TCB entry "
     "6, id 41, priority 0x0A; the only caller of aes_ti_out_start. " + _B6),

    # ---- calibration ------------------------------------------------------
    (0x5D3DBC, "label", "KRKATE_injector_const",
     "VERIFIED-STATIC. u16 = 3858, the injector constant (FR KRKATE, ms/%). "
     "Exactly ONE reference in the image: lhz r3,0x3DBC(r3) at 0x0AC528 "
     "(lis r3,0x5D at 0x0AC51C). Scaling it is the code-free way to fuel a "
     "fixed ethanol blend. " + _B6 + " sections 1 and 6.4"),
    (0x5D3DBE, "label", "axis_dp_injector",
     "VERIFIED-STATIC. {u16 n=12; u16 axis[12]} = 400, 800, 1200, 1800, "
     "2800, 4200, 6120, 8180, 11340, 15000, 20000, 24000. Differential "
     "pressure across the injector; shared by KLTIKRPR and the dead-time "
     "curve (one axis_search_u16_hint at 0x0AC4EC serves both). " + _B6
     + " section 5.1"),
    (0x5C72F8, "label", "KLTIKRPR_flow_vs_dp",
     "VERIFIED-STATIC. 12 x u16 value array on axis_dp_injector: 28344, "
     "20029, 16605, 13558, 10854, 8868, 7369, 6373, 5415, 4710, 4088, 3736. "
     "value * sqrt(dp) is constant to 1 %, i.e. the Bernoulli orifice law - "
     "independent proof that the axis is a pressure and the value a time per "
     "unit fuel mass. FR KLTIKRPR. " + _B6 + " section 5.2"),
    (0x5C7310, "label", "TVUB_deadtime_vs_dp",
     "VERIFIED-STATIC. 12 x s16 value array on axis_dp_injector: 950, 750, "
     "587, 457, 362, 316, 340, 318, 329, 398, 407, 451. The injector dead "
     "time added by rk2ti (the FR's KLTVTSV / TVUB equivalent) - in this "
     "dataset it is a function of dp, NOT of battery voltage. " + _B6
     + " section 5.3"),
    (0x5C71F8, "label", "FKKVS_rail_pulsation",
     "VERIFIED-STATIC. Self-describing 8x8 u16 map, y = ti_eff (500..7000), "
     "x = nmot (3200..24000 = 800..6000 rpm at 1/4 rpm per LSB), values "
     "0x8000..0x8666 = 1.000..1.050 in Q15. Fuel-rail pulsation correction, "
     "read by lookup_2d_u16 at 0x0AC570. FR FKKVS. " + _B6 + " section 5.4"),
    (0x5C729C, "label", "KLHDEV_injector_curve",
     "VERIFIED-STATIC. Self-describing 10-point u16 curve over ti_raw: axis "
     "550, 700, 850, 920, 1000, 1500, 2500, 3500, 4850, 6500; values 32004 "
     "... 31425 = 0.977..1.002 in Q15. The HDEV master curve that linearises "
     "small injection quantities, read by lookup_1d_u16 at 0x0AC3CC. " + _B6
     + " section 5.5"),
    (0x5C7328, "label", "TIMINP_min_inj_time",
     "VERIFIED-STATIC. u16 = 900, the minimum injection time. Applied at "
     "0x0AC410-0x0AC41C in rk2ti and compared against by aes_ti_out to set "
     "the 'ti at minimum' flag. There is no ti MAXIMUM; the ceiling is the "
     "injection window in awea_ti_to_angle. " + _B6 + " sections 5.6 and 8"),
    (0x5C72C6, "label", "KLPBR_backpressure",
     "VERIFIED-STATIC. Self-describing 12-point curve (s16 axis 0x5C72C8, "
     "u16 values 0x5C72E0) read by rkti_dp_angle through lookup_1d_g_s16_u16 "
     "at 0x0AC468: the cylinder back pressure over crank angle. FR KLPBR. "
     + _B6 + " section 5.7"),

    # ---- RAM --------------------------------------------------------------
    (0x803038, "label", "rk_fuel_mass",
     "VERIFIED-STATIC. u16 relative fuel mass for the current segment, after "
     "every correction including ZGST. ONE writer (sth at 0x41ADD4 in gk_rk) "
     "and seven readers, all inside rksplit - which is why it is B6's "
     "flex-fuel multiplication point. Not read by the monitoring path, which "
     "uses the pre-ZGST 0x803030/0x803032/0x803034/0x80303A. " + _B6
     + " sections 6.1 and 7"),
    (0x8030C4, "label", "ti_sum",
     "VERIFIED-STATIC. u32 total injection time of the segment, written by "
     "aes_ti_out; the value behind VCDS measuring-block group 002 field 3 "
     "(variable id 595, handler 0x03EA3C, which clamps at 0xFE01 and divides "
     "by 255). " + _B6 + " section 2"),
    (0x8030E0, "label", "ti_raw",
     "VERIFIED-STATIC. u16 (rk_i * frt) >> 9 before the KLHDEV/FKKVS "
     "correction, written by rk2ti at 0x0AC3C0 and used as the KLHDEV axis "
     "input. " + _B6 + " section 3"),
    (0x8030E2, "label", "ti_eff_fkkvs",
     "VERIFIED-STATIC. u16, the same (rk_i * frt) >> 9 recomputed inside "
     "fkkvs_func at 0x0AC548-0x0AC560 as the FKKVS y-axis input. " + _B6
     + " section 4"),
    (0x8030CE, "label", "dp_injector",
     "VERIFIED-STATIC. u16 differential pressure across the injector, saved "
     "by fkkvs_func at 0x0AC4D0. " + _B6 + " section 4"),
    (0x7FD5B8, "label", "axis_key_dp",
     "VERIFIED-STATIC. u32 axis_search key for axis_dp_injector, kept across "
     "calls as the hint and reused by fkkvs_func for the dead-time curve "
     "(0x0AC4F0 stores it, 0x0AC58C reloads it). " + _B6 + " section 4"),
    (0x7FEE74, "label", "nmot_w",
     "VERIFIED-STATIC for the use, COMMUNITY for the name. u16 engine speed, "
     "1 LSB = 0.25 min^-1: it is the x-axis input of FKKVS at 0x0AC56C and "
     "that axis runs 3200..24000 = 800..6000 rpm. " + _B6 + " section 5.4"),
    (0x8031DA, "label", "prist_w",
     "VERIFIED-STATIC for the use, COMMUNITY for the name. u16 rail-pressure "
     "actual value: the minuend of dp in rkti_pre (0x45505C) and in "
     "rkti_dp_angle (0x0AC460). " + _B6 + " sections 4 and 5.7"),
    (0x803088, "label", "dwi_inj_angle",
     "VERIFIED-STATIC. u16 injection duration expressed as a crank angle, "
     "(ti * 0x803072) >> 13, written by awea_ti_to_angle. The quantity to "
     "log against the injection window when the flex-fuel factor raises ti. "
     + _B6 + " section 8"),
]


def apply(program, monitor=None):
    from ghidra.program.model.symbol import SourceType

    af = program.getAddressFactory().getDefaultAddressSpace()
    fm = program.getFunctionManager()
    listing = program.getListing()
    st = program.getSymbolTable()
    memory = program.getMemory()

    named = skipped = 0
    for cpu, kind, name, comment in SYMBOLS:
        addr = af.getAddress(cpu)
        if not memory.contains(addr):
            print("[b6_injection_symbols] skipped 0x%06X %s: no memory block"
                  % (cpu, name))
            skipped += 1
            continue
        target = None
        if kind == "func":
            func = fm.getFunctionAt(addr)
            if func is None:
                try:
                    from ghidra.app.cmd.function import CreateFunctionCmd
                    CreateFunctionCmd(addr).applyTo(program)
                except Exception as exc:                       # noqa: BLE001
                    print("[b6_injection_symbols] 0x%06X: createFunction "
                          "failed (%s)" % (cpu, exc))
                func = fm.getFunctionAt(addr)
            if func is not None:
                func.setName(name, SourceType.USER_DEFINED)
                target = addr
            else:
                kind = "label"
        if kind == "label":
            st.createLabel(addr, name, SourceType.USER_DEFINED)
            target = addr
        if target is not None:
            listing.setComment(target, 3, comment)             # 3 = PLATE_COMMENT
            named += 1
    print("[b6_injection_symbols] %d symbols named, %d skipped" % (named, skipped))
    return named, skipped


def _run_as_ghidra_script():
    apply(currentProgram, monitor)                             # noqa: F821


def _run_standalone(argv):
    def flag(nm, default=None):
        for i, a in enumerate(argv):
            if a == nm and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(nm + "="):
                return a.split("=", 1)[1]
        return default

    project_dir = os.path.abspath(flag("--project-dir", "ghidra_projects"))
    project_name = flag("--project-name", "med9")
    program_name = flag("--program", "passat_azx_ori.bin")

    import pyghidra
    pyghidra.start(verbose=False)
    from ghidra.base.project import GhidraProject

    project = GhidraProject.openProject(project_dir, project_name, True)
    try:
        program = project.openProgram("/", program_name, False)
        tx = program.startTransaction("b6_injection_symbols")
        try:
            apply(program)
        finally:
            program.endTransaction(tx, True)
        project.save(program)
    finally:
        project.close()
    return 0


def _running_inside_ghidra():
    try:
        currentProgram                                         # noqa: F821,B018
    except NameError:
        return False
    return True


if _running_inside_ghidra():
    _run_as_ghidra_script()
elif __name__ == "__main__":
    sys.exit(_run_standalone(sys.argv[1:]))
