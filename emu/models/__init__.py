"""Hand-written Python models of individual MED9 ECU functions.

Each model reproduces one decompiled routine **bit-exactly** so that it can be
checked against the real code with the Unicorn harness in `emu/` (and so that a
patch can be designed against a model instead of against the disassembly).

See `emu/models/injection.py` (brief B6, issue #14).
"""
