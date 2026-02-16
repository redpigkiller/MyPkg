"""
NumBV — Fixed-point arithmetic for IC design & verification.

Like numpy for matrices, NumBV is the go-to tool for fixed-point numbers.
Wraps ``fxpmath.Fxp`` with a clean, operator-rich API.

**Auto-limit**: All arithmetic results are automatically quantized back
to the left operand's format.  No need to call ``.limit()`` manually.
"""

from __future__ import annotations

from typing import Optional, Union

from fxpmath import Fxp


_Operand = Union["NumBV", int, float]


class NumBV:
    """Fixed-point number with Q-format.

    All arithmetic operators **auto-limit** results to this object's format
    using its ``overflow`` and ``rounding`` settings.

    Usage::

        a = NumBV(16, 8, value=0.75)
        b = a * 1.5          # → NumBV(16, 8, val=1.125), same format
        a *= 2               # in-place, a is now 1.5

        x = NumBV(8, 0, signed=True, value=120)
        y = x + 10           # → NumBV(8, 0, val=127), auto-saturated!
    """

    __slots__ = (
        "_fxp", "_width", "_frac", "_signed", "_mask",
        "_overflow", "_rounding",
    )

    # ── construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        width: int,
        frac: int,
        signed: bool = True,
        value: Union[int, float] = 0,
        overflow: str = "saturate",
        rounding: str = "trunc",
    ) -> None:
        if width <= 0:
            raise ValueError(f"Width must be > 0, got {width}")
        if frac < 0 or frac >= width:
            raise ValueError(f"Frac must be in [0, {width - 1}], got {frac}")

        self._width = width
        self._frac = frac
        self._signed = signed
        self._mask = (1 << width) - 1
        self._overflow = overflow
        self._rounding = rounding

        self._fxp = Fxp(
            value, signed=signed,
            n_word=width, n_frac=frac,
            overflow=overflow, rounding=rounding,
        )

    # ── setting values ────────────────────────────────────────────────────

    def from_val(self, real_value: float) -> None:
        """Set the value from a real number (auto-quantizes)."""
        self._fxp(real_value)

    def from_bits(self, raw: int) -> None:
        """Set the value from raw bit pattern (integer)."""
        self._fxp.set_val(raw, raw=True)

    # ── properties ────────────────────────────────────────────────────────

    @property
    def val(self) -> float:
        """Current real-number value (quantized)."""
        v = self._fxp.astype(float)
        return float(v.item()) if hasattr(v, "item") else float(v)

    @property
    def bits(self) -> int:
        """Raw bit pattern as unsigned Python int."""
        raw = int(self._fxp.val)
        return raw & self._mask if raw < 0 else raw

    @property
    def hex(self) -> str:
        """Hex string, e.g. ``'0x00C0'``."""
        ndigits = (self._width + 3) // 4
        return f"0x{self.bits:0{ndigits}X}"

    @property
    def bin(self) -> str:
        """Binary string, e.g. ``'0b11000000'``."""
        return f"0b{self.bits:0{self._width}b}"

    @property
    def width(self) -> int:
        return self._width

    @property
    def frac(self) -> int:
        return self._frac

    @property
    def signed(self) -> bool:
        return self._signed

    @property
    def overflow(self) -> str:
        return self._overflow

    @property
    def rounding(self) -> str:
        return self._rounding

    # ── internal helpers ──────────────────────────────────────────────────

    def _to_fxp(self, other: _Operand) -> Fxp:
        """Coerce *other* to Fxp."""
        if isinstance(other, NumBV):
            return other._fxp
        return Fxp(other)

    @staticmethod
    def _fxp_to_float(fxp: Fxp) -> float:
        """Extract float from Fxp (handles ndarray scalars)."""
        v = fxp.astype(float)
        return float(v.item()) if hasattr(v, "item") else float(v)

    def _result(self, fxp: Fxp) -> NumBV:
        """Create a new NumBV auto-limited to self's format."""
        return NumBV(
            self._width, self._frac, self._signed,
            value=self._fxp_to_float(fxp),
            overflow=self._overflow, rounding=self._rounding,
        )

    def _inplace(self, fxp: Fxp) -> NumBV:
        """Update self's value in-place from a computed Fxp."""
        self._fxp(self._fxp_to_float(fxp))
        return self

    # ── arithmetic operators (return new NumBV, auto-limited) ─────────────

    def __add__(self, other: _Operand) -> NumBV:
        return self._result(self._fxp + self._to_fxp(other))

    def __radd__(self, other: _Operand) -> NumBV:
        return self._result(self._to_fxp(other) + self._fxp)

    def __sub__(self, other: _Operand) -> NumBV:
        return self._result(self._fxp - self._to_fxp(other))

    def __rsub__(self, other: _Operand) -> NumBV:
        return self._result(self._to_fxp(other) - self._fxp)

    def __mul__(self, other: _Operand) -> NumBV:
        return self._result(self._fxp * self._to_fxp(other))

    def __rmul__(self, other: _Operand) -> NumBV:
        return self._result(self._to_fxp(other) * self._fxp)

    def __truediv__(self, other: _Operand) -> NumBV:
        return self._result(self._fxp / self._to_fxp(other))

    def __rtruediv__(self, other: _Operand) -> NumBV:
        return self._result(self._to_fxp(other) / self._fxp)

    def __neg__(self) -> NumBV:
        return self._result(-self._fxp)

    def __abs__(self) -> NumBV:
        return -self if self.val < 0 else self.copy()

    # ── in-place operators (modify self, auto-limited) ────────────────────

    def __iadd__(self, other: _Operand) -> NumBV:
        return self._inplace(self._fxp + self._to_fxp(other))

    def __isub__(self, other: _Operand) -> NumBV:
        return self._inplace(self._fxp - self._to_fxp(other))

    def __imul__(self, other: _Operand) -> NumBV:
        return self._inplace(self._fxp * self._to_fxp(other))

    def __itruediv__(self, other: _Operand) -> NumBV:
        return self._inplace(self._fxp / self._to_fxp(other))

    def __ilshift__(self, n: int) -> NumBV:
        new_bits = (self.bits << n) & self._mask
        self.from_bits(new_bits)
        return self

    def __irshift__(self, n: int) -> NumBV:
        if self._signed:
            raw = int(self._fxp.val)
            shifted = raw >> n
            val = shifted / (1 << self._frac) if self._frac else float(shifted)
            self._fxp(val)
        else:
            self.from_bits(self.bits >> n)
        return self

    # ── shift operators (return new NumBV, same format) ───────────────────

    def __lshift__(self, n: int) -> NumBV:
        new_bits = (self.bits << n) & self._mask
        r = NumBV(
            self._width, self._frac, self._signed,
            overflow=self._overflow, rounding=self._rounding,
        )
        r.from_bits(new_bits)
        return r

    def __rshift__(self, n: int) -> NumBV:
        if self._signed:
            raw = int(self._fxp.val)
            shifted = raw >> n
            val = shifted / (1 << self._frac) if self._frac else float(shifted)
            r = NumBV(
                self._width, self._frac, self._signed,
                value=val,
                overflow=self._overflow, rounding=self._rounding,
            )
        else:
            r = NumBV(
                self._width, self._frac, self._signed,
                overflow=self._overflow, rounding=self._rounding,
            )
            r.from_bits(self.bits >> n)
        return r

    # ── comparison operators ──────────────────────────────────────────────

    @staticmethod
    def _as_float(other: _Operand) -> float:
        if isinstance(other, NumBV):
            return other.val
        return float(other)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (int, float, NumBV)):
            return self.val == self._as_float(other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        if isinstance(other, (int, float, NumBV)):
            return self.val != self._as_float(other)
        return NotImplemented

    def __lt__(self, other: _Operand) -> bool:
        return self.val < self._as_float(other)

    def __le__(self, other: _Operand) -> bool:
        return self.val <= self._as_float(other)

    def __gt__(self, other: _Operand) -> bool:
        return self.val > self._as_float(other)

    def __ge__(self, other: _Operand) -> bool:
        return self.val >= self._as_float(other)

    def __hash__(self) -> int:
        return id(self)

    # ── bit-level slicing ─────────────────────────────────────────────────

    def __getitem__(self, key: slice) -> int:
        """``bv[high:low]`` — read bits as int (inclusive bounds)."""
        if not isinstance(key, slice):
            raise TypeError("NumBV indexing requires a slice, e.g. bv[15:8]")
        high, low = key.start, key.stop
        if high is None or low is None:
            raise ValueError("Both high and low must be specified: bv[high:low]")
        if high < low:
            raise ValueError(f"high ({high}) must be >= low ({low})")
        if high >= self._width:
            raise ValueError(f"high bit {high} exceeds width {self._width}")
        w = high - low + 1
        return (self.bits >> low) & ((1 << w) - 1)

    # ── resize / copy ─────────────────────────────────────────────────────

    def resize(
        self,
        new_width: int,
        new_frac: int,
        new_signed: Optional[bool] = None,
        overflow: Optional[str] = None,
    ) -> NumBV:
        """Create a new ``NumBV`` with a different Q-format, preserving value.

        Example::

            a = NumBV(16, 8, value=1.5)
            b = a.resize(8, 4)                      # Q8.8 → Q4.4
            c = a.resize(8, 4, overflow='wrap')      # 指定 overflow
        """
        s = new_signed if new_signed is not None else self._signed
        of = overflow or self._overflow
        return NumBV(
            new_width, new_frac, signed=s, value=self.val,
            overflow=of, rounding=self._rounding,
        )

    def copy(self) -> NumBV:
        """Create an independent copy with the current value."""
        c = NumBV.__new__(NumBV)
        c._fxp = self._fxp.copy()
        c._width = self._width
        c._frac = self._frac
        c._signed = self._signed
        c._mask = self._mask
        c._overflow = self._overflow
        c._rounding = self._rounding
        return c

    # ── report ────────────────────────────────────────────────────────────

    def report(self) -> str:
        """Print and return a formatted debug summary."""
        int_bits = self._width - self._frac - (1 if self._signed else 0)
        sign_label = "Signed" if self._signed else "Unsigned"

        lines = [
            f"Value     : {self.val}",
            f"Bits      : {self.hex} ({self.bin})",
            f"Q-Format  : Q{int_bits}.{self._frac} ({sign_label})",
            f"Range     : [{self._fxp.lower}, {self._fxp.upper}]",
            f"Precision : {self._fxp.precision}",
            f"Overflow  : {self._overflow}",
            f"Rounding  : {self._rounding}",
        ]
        text = "\n".join(lines)
        print(text)
        return text

    # ── dunder helpers ────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self._width

    def __int__(self) -> int:
        return self.bits

    def __float__(self) -> float:
        return self.val

    def __bool__(self) -> bool:
        return self.val != 0.0

    def __round__(self, ndigits: int = 0) -> float:
        return round(self.val, ndigits)

    def __repr__(self) -> str:
        return (
            f"NumBV(w={self._width}, f={self._frac}, "
            f"{'s' if self._signed else 'u'}, val={self.val})"
        )

    def __format__(self, spec: str) -> str:
        if spec in ("x", "X", "hex"):
            return self.hex
        if spec in ("b", "bin"):
            return self.bin
        return format(self.val, spec)
