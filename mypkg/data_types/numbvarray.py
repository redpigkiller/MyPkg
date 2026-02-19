"""
NumBVArray — Vectorized fixed-point arithmetic.

Thin wrapper around ``fxpmath.Fxp`` for batch/array operations.
Use this when you need to process many fixed-point values at once;
for single-value precision work, use :class:`NumBV` instead.

**Use-case split**:

- ``NumBV``      — scalar, bit-level control, hardware register simulation
- ``NumBVArray`` — batch operations, signal processing, large datasets

Usage::

    arr = NumBVArray(16, 8, values=[1.0, 2.0, 3.0])
    result = arr * 2          # vectorized, auto-saturated
    lst = arr.to_numbv_list() # bridge back to NumBV when needed
"""

from __future__ import annotations

from typing import Optional, Union

import numpy as np
from fxpmath import Fxp

from mypkg.data_types.numbv import NumBV


_Operand = Union["NumBVArray", NumBV, int, float, list]


class NumBVArray:
    """Vectorized fixed-point array with Q-format.

    All arithmetic operators **auto-limit** results to this object's format
    using its ``overflow`` and ``rounding`` settings (same semantics as NumBV).

    Usage::

        arr = NumBVArray(16, 8, values=[0.5, 1.0, 1.5])
        result = arr + 0.25          # → NumBVArray, element-wise
        result2 = arr * arr          # → NumBVArray, element-wise
        lst = arr.to_numbv_list()    # → list[NumBV]
    """

    __slots__ = ("_fxp", "_width", "_frac", "_signed", "_mask",
                 "_overflow", "_rounding")

    # ── construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        width: int,
        frac: int,
        signed: bool = True,
        values: Optional[list] = None,
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
            values if values is not None else [],
            signed=signed,
            n_word=width,
            n_frac=frac,
            overflow=overflow,
            rounding=rounding,
        )

    # ── bridge methods ────────────────────────────────────────────────────

    def to_numbv_list(self) -> list[NumBV]:
        """Convert to a list of :class:`NumBV` objects.

        Use only when you need per-element bit-level control.
        For bulk operations, stay in ``NumBVArray``.
        """
        return [
            NumBV(self._width, self._frac, self._signed,
                  value=float(v),
                  overflow=self._overflow, rounding=self._rounding)
            for v in self._fxp.astype(float)
        ]

    @classmethod
    def from_numbv_list(cls, items: list[NumBV]) -> NumBVArray:
        """Build a ``NumBVArray`` from a list of :class:`NumBV` objects.

        All items must share the same format (width, frac, signed).
        """
        if not items:
            raise ValueError("Cannot build NumBVArray from an empty list")
        ref = items[0]
        values = [x.val for x in items]
        return cls(
            ref.width, ref.frac, ref.signed,
            values=values,
            overflow=ref.overflow, rounding=ref.rounding,
        )

    # ── properties ────────────────────────────────────────────────────────

    @property
    def val(self) -> np.ndarray:
        """Current values as a numpy float array."""
        return self._fxp.astype(float)

    @property
    def bits(self) -> np.ndarray:
        """Raw bit patterns as unsigned numpy int array."""
        raw = self._fxp.val.astype(int)
        return np.where(raw < 0, raw & self._mask, raw)

    @property
    def hex(self) -> list[str]:
        """Hex strings for each element, e.g. ``['0x00C0', ...]``."""
        ndigits = (self._width + 3) // 4
        return [f"0x{b:0{ndigits}X}" for b in self.bits]

    @property
    def bin(self) -> list[str]:
        """Binary strings for each element."""
        return [f"0b{b:0{self._width}b}" for b in self.bits]

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
        if isinstance(other, NumBVArray):
            return other._fxp
        if isinstance(other, NumBV):
            return other._fxp
        return Fxp(other)

    def _result(self, fxp: Fxp) -> NumBVArray:
        """Wrap a computed Fxp back into a NumBVArray with self's format."""
        res = NumBVArray.__new__(NumBVArray)
        res._width = self._width
        res._frac = self._frac
        res._signed = self._signed
        res._mask = self._mask
        res._overflow = self._overflow
        res._rounding = self._rounding
        res._fxp = Fxp(
            fxp.astype(float),
            signed=self._signed,
            n_word=self._width,
            n_frac=self._frac,
            overflow=self._overflow,
            rounding=self._rounding,
        )
        return res

    # ── arithmetic operators ──────────────────────────────────────────────

    def __add__(self, other: _Operand) -> NumBVArray:
        return self._result(self._fxp + self._to_fxp(other))

    def __radd__(self, other: _Operand) -> NumBVArray:
        return self._result(self._to_fxp(other) + self._fxp)

    def __sub__(self, other: _Operand) -> NumBVArray:
        return self._result(self._fxp - self._to_fxp(other))

    def __rsub__(self, other: _Operand) -> NumBVArray:
        return self._result(self._to_fxp(other) - self._fxp)

    def __mul__(self, other: _Operand) -> NumBVArray:
        return self._result(self._fxp * self._to_fxp(other))

    def __rmul__(self, other: _Operand) -> NumBVArray:
        return self._result(self._to_fxp(other) * self._fxp)

    def __truediv__(self, other: _Operand) -> NumBVArray:
        return self._result(self._fxp / self._to_fxp(other))

    def __neg__(self) -> NumBVArray:
        return self._result(-self._fxp)

    def __abs__(self) -> NumBVArray:
        return self._result(abs(self._fxp))

    # ── indexing (list/numpy conventions) ────────────────────────────────

    def __getitem__(self, key: Union[int, slice]) -> Union[NumBV, "NumBVArray"]:
        """Element access following list/numpy conventions.

        - ``arr[i]``   → scalar :class:`NumBV` (single element)
        - ``arr[i:j]`` → :class:`NumBVArray` (sub-array, exclusive end)

        .. note::
            This is **element** indexing, not bit slicing.
            For bit-level access, use ``arr[i][msb:lsb]`` (get element first).
        """
        if isinstance(key, int):
            # Scalar access → return NumBV
            raw_val = float(self._fxp[key].astype(float))
            return NumBV(self._width, self._frac, self._signed,
                         value=raw_val,
                         overflow=self._overflow, rounding=self._rounding)
        elif isinstance(key, slice):
            # Slice access → return NumBVArray
            sub_fxp = self._fxp[key]
            return self._result(sub_fxp)
        else:
            raise TypeError(
                f"NumBVArray indices must be int or slice, not {type(key).__name__}. "
                "For bit slicing, get a scalar element first: arr[i][msb:lsb]"
            )

    def __setitem__(self, key: Union[int, slice], value: Union[NumBV, int, float]) -> None:
        """Element assignment following list/numpy conventions.

        - ``arr[i] = v``   → set element i
        - ``arr[i:j] = v`` → broadcast or assign slice

        Accepts :class:`NumBV`, int, or float.
        """
        if isinstance(value, NumBV):
            self._fxp[key] = value.val
        else:
            self._fxp[key] = value

    # ── dunder helpers ────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._fxp)

    def __repr__(self) -> str:
        return (
            f"NumBVArray(w={self._width}, f={self._frac}, "
            f"{'s' if self._signed else 'u'}, n={len(self)}, "
            f"val={list(self.val)})"
        )

    # ── report ────────────────────────────────────────────────────────────

    def report(self) -> str:
        """Print and return a formatted debug summary."""
        int_bits = self._width - self._frac - (1 if self._signed else 0)
        sign_label = "Signed" if self._signed else "Unsigned"
        lines = [
            f"Count     : {len(self)}",
            f"Q-Format  : Q{int_bits}.{self._frac} ({sign_label})",
            f"Range     : [{self._fxp.lower}, {self._fxp.upper}]",
            f"Overflow  : {self._overflow}",
            f"Rounding  : {self._rounding}",
            f"Values    : {list(self.val)}",
        ]
        text = "\n".join(lines)
        print(text)
        return text
