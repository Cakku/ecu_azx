# b3_decompile.py -- PyGhidra post-script (issue #13, agent B3)
#
# Decompile and/or disassemble a list of addresses in the analysed med9 project.
# Usage (headless):
#   ./.venv/bin/python -m pyghidra.ghidra_launch --install-dir "$GHIDRA_INSTALL_DIR" \
#       ghidra.app.util.headless.AnalyzeHeadless <projdir> med9 \
#       -process passat_azx_ori.bin -noanalysis \
#       -scriptPath ghidra_scripts -postScript b3_decompile.py <addr>[,d] <addr> ...
#
# Each arg is an address (hex, 0x optional). Append ':d' to also print the raw
# disassembly listing of the function, ':D' for ONLY disassembly (no decompile),
# ':rN' to instead dump N bytes of raw data at that address as hex.
# ':xN' forces raw linear disassembly of N instrs from the address (creating
#       instructions if Ghidra has not, e.g. code only reached via a pointer).
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.app.cmd.disassemble import DisassembleCommand

prog = currentProgram
fm = prog.getFunctionManager()
af = prog.getAddressFactory()
sp = af.getDefaultAddressSpace()
listing = prog.getListing()

def A(x):
    return sp.getAddress(x)

di = DecompInterface()
di.openProgram(prog)
mon = ConsoleTaskMonitor()

args = getScriptArgs()
for raw in args:
    mode = ''
    addrpart = raw
    if ':' in raw:
        addrpart, mode = raw.split(':', 1)
    val = int(addrpart, 16)
    a = A(val)
    print("\n" + "="*78)
    print("ADDR 0x%X  mode=%r" % (val, mode))
    print("="*78)
    if mode.startswith('r'):
        n = int(mode[1:] or '64')
        b = bytearray(n)
        prog.getMemory().getBytes(a, b)
        for i in range(0, n, 16):
            chunk = b[i:i+16]
            hexs = ' '.join('%02X' % c for c in chunk)
            print("0x%X  %s" % (val+i, hexs))
        continue
    if mode.startswith('x'):
        n = int(mode[1:] or '120')
        ins = listing.getInstructionAt(a)
        if ins is None:
            cmd = DisassembleCommand(a, None, True)
            cmd.applyTo(prog, mon)
            ins = listing.getInstructionAt(a)
        cnt = 0
        while ins is not None and cnt < n:
            fr = ins.getFlows()
            ref = ('  -> ' + ','.join(str(x) for x in fr)) if fr else ''
            print("%s  %-32s%s" % (ins.getAddress(), ins, ref))
            nxt = ins.getNext()
            if nxt is None:
                a2 = ins.getAddress().add(ins.getLength())
                if listing.getInstructionAt(a2) is None:
                    cmd = DisassembleCommand(a2, None, True)
                    cmd.applyTo(prog, mon)
                nxt = listing.getInstructionAt(a2)
            ins = nxt
            cnt += 1
        continue
    f = fm.getFunctionContaining(a)
    if f is None:
        print("(no function contains this address; disassembling 40 instrs)")
        ins = listing.getInstructionAt(a)
        cnt = 0
        while ins is not None and cnt < 40:
            print("%s  %s" % (ins.getAddress(), ins))
            ins = ins.getNext(); cnt += 1
        continue
    print("FUNC %s @ %s  (entry %s)" % (f.getName(), f.getEntryPoint(), f.getBody().getMinAddress()))
    if mode != 'D':
        res = di.decompileFunction(f, 60, mon)
        if res.decompileCompleted():
            print(res.getDecompiledFunction().getC())
        else:
            print("(decompile failed: %s)" % res.getErrorMessage())
    if mode == 'd' or mode == 'D':
        print("--- disassembly ---")
        ins = listing.getInstructionAt(f.getEntryPoint())
        body_end = f.getBody().getMaxAddress()
        while ins is not None and ins.getAddress().compareTo(body_end) <= 0:
            ref = ''
            fr = ins.getFlows()
            if fr:
                ref = '  -> ' + ','.join(str(x) for x in fr)
            print("%s  %-30s%s" % (ins.getAddress(), ins, ref))
            ins = ins.getNext()
