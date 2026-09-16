"""VAG measuring-block display formulas: ``(formula, A, B)`` -> a number.

A measuring block field is three bytes: a **formula id** and two operands the
handler produced.  The firmware contains the ids and the constants but not the
arithmetic -- that lives in the tester.  The expressions below are **community
knowledge**, re-stated here from the public implementations listed in
`logging/README.md` section 5; none of that code is copied, and one of those
projects (jazdw/vag-blocks) is GPL-3.0, which this repository could not carry
anyway (`NOTICE.md`).

Every entry carries its own tag, and the CLI prints it, because their quality
differs a lot:

``crosschecked``
    proved against this ECU's own arithmetic in `re/findings/measuring_vars.md`
    section 7 -- the handler was fed a known RAM byte and the emitted `B`
    matched a scaling derived independently from the firmware.
``community``
    the expression every public implementation agrees on, consistent with what
    this ECU emits, but with no second chain behind it.
absent
    the formula id is not in the table: the raw triple is printed and nothing
    is claimed.  148 of this dataset's 660 variables use 0x10 (a bit field)
    and 90 use 0x25, so this happens often and is not an error.

Usage::

    from med9kwp.vag_formulas import decode
    decode(0x05, 0x0A, 124)     # Decoded(value=24.0, unit='degC', tag='crosschecked')
"""
from __future__ import annotations

from dataclasses import dataclass

CROSSCHECKED = "crosschecked"
COMMUNITY = "community"

#: formula id -> (callable(A, B) -> float, unit, tag, note)
_TABLE: dict[int, tuple] = {
    0x01: (lambda a, b: 0.2 * a * b, "rpm", CROSSCHECKED,
           "measuring_vars.md 7.2; A=0xC8 gives 40 rpm/count"),
    0x05: (lambda a, b: 0.1 * a * (b - 100), "degC", CROSSCHECKED,
           "measuring_vars.md 7.1; exact against tmot = 0.75x-48, "
           "B saturates at 243 = 143 degC"),
    0x02: (lambda a, b: 0.002 * a * b, "%", COMMUNITY, ""),
    0x03: (lambda a, b: 0.002 * a * b, "ms", COMMUNITY, ""),
    0x06: (lambda a, b: 0.001 * a * (b - 128), "V", COMMUNITY, ""),
    0x07: (lambda a, b: 0.01 * a * b, "km/h", COMMUNITY, ""),
    0x0E: (lambda a, b: 0.005 * a * b, "bar", COMMUNITY, ""),
    0x0F: (lambda a, b: 0.01 * a * b, "ms", COMMUNITY, ""),
    0x12: (lambda a, b: a * b / 25.0, "mbar", COMMUNITY, ""),
    0x15: (lambda a, b: a * b / 1000.0, "V", COMMUNITY, ""),
    0x16: (lambda a, b: 0.001 * a * b, "ms", COMMUNITY, ""),
    0x17: (lambda a, b: a * b / 256.0, "%", COMMUNITY, ""),
    0x1A: (lambda a, b: float(b - a), "degC", COMMUNITY, ""),
    0x21: (lambda a, b: (100.0 * b / a) if a else 100.0 * b, "%", COMMUNITY,
           "A is the full-scale count; A=0x85 reads 100 % at B=0x85"),
    0x22: (lambda a, b: (b - 128) * 0.01 * a, "kW", COMMUNITY, ""),
    0x23: (lambda a, b: a * b / 100.0, "l/h", COMMUNITY, ""),
    0x36: (lambda a, b: float((a << 8) | b), "count", COMMUNITY, ""),
    0x37: (lambda a, b: a * b / 200.0, "s", COMMUNITY, ""),
}

#: ids that are containers rather than numbers
BITFIELD = 0x10
TEXT = 0x25
ASCII = 0x11


@dataclass
class Decoded:
    """One decoded measuring-block field."""

    formula: int
    a: int
    b: int
    value: float | None = None
    unit: str = ""
    tag: str = ""
    text: str = ""

    @property
    def empty(self) -> bool:
        """True for the 'not implemented' stub 0x038EC4, which emits 0x25 0 0."""
        return self.formula == TEXT and self.a == 0 and self.b == 0

    def format(self) -> str:
        if self.empty:
            return "-"
        if self.text:
            return self.text
        if self.value is None:
            return f"raw A={self.a:#04x} B={self.b:#04x}"
        return f"{self.value:.3f} {self.unit}".strip()

    def line(self) -> str:
        tag = f" [{self.tag}]" if self.tag else " [unknown formula]"
        return (f"fmt {self.formula:#04x} A={self.a:#04x} B={self.b:#04x} -> "
                f"{self.format()}{tag}")


def decode(formula: int, a: int, b: int) -> Decoded:
    """Decode one `(formula, A, B)` triple; never raises."""
    out = Decoded(formula, a, b)
    if formula == BITFIELD:
        out.text = f"bits {a:08b} {b:08b}"
        out.tag = COMMUNITY
        return out
    if formula in (TEXT, ASCII):
        if formula == TEXT and a == 0 and b == 0:
            out.tag = COMMUNITY
            return out
        printable = "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in (a, b))
        out.text = f"text {printable!r}"
        out.tag = COMMUNITY
        return out
    entry = _TABLE.get(formula)
    if entry is None:
        return out
    fn, unit, tag, _note = entry
    try:
        out.value = float(fn(a, b))
    except ZeroDivisionError:                                # pragma: no cover
        return out
    out.unit = unit
    out.tag = tag
    return out


def table_lines() -> list[str]:
    """Human-readable dump of what this module claims to know."""
    rows = [f"  {BITFIELD:#04x}  bit field (A<<8|B)                  [{COMMUNITY}]",
            f"  {ASCII:#04x}  two ASCII characters                 [{COMMUNITY}]",
            f"  {TEXT:#04x}  text; A=B=0 is 'not implemented'     [{COMMUNITY}]"]
    for fid in sorted(_TABLE):
        _fn, unit, tag, note = _TABLE[fid]
        rows.append(f"  {fid:#04x}  unit {unit:<6} [{tag}]"
                    + (f"  {note}" if note else ""))
    return sorted(rows)
