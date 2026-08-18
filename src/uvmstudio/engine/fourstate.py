"""Four-state logic values.

Encoding follows the VPI aval/bval convention, one integer bit per logic bit:

    bval=0, aval=0  -> 0
    bval=0, aval=1  -> 1
    bval=1, aval=0  -> Z
    bval=1, aval=1  -> X

X-propagation policy (matches common simulator practice and the LRM tables):
arithmetic with any unknown operand bit yields all-X; bitwise ops use the
per-bit LRM tables (0&X=0, 1|X=1); ordinary equality/relational comparison
with unknowns yields 1'bx; case equality (===) compares X/Z literally.
"""

from __future__ import annotations

from dataclasses import dataclass


def _mask(width: int) -> int:
    return (1 << width) - 1


@dataclass(frozen=True)
class FourState:
    width: int
    aval: int
    bval: int
    signed: bool = False

    # -- constructors ------------------------------------------------------
    @staticmethod
    def from_int(value: int, width: int, signed: bool = False) -> "FourState":
        return FourState(width, value & _mask(width), 0, signed)

    @staticmethod
    def all_x(width: int, signed: bool = False) -> "FourState":
        m = _mask(width)
        return FourState(width, m, m, signed)

    @staticmethod
    def all_z(width: int, signed: bool = False) -> "FourState":
        return FourState(width, 0, _mask(width), signed)

    @staticmethod
    def from_svint_str(text: str, width: int, signed: bool = False) -> "FourState":
        """Parse slang's SVInt string form: "4'b101x", "16'shbeef", "5"."""
        t = text.strip()
        if "'" not in t:
            neg = t.startswith("-")
            v = int(t.lstrip("-"))
            if neg:
                v = (-v) & _mask(width)
            return FourState(width, v & _mask(width), 0, signed)
        _, rest = t.split("'", 1)
        rest = rest.lower()
        if rest.startswith("s"):
            rest = rest[1:]
            signed = True
        base_ch, digits = rest[0], rest[1:].replace("_", "")
        base_bits = {"b": 1, "o": 3, "h": 4, "d": 0}[base_ch]
        if base_ch == "d":
            return FourState(width, int(digits) & _mask(width), 0, signed)
        aval = bval = 0
        for ch in digits:
            aval <<= base_bits
            bval <<= base_bits
            if ch == "x":
                aval |= _mask(base_bits)
                bval |= _mask(base_bits)
            elif ch == "z" or ch == "?":
                bval |= _mask(base_bits)
            else:
                aval |= int(ch, 16)
        return FourState(width, aval & _mask(width), bval & _mask(width), signed)

    # -- predicates --------------------------------------------------------
    @property
    def has_unknown(self) -> bool:
        return self.bval != 0

    def is_true(self) -> bool | None:
        """LRM truthiness: nonzero known -> True, zero -> False, unknown -> None."""
        known_ones = self.aval & ~self.bval & _mask(self.width)
        if known_ones:
            return True
        if self.bval:
            return None
        return False

    def to_int(self) -> int:
        """Known bits as unsigned int; X/Z bits read as 0 (caller checks has_unknown)."""
        return self.aval & ~self.bval & _mask(self.width)

    def to_signed_int(self) -> int:
        v = self.to_int()
        if self.signed and self.width and (v >> (self.width - 1)) & 1:
            v -= 1 << self.width
        return v

    # -- shape -------------------------------------------------------------
    def resize(self, width: int, signed: bool | None = None) -> "FourState":
        signed = self.signed if signed is None else signed
        if width == self.width:
            return FourState(width, self.aval, self.bval, signed)
        if width < self.width:
            m = _mask(width)
            return FourState(width, self.aval & m, self.bval & m, signed)
        # extension: sign-extend a signed value by its MSB; X/Z extends by its
        # own top bit state, matching the LRM rules for extension of unsized
        # unknown bits
        m_new = _mask(width)
        msb = (self.aval >> (self.width - 1)) & 1 if self.width else 0
        msb_b = (self.bval >> (self.width - 1)) & 1 if self.width else 0
        ext = _mask(width - self.width) << self.width
        aval, bval = self.aval, self.bval
        if msb_b:                    # top bit X or Z -> extend that state
            bval |= ext
            if msb:
                aval |= ext
        elif self.signed and msb:    # signed negative -> ones
            aval |= ext
        return FourState(width, aval & m_new, bval & m_new, signed)

    # -- arithmetic (X-pessimistic) ---------------------------------------
    def _arith(self, other: "FourState", width: int, fn) -> "FourState":
        if self.has_unknown or other.has_unknown:
            return FourState.all_x(width)
        signed = self.signed and other.signed
        a = self.resize(width, self.signed).to_signed_int() if signed else self.to_int()
        b = other.resize(width, other.signed).to_signed_int() if signed else other.to_int()
        return FourState(width, fn(a, b) & _mask(width), 0, signed)

    def add(self, o: "FourState", width: int) -> "FourState":
        return self._arith(o, width, lambda a, b: a + b)

    def sub(self, o: "FourState", width: int) -> "FourState":
        return self._arith(o, width, lambda a, b: a - b)

    def mul(self, o: "FourState", width: int) -> "FourState":
        return self._arith(o, width, lambda a, b: a * b)

    def div(self, o: "FourState", width: int) -> "FourState":
        if o.has_unknown or self.has_unknown or o.to_int() == 0:
            return FourState.all_x(width)          # divide by zero -> X (LRM)
        return self._arith(o, width, lambda a, b: int(a / b) if (a < 0) != (b < 0) and a % b else a // b)

    def mod(self, o: "FourState", width: int) -> "FourState":
        if o.has_unknown or self.has_unknown or o.to_int() == 0:
            return FourState.all_x(width)
        # LRM: result takes the sign of the first operand
        def _m(a: int, b: int) -> int:
            r = abs(a) % abs(b)
            return -r if a < 0 else r
        return self._arith(o, width, _m)

    # -- bitwise (per-bit LRM tables) -------------------------------------
    def bit_and(self, o: "FourState", width: int) -> "FourState":
        s, t = self.resize(width), o.resize(width)
        known0 = (~s.aval & ~s.bval) | (~t.aval & ~t.bval)   # 0 & anything = 0
        bval = (s.bval | t.bval) & ~known0                    # else unknown -> X
        aval = ((s.aval & t.aval) & ~s.bval & ~t.bval) | bval
        return FourState(width, aval & _mask(width), bval & _mask(width))

    def bit_or(self, o: "FourState", width: int) -> "FourState":
        s, t = self.resize(width), o.resize(width)
        known1 = (s.aval & ~s.bval) | (t.aval & ~t.bval)
        bval = (s.bval | t.bval) & ~known1
        aval = known1 | bval
        return FourState(width, aval & _mask(width), bval & _mask(width))

    def bit_xor(self, o: "FourState", width: int) -> "FourState":
        s, t = self.resize(width), o.resize(width)
        bval = s.bval | t.bval
        aval = ((s.aval ^ t.aval) & ~bval) | bval
        return FourState(width, aval & _mask(width), bval & _mask(width))

    def bit_not(self) -> "FourState":
        m = _mask(self.width)
        aval = (~self.aval & m & ~self.bval) | self.bval
        return FourState(self.width, aval, self.bval, self.signed)

    # -- reductions --------------------------------------------------------
    def reduce_and(self) -> "FourState":
        m = _mask(self.width)
        if (~self.aval & ~self.bval) & m:
            return FourState(1, 0, 0)          # any known 0 -> 0
        if self.bval:
            return FourState.all_x(1)
        return FourState(1, 1, 0)

    def reduce_or(self) -> "FourState":
        if self.aval & ~self.bval:
            return FourState(1, 1, 0)          # any known 1 -> 1
        if self.bval:
            return FourState.all_x(1)
        return FourState(1, 0, 0)

    def reduce_xor(self) -> "FourState":
        if self.bval:
            return FourState.all_x(1)
        return FourState(1, bin(self.aval).count("1") & 1, 0)

    # -- comparison --------------------------------------------------------
    def eq(self, o: "FourState") -> "FourState":
        w = max(self.width, o.width)
        s, t = self.resize(w), o.resize(w)
        if s.bval or t.bval:
            return FourState.all_x(1)
        return FourState(1, int(s.aval == t.aval), 0)

    def case_eq(self, o: "FourState") -> "FourState":
        w = max(self.width, o.width)
        s, t = self.resize(w), o.resize(w)
        return FourState(1, int(s.aval == t.aval and s.bval == t.bval), 0)

    def _rel(self, o: "FourState", fn) -> "FourState":
        if self.has_unknown or o.has_unknown:
            return FourState.all_x(1)
        signed = self.signed and o.signed
        w = max(self.width, o.width)
        a = self.resize(w).to_signed_int() if signed else self.to_int()
        b = o.resize(w).to_signed_int() if signed else o.to_int()
        return FourState(1, int(fn(a, b)), 0)

    def lt(self, o):  return self._rel(o, lambda a, b: a < b)
    def le(self, o):  return self._rel(o, lambda a, b: a <= b)
    def gt(self, o):  return self._rel(o, lambda a, b: a > b)
    def ge(self, o):  return self._rel(o, lambda a, b: a >= b)

    # -- shifts ------------------------------------------------------------
    def shl(self, o: "FourState", width: int) -> "FourState":
        if o.has_unknown:
            return FourState.all_x(width)
        n = o.to_int()
        m = _mask(width)
        return FourState(width, (self.aval << n) & m, (self.bval << n) & m, self.signed)

    def shr(self, o: "FourState", width: int, arithmetic: bool = False) -> "FourState":
        if o.has_unknown:
            return FourState.all_x(width)
        n = o.to_int()
        s = self.resize(width)
        if arithmetic and self.signed and self.width:
            a = s.to_signed_int() >> n
            if s.bval:
                return FourState.all_x(width)
            return FourState(width, a & _mask(width), 0, True)
        return FourState(width, s.aval >> n, s.bval >> n, self.signed)

    # -- structure ---------------------------------------------------------
    def concat(self, o: "FourState") -> "FourState":
        w = self.width + o.width
        return FourState(
            w, (self.aval << o.width) | o.aval, (self.bval << o.width) | o.bval
        )

    def select_bit(self, index: int) -> "FourState":
        if index < 0 or index >= self.width:
            return FourState.all_x(1)          # out-of-range select -> X (LRM)
        return FourState(1, (self.aval >> index) & 1, (self.bval >> index) & 1)

    def select_range(self, msb: int, lsb: int) -> "FourState":
        w = msb - lsb + 1
        if lsb < 0 or msb >= self.width:
            return FourState.all_x(w)
        m = _mask(w)
        return FourState(w, (self.aval >> lsb) & m, (self.bval >> lsb) & m)

    # -- formatting --------------------------------------------------------
    def bit_char(self, i: int) -> str:
        a, b = (self.aval >> i) & 1, (self.bval >> i) & 1
        return "01zx"[a + 2 * b]

    def to_bin(self) -> str:
        return "".join(self.bit_char(i) for i in range(self.width - 1, -1, -1))

    def format(self, spec: str) -> str:
        """Render for $display: spec in {d, 0d, b, 0b, h, 0h, x, 0x}."""
        base = spec.lstrip("0") or "d"
        if base in ("d",):
            if self.has_unknown:
                return "x" if self.bval == _mask(self.width) else "X"
            return str(self.to_signed_int() if self.signed else self.to_int())
        if base in ("b",):
            s = self.to_bin()
            return s.lstrip("0") or "0" if spec.startswith("0") else s
        if base in ("h", "x"):
            out = []
            for nib in range(( self.width + 3 ) // 4 - 1, -1, -1):
                a = (self.aval >> (nib * 4)) & 0xF
                b = (self.bval >> (nib * 4)) & 0xF
                if b == 0xF and a == 0xF: out.append("x")
                elif b == 0xF and a == 0: out.append("z")
                elif b: out.append("X" if a else "Z")
                else: out.append(format(a, "x"))
            s = "".join(out)
            return (s.lstrip("0") or "0") if spec.startswith("0") else s
        raise ValueError(f"unsupported format spec %{spec}")

    def __str__(self) -> str:
        return f"{self.width}'b{self.to_bin()}"
