"""
NumBV — Fixed-point arithmetic for DSP / signal processing engineers.

Like numpy for matrices, NumBV is the go-to tool for fixed-point numbers.
Built on pure numpy with no external fixed-point dependencies.

A single ``NumBV`` class handles both scalar and array operations.
Use factory functions to construct instances — do not call ``NumBV()`` directly.

**Auto-quantize**: All arithmetic results are automatically quantized back
to the left operand's format. No manual clamping needed.

**numpy interop**: All numpy ufuncs (``np.sum``, ``np.sin``, ...) work
directly on ``NumBV`` via ``__array_ufunc__``. Results are quantized back
to the input format.

Factory functions (preferred user-facing API)::

    import numbv as nbv

    a = nbv.zeros(16, 8)                        # scalar Q16.8, value = 0
    b = nbv.full(16, 8, 1.5, n=1024)           # array, 1024 × 1.5
    c = nbv.array(16, 8, np.linspace(0, 1, 512))
    d = nbv.from_bits(16, 8, [0x0100, 0x0200]) # from raw bit patterns
    e = nbv.zeros_like(a)                       # same format as a

Global config::

    nbv.config.overflow          = 'saturate'  # default
    nbv.config.rounding          = 'trunc'     # default
    nbv.config.on_precision_loss = 'silent'    # 'silent' | 'warn' | 'error'
"""

from __future__ import annotations

import warnings
from typing import Literal

import numpy as np


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class _Config:
    """Module-level configuration for NumBV defaults.

    Per-instance ``overflow`` and ``rounding`` always take precedence.
    """

    __slots__ = ("overflow", "rounding", "on_precision_loss")

    def __init__(self) -> None:
        self.overflow: Literal["saturate", "wrap"] = "saturate"
        self.rounding: Literal["trunc", "round"] = "trunc"
        self.on_precision_loss: Literal["silent", "warn", "error"] = "silent"

    def __repr__(self) -> str:
        return (
            f"NumBVConfig(overflow={self.overflow!r}, "
            f"rounding={self.rounding!r}, "
            f"on_precision_loss={self.on_precision_loss!r})"
        )


config = _Config()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _raw_to_float(raw: np.ndarray, frac: int) -> np.ndarray:
    """Convert raw int64 fixed-point array back to float64."""
    return raw.astype(np.float64) / (1 << frac)



def _precision_loss_check(
    original: np.ndarray,
    quantized: np.ndarray,
    width: int,
    frac: int,
) -> None:
    """Emit warning or error if quantization changed the value."""
    if config.on_precision_loss == "silent":
        return
    if not np.allclose(original, _raw_to_float(quantized, frac), atol=0):
        msg = (
            f"Precision loss during quantization to Q{width}.{frac}: "
            f"original range [{float(np.min(original)):.6g}, "
            f"{float(np.max(original)):.6g}]"
        )
        if config.on_precision_loss == "error":
            raise ValueError(msg)
        warnings.warn(msg, UserWarning, stacklevel=3)


# ---------------------------------------------------------------------------
# Raw integer operations (pure functions — numba-friendly)
# ---------------------------------------------------------------------------
# These functions operate exclusively on int64 ndarrays. They do NOT
# depend on any class state and can be decorated with @numba.njit in the
# future by changing `overflow: str` → `overflow_is_wrap: bool`.


def _clip(
    raw: np.ndarray,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    overflow: str,
    signed: bool,
) -> np.ndarray:
    """Apply overflow handling (saturate or wrap) to raw int64 values."""
    if overflow == "wrap":
        raw = raw & mask
        if signed:
            raw = np.where(raw > hi, raw - wrap_offset, raw)
    else:
        raw = np.clip(raw, lo, hi)
    return raw


def _raw_add(
    a: np.ndarray, b: np.ndarray,
    lo: int, hi: int, mask: int, wrap_offset: int,
    overflow: str, signed: bool,
) -> np.ndarray:
    """Add two same-format raw arrays. No float conversion."""
    return _clip(a + b, lo, hi, mask, wrap_offset, overflow, signed)


def _raw_sub(
    a: np.ndarray, b: np.ndarray,
    lo: int, hi: int, mask: int, wrap_offset: int,
    overflow: str, signed: bool,
) -> np.ndarray:
    """Subtract two same-format raw arrays. No float conversion."""
    return _clip(a - b, lo, hi, mask, wrap_offset, overflow, signed)


def _raw_mul(
    a: np.ndarray, b: np.ndarray,
    frac: int, rounding: str,
    lo: int, hi: int, mask: int, wrap_offset: int,
    overflow: str, signed: bool,
) -> np.ndarray:
    """Multiply two same-format raw arrays.

    ``(a * b) >> frac`` with rounding, then overflow clip.
    """
    product = a.astype(np.int64) * b.astype(np.int64)
    if rounding == "round" and frac > 0:
        product += 1 << (frac - 1)
    result = product >> frac
    return _clip(result, lo, hi, mask, wrap_offset, overflow, signed)


def _raw_div(
    a: np.ndarray, b: np.ndarray,
    frac: int, rounding: str,
    lo: int, hi: int, mask: int, wrap_offset: int,
    overflow: str, signed: bool,
) -> np.ndarray:
    """Divide two same-format raw arrays.

    ``(a << frac) / b`` (integer division with rounding), then overflow clip.
    """
    # Guard against division by zero — replace 0 with 1 (result will be
    # clipped to lo/hi by overflow handling anyway).
    safe_b = np.where(b == 0, np.int64(1), b)
    numerator = a.astype(np.int64) << frac
    if rounding == "round":
        # round half-up: add half of |b| before dividing
        half_b = np.abs(safe_b) >> 1
        numerator = np.where(numerator >= 0, numerator + half_b, numerator - half_b)
    # Use floor division for truncation toward negative infinity,
    # matching hardware arithmetic right-shift semantics.
    result = np.where(b == 0, np.where(a >= 0, np.int64(hi), np.int64(lo)), numerator // safe_b)
    return _clip(result, lo, hi, mask, wrap_offset, overflow, signed)


def _raw_neg(
    a: np.ndarray,
    lo: int, hi: int, mask: int, wrap_offset: int,
    overflow: str, signed: bool,
) -> np.ndarray:
    """Negate raw array. No float conversion."""
    return _clip(-a, lo, hi, mask, wrap_offset, overflow, signed)


# ---------------------------------------------------------------------------
# NumBV
# ---------------------------------------------------------------------------


class NumBV:
    """Fixed-point number with Q-format — scalar or array.

    Do **not** instantiate directly. Use the module-level factory functions::

        import numbv as nbv

        a = nbv.zeros(16, 8)                  # scalar
        b = nbv.zeros(16, 8, n=256)           # array
        c = nbv.array(16, 8, [0.5, 1.0])     # from list

    All arithmetic operators auto-quantize results back to the left
    operand's format. Mixed signed/unsigned operations raise ``TypeError``.
    """

    __slots__ = (
        "_raw",
        "_width",
        "_frac",
        "_signed",
        "_overflow",
        "_rounding",
        "_scale",
        "_mask",
        "_wrap_offset",
        "_lo",
        "_hi",
    )

    # -- construction (internal) -------------------------------------------

    def __init__(
        self,
        raw: np.ndarray,
        width: int,
        frac: int,
        signed: bool,
        overflow: Literal["saturate", "wrap"],
        rounding: Literal["trunc", "round"],
    ) -> None:
        if width <= 0:
            raise ValueError(f"width must be > 0, got {width}")
        if not (0 <= frac < width):
            raise ValueError(f"frac must be in [0, {width - 1}], got {frac}")

        self._width = width
        self._frac = frac
        self._signed = signed
        self._overflow: Literal["saturate", "wrap"] = overflow
        self._rounding: Literal["trunc", "round"] = rounding

        # Pre-compute data
        self._scale = 1 << self._frac
        self._mask = (1 << self._width) - 1
        self._wrap_offset = 1 << self._width
        self._lo = -(1 << (self._width - 1)) if self._signed else 0
        self._hi = (
            (1 << (self._width - 1)) - 1 if self._signed else (1 << self._width) - 1
        )

        # int64 ndarray, shape () or (n,)
        self._raw: np.ndarray = self._quantize(raw)

    # -- internal helpers --------------------------------------------------

    def _quantize(self, values: np.ndarray|int|float) -> np.ndarray:
        """Quantize real-valued *values* into a fixed-point raw integer array.

        Returns a numpy int64 array of raw (signed two's complement) integers.
        """
        # Shift fractional bits into the integer domain using the precomputed scale.
        # This avoids recalculating (1 << frac) on every function call.
        scaled = values * self._scale

        if self._rounding == "round":
            # round: Add 0.5 before flooring for standard half-up rounding.
            raw = np.floor(scaled + 0.5).astype(np.int64)
        else:
            # trunc: Simply dropping the fractional bits.
            raw = np.floor(scaled).astype(np.int64)

        if self._overflow == "wrap":
            # wrap: Simulate hardware wrapping via bitwise AND with a precomputed width mask.
            raw &= self._mask

            if self._signed:
                # Perform sign extension for signed integers. If the masked value exceeds
                # the max positive bound (_hi), subtract 2^width (_wrap_offset) to map to negative.
                raw = np.where(raw > self._hi, raw - self._wrap_offset, raw)
        else:
            # saturate: Clamp the values strictly between precomputed bounds (_lo and _hi) to prevent overflow.
            raw = np.clip(raw, self._lo, self._hi)

        return raw

    def _clip_raw(self, raw: np.ndarray) -> np.ndarray:
        """Apply overflow (clip or wrap) to raw int64. Thin instance-level wrapper."""
        return _clip(raw, self._lo, self._hi, self._mask, self._wrap_offset,
                     self._overflow, self._signed)

    def _other_raw(self, other: NumBV | int | float) -> np.ndarray:
        """Get *other*'s value as raw int64 in **self's** format.

        - Same-format ``NumBV``: returns ``other._raw`` directly (zero cost).
        - Different-format ``NumBV``: converts via float (unavoidable).
        - ``int``/``float``: quantize once.
        """
        if isinstance(other, NumBV):
            if other._signed != self._signed:
                raise TypeError(
                    f"Mixed signed/unsigned operation: "
                    f"{'signed' if self._signed else 'unsigned'} "
                    f"vs {'signed' if other._signed else 'unsigned'}. "
                    f"Use cast(signed=...) to align first."
                )
            if other._frac == self._frac and other._width == self._width:
                return other._raw  # same format — zero cost
            # different format: convert via float (unavoidable)
            return self._quantize(other._raw.astype(np.float64) / other._scale)
        return self._quantize(np.asarray(other, dtype=np.float64))

    def _is_scalar(self) -> bool:
        return self._raw.ndim == 0

    @classmethod
    def _from_raw(cls, raw: np.ndarray, ref: "NumBV") -> "NumBV":
        """Internal: wrap an already-quantized int64 ndarray into a new NumBV.

        Bypasses ``__init__`` so the raw bits are **not** re-quantized.
        Use only when ``raw`` is guaranteed to be a valid int64 array in ref's format.
        """
        obj = object.__new__(cls)
        obj._width = ref._width
        obj._frac = ref._frac
        obj._signed = ref._signed
        obj._overflow = ref._overflow
        obj._rounding = ref._rounding
        obj._scale = ref._scale
        obj._mask = ref._mask
        obj._wrap_offset = ref._wrap_offset
        obj._lo = ref._lo
        obj._hi = ref._hi
        obj._raw = raw
        return obj

    # -- properties --------------------------------------------------------

    @property
    def val(self) -> float | np.ndarray:
        """Current value(s). ``float`` for scalar, ``np.ndarray`` for array."""
        v = self._raw.astype(np.float64) / self._scale
        return float(v) if self._is_scalar() else v

    @property
    def bits(self) -> int | np.ndarray:
        """Unsigned bit pattern(s). ``int`` for scalar, ``np.ndarray`` for array."""
        v = self._raw.astype(np.int64) & self._mask
        return int(v) if self._is_scalar() else v

    @property
    def hex(self) -> str | list[str]:
        """Hex string(s), e.g. ``'0x00C0'``."""
        ndigits = (self._width + 3) // 4
        values = self.bits
        if isinstance(values, int):
            return f"0x{int(values):0{ndigits}X}"
        return [f"0x{b:0{ndigits}X}" for b in values]

    @property
    def bin(self) -> str | list[str]:
        """Binary string(s), e.g. ``'0b1100...'``."""
        values = self.bits
        if isinstance(values, int):
            return f"0b{int(values):0{self._width}b}"
        return [f"0b{b:0{self._width}b}" for b in values]

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
    def overflow(self) -> Literal["saturate", "wrap"]:
        return self._overflow

    @property
    def rounding(self) -> Literal["trunc", "round"]:
        return self._rounding

    @property
    def shape(self) -> tuple:
        """numpy-style shape. ``()`` for scalar, ``(n,)`` for array."""
        return self._raw.shape

    @property
    def size(self) -> int:
        """Total number of elements."""
        return int(self._raw.size)

    # -- arithmetic operators (raw int64 fast path) ------------------------

    def _arith_args(self):
        """Shorthand for the overflow-handling arguments."""
        return (self._lo, self._hi, self._mask, self._wrap_offset,
                self._overflow, self._signed)

    def __add__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_add(self._raw, self._other_raw(other), *self._arith_args()),
            self,
        )

    def __radd__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_add(self._other_raw(other), self._raw, *self._arith_args()),
            self,
        )

    def __sub__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_sub(self._raw, self._other_raw(other), *self._arith_args()),
            self,
        )

    def __rsub__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_sub(self._other_raw(other), self._raw, *self._arith_args()),
            self,
        )

    def __mul__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_mul(self._raw, self._other_raw(other),
                     self._frac, self._rounding, *self._arith_args()),
            self,
        )

    def __rmul__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_mul(self._other_raw(other), self._raw,
                     self._frac, self._rounding, *self._arith_args()),
            self,
        )

    def __truediv__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_div(self._raw, self._other_raw(other),
                     self._frac, self._rounding, *self._arith_args()),
            self,
        )

    def __rtruediv__(self, other: int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_div(self._other_raw(other), self._raw,
                     self._frac, self._rounding, *self._arith_args()),
            self,
        )

    def __neg__(self) -> NumBV:
        return NumBV._from_raw(
            _raw_neg(self._raw, *self._arith_args()),
            self,
        )

    def __abs__(self) -> NumBV:
        return NumBV._from_raw(
            self._clip_raw(np.abs(self._raw)),
            self,
        )

    # -- in-place operators (raw int64 fast path) --------------------------

    def __iadd__(self, other: NumBV | int | float) -> NumBV:
        self._raw = _raw_add(self._raw, self._other_raw(other), *self._arith_args())
        return self

    def __isub__(self, other: NumBV | int | float) -> NumBV:
        self._raw = _raw_sub(self._raw, self._other_raw(other), *self._arith_args())
        return self

    def __imul__(self, other: NumBV | int | float) -> NumBV:
        self._raw = _raw_mul(self._raw, self._other_raw(other),
                             self._frac, self._rounding, *self._arith_args())
        return self

    def __itruediv__(self, other: NumBV | int | float) -> NumBV:
        self._raw = _raw_div(self._raw, self._other_raw(other),
                             self._frac, self._rounding, *self._arith_args())
        return self

    # -- comparison operators (raw int64 fast path) ------------------------

    def __eq__(self, other: object) -> bool | np.ndarray:
        """Element-wise equality. Same-format compares raw integers directly."""
        if isinstance(other, NumBV):
            if self._frac == other._frac and self._width == other._width:
                return np.equal(self._raw, other._raw)
            return np.equal(self.val, other.val)
        if isinstance(other, (int, float)):
            return np.equal(self._raw, self._other_raw(other))
        return NotImplemented

    def __lt__(self, other: NumBV | int | float) -> bool | np.ndarray:
        """Element-wise less-than. Scalar → ``bool``, array → ``np.ndarray``."""
        return np.less(self._raw, self._other_raw(other))

    def __le__(self, other: NumBV | int | float) -> bool | np.ndarray:
        """Element-wise less-or-equal. Scalar → ``bool``, array → ``np.ndarray``."""
        return np.less_equal(self._raw, self._other_raw(other))

    def __gt__(self, other: NumBV | int | float) -> bool | np.ndarray:
        """Element-wise greater-than. Scalar → ``bool``, array → ``np.ndarray``."""
        return np.greater(self._raw, self._other_raw(other))

    def __ge__(self, other: NumBV | int | float) -> bool | np.ndarray:
        """Element-wise greater-or-equal. Scalar → ``bool``, array → ``np.ndarray``."""
        return np.greater_equal(self._raw, self._other_raw(other))


    # -- numpy interop -----------------------------------------------------

    def __array__(self, dtype=None) -> np.ndarray:
        """Allow numpy to consume NumBV as a float array."""
        v = self._raw.astype(np.float64) / self._scale
        return v.astype(dtype) if dtype is not None else v

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        """Intercept all numpy ufuncs. Results are quantized back to self's format.

        Precision loss behaviour is controlled by ``nbv.config.on_precision_loss``.
        For ufuncs that may lose precision (e.g. ``np.sin`` on a low-frac format),
        cast to a higher-frac format first::

            np.sin(a.cast(16, 14))
        """
        # Resolve all inputs to float arrays (ufuncs require float64).
        float_inputs = []
        ref = None
        for inp in inputs:
            if isinstance(inp, NumBV):
                if ref is None:
                    ref = inp
                # Use precomputed _scale — avoids recomputing (1 << frac)
                float_inputs.append(inp._raw.astype(np.float64) / inp._scale)
            else:
                float_inputs.append(np.asarray(inp, dtype=np.float64))

        if ref is None:
            return NotImplemented

        result = getattr(ufunc, method)(*float_inputs, **kwargs)

        # Some ufuncs return multiple outputs (e.g. np.modf, np.frexp).
        if isinstance(result, tuple):
            return tuple(
                NumBV(r, ref._width, ref._frac, ref._signed, ref._overflow, ref._rounding)
                for r in result
            )

        out = NumBV(result, ref._width, ref._frac, ref._signed, ref._overflow, ref._rounding)
        # Skip the precision check entirely when silent (default) — no function call overhead.
        if config.on_precision_loss != "silent":
            _precision_loss_check(result, out._raw, ref._width, ref._frac)
        return out

    # -- array indexing ----------------------------------------------------

    def __getitem__(self, key: int|slice) -> NumBV:
        """Element access following numpy conventions.

        - ``arr[i]``   → scalar ``NumBV`` (view)
        - ``arr[i:j]`` → array ``NumBV`` (view, exclusive end)

        Raises ``TypeError`` on scalar ``NumBV``.
        """
        if self._is_scalar():
            raise TypeError(
                "NumBV scalar does not support indexing. "
                "Use .val to get the float value."
            )
        raw = self._raw[key]
        return NumBV._from_raw(raw, self)

    def __setitem__(
        self,
        key: int | slice,
        value: NumBV | int | float,
    ) -> None:
        """Element assignment. Quantizes value to self's format."""
        if self._is_scalar():
            raise TypeError("Cannot index-assign to a scalar NumBV.")
        self._raw[key] = self._other_raw(value)

    def __len__(self) -> int:
        """Number of elements. Scalar returns ``1`` (like MATLAB ``length``)."""
        if self._is_scalar():
            return 1
        return len(self._raw)

    # -- bit-level operations (scalar only) --------------------------------

    def _require_scalar(self, op: str) -> None:
        if not self._is_scalar():
            raise TypeError(
                f"{op} requires a scalar NumBV. Index the array first: arr[i].{op}(...)"
            )

    def get_bits(self, high: int, low: int) -> int:
        """Read bits ``[high:low]`` inclusive. Returns ``int``.

        Bit indices are 0-based from LSB. ``high`` must be >= ``low``.

        Usage::

            a.get_bits(15, 8)   # read upper byte of 16-bit value
        """
        self._require_scalar("get_bits")
        if high < low:
            raise ValueError(f"high ({high}) must be >= low ({low})")
        if high >= self._width:
            raise ValueError(f"high bit {high} exceeds width {self._width}")
        w = high - low + 1
        return (int(self.bits) >> low) & ((1 << w) - 1)

    def get_bit(self, pos: int) -> bool:
        """Read single bit at position ``pos``. Returns ``bool``."""
        self._require_scalar("get_bit")
        if not 0 <= pos < self._width:
            raise ValueError(f"Bit {pos} out of range [0, {self._width - 1}]")
        return bool((int(self.bits) >> pos) & 1)

    def set_bits(self, high: int, low: int, val: int) -> None:
        """Write ``val`` into bits ``[high:low]`` inclusive. In-place."""
        self._require_scalar("set_bits")
        if high < low:
            raise ValueError(f"high ({high}) must be >= low ({low})")
        if high >= self._width:
            raise ValueError(f"high bit {high} exceeds width {self._width}")
        w = high - low + 1
        mask = (1 << w) - 1
        full_mask = (1 << self._width) - 1
        new_bits = (int(self.bits) & (~(mask << low) & full_mask)) | (
            (val & mask) << low
        )
        self.from_bits(new_bits)

    def set_bit(self, pos: int, value: bool) -> None:
        """Set single bit at ``pos`` to ``True`` (1) or ``False`` (0). In-place."""
        self._require_scalar("set_bit")
        if not 0 <= pos < self._width:
            raise ValueError(f"Bit {pos} out of range [0, {self._width - 1}]")
        if value:
            self.from_bits(int(self.bits) | (1 << pos))
        else:
            self.from_bits(int(self.bits) & ~(1 << pos))

    def from_bits(self, raw: int) -> None:
        """Set value from raw unsigned integer bit pattern. In-place.

        The raw value is masked to ``width`` bits and interpreted as a
        two's complement integer if ``signed=True``.
        """
        self._require_scalar("from_bits")
        full_mask = (1 << self._width) - 1
        raw &= full_mask
        # Two's complement reinterpret for signed
        if self._signed and (raw & (1 << (self._width - 1))):
            raw -= 1 << self._width
        self._raw = np.array(raw, dtype=np.int64)

    # -- format conversion -------------------------------------------------

    def cast(
        self,
        width: int,
        frac: int,
        signed: bool | None = None,
        overflow: str | None = None,
    ) -> "NumBV":
        """Convert to a different Q-format. Value is preserved subject to overflow.

        Usage::

            b = a.cast(8, 4)                    # Q16.8 → Q8.4
            c = a.cast(8, 4, signed=False)      # change signedness
            d = a.cast(16, 14)                  # upcast before np.sin
        """
        s = signed if signed is not None else self._signed
        of = overflow if overflow is not None else self._overflow
        # Convert via float using precomputed _scale.
        return NumBV(
            self._raw.astype(np.float64) / self._scale,
            width, frac, s, of, self._rounding,
        )

    def copy(self) -> "NumBV":
        """Return an independent copy with the same format and value."""
        return NumBV._from_raw(self._raw.copy(), self)

    # -- debug -------------------------------------------------------------

    def report(self) -> str:
        """Print and return a formatted debug summary."""
        int_bits = self._width - self._frac - (1 if self._signed else 0)
        sign_label = "Signed" if self._signed else "Unsigned"
        scale = 1 << self._frac

        if self._signed:
            lo = -(1 << (self._width - 1)) / scale
            hi = ((1 << (self._width - 1)) - 1) / scale
        else:
            lo = 0.0
            hi = ((1 << self._width) - 1) / scale

        precision = 1.0 / scale

        lines = [
            f"Q-Format  : Q{int_bits}.{self._frac} ({sign_label})",
            f"Range     : [{lo}, {hi}]",
            f"Precision : {precision}",
            f"Overflow  : {self._overflow}",
            f"Rounding  : {self._rounding}",
        ]

        if self._is_scalar():
            lines.insert(0, f"Value     : {self.val}")
            lines.insert(1, f"Bits      : {self.hex} ({self.bin})")
        else:
            lines.insert(0, f"Shape     : {self.shape}")
            lines.insert(1, f"Values    : {self.val}")

        text = "\n".join(lines)
        print(text)
        return text

    # -- dunder helpers ----------------------------------------------------

    def __float__(self) -> float:
        if not self._is_scalar():
            raise TypeError("Cannot convert array NumBV to float. Use .val instead.")
        return float(self.val)

    def __int__(self) -> int:
        if not self._is_scalar():
            raise TypeError("Cannot convert array NumBV to int. Use .bits instead.")
        return int(self.val)

    def __bool__(self) -> bool:
        if not self._is_scalar():
            raise TypeError("Cannot convert array NumBV to bool.")
        return bool(self._raw != 0)

    def __repr__(self) -> str:
        s = "s" if self._signed else "u"
        if self._is_scalar():
            return f"NumBV(w={self._width}, f={self._frac}, {s}, val={self.val})"
        return f"NumBV(w={self._width}, f={self._frac}, {s}, n={self.size})"

    def __format__(self, spec: str) -> str:
        if self._is_scalar():
            if spec in ("x", "X", "hex"):
                return self.hex
            if spec in ("b", "bin"):
                return self.bin
        return format(self.val, spec)


# ---------------------------------------------------------------------------
# Factory functions  — preferred user-facing API
# ---------------------------------------------------------------------------


def _make(
    width: int,
    frac: int,
    values: float | list | np.ndarray,
    signed: bool,
    overflow: str,
    rounding: str,
) -> NumBV:
    """Internal: build a NumBV from float value(s).

    Validation is delegated to ``NumBV.__init__``.
    Scalar inputs (0-d or size-1 when originated as scalar) are reshaped to
    a 0-d array so that ``_is_scalar()`` returns ``True``.
    """
    arr = np.asarray(values, dtype=np.float64)
    # Collapse a size-1 1-d result from a scalar Python float/int input to 0-d.
    if arr.ndim == 0 or (arr.ndim == 1 and arr.size == 1 and np.ndim(values) == 0):
        arr = arr.reshape(())
    return NumBV(arr, width, frac, signed, overflow, rounding)


def _kwargs(signed, overflow, rounding) -> tuple[bool, str, str]:
    return (
        signed,
        overflow if overflow is not None else config.overflow,
        rounding if rounding is not None else config.rounding,
    )


def zeros(
    width: int,
    frac: int,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: str | None = None,
    rounding: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with zeros.

    Usage::

        a = nbv.zeros(16, 8)            # scalar
        b = nbv.zeros(16, 8, n=1024)   # array of 1024 zeros
    """
    s, of, rd = _kwargs(signed, overflow, rounding)
    values = np.zeros(n, dtype=np.float64) if n is not None else 0.0
    return _make(width, frac, values, s, of, rd)


def ones(
    width: int,
    frac: int,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: str | None = None,
    rounding: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with ones (quantized to format).

    Usage::

        a = nbv.ones(16, 8)            # scalar, value = 1.0
        b = nbv.ones(16, 8, n=1024)   # array of 1024 ones
    """
    s, of, rd = _kwargs(signed, overflow, rounding)
    values = np.ones(n, dtype=np.float64) if n is not None else 1.0
    return _make(width, frac, values, s, of, rd)


def full(
    width: int,
    frac: int,
    fill: int | float,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: str | None = None,
    rounding: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with *fill*.

    Usage::

        a = nbv.full(16, 8, 1.5)           # scalar
        b = nbv.full(16, 8, 1.5, n=1024)   # array
    """
    s, of, rd = _kwargs(signed, overflow, rounding)
    values = np.full(n, fill, dtype=np.float64) if n is not None else float(fill)
    return _make(width, frac, values, s, of, rd)


def array(
    width: int,
    frac: int,
    data: list | np.ndarray,
    *,
    signed: bool = True,
    overflow: str | None = None,
    rounding: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` from a Python list or ``np.ndarray``.

    Usage::

        a = nbv.array(16, 8, [0.5, 1.0, 1.5])
        b = nbv.array(16, 8, np.linspace(0, 1, 512))
    """
    s, of, rd = _kwargs(signed, overflow, rounding)
    return _make(width, frac, data, s, of, rd)


def from_bits(
    width: int,
    frac: int,
    data: int | list | np.ndarray,
    *,
    signed: bool = True,
    overflow: str | None = None,
    rounding: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` from raw unsigned bit pattern(s).

    Useful for loading hardware register dumps or binary files.

    Usage::

        a = nbv.from_bits(16, 8, 0x0180)                    # scalar
        b = nbv.from_bits(16, 8, [0x0100, 0x0200, 0x0300]) # array
    """
    s, of, rd = _kwargs(signed, overflow, rounding)

    raw = np.asarray(data, dtype=np.int64)
    # Mask to width and reinterpret as two's complement if signed.
    raw &= (1 << width) - 1
    if s:
        raw = np.where(raw & (1 << (width - 1)), raw - (1 << width), raw)

    # Scalar: collapse to 0-d array.
    if raw.ndim == 0 or (raw.ndim == 1 and raw.size == 1 and np.ndim(data) == 0):
        raw = raw.reshape(())

    # Bypass _make / __init__ — raw is already a valid int64 representation.
    dummy = object.__new__(NumBV)
    dummy._width = width
    dummy._frac = frac
    dummy._signed = s
    dummy._overflow = of
    dummy._rounding = rd
    dummy._scale     = 1 << frac
    dummy._mask      = (1 << width) - 1
    dummy._wrap_offset = 1 << width
    dummy._lo = -(1 << (width - 1)) if s else 0
    dummy._hi = (1 << (width - 1)) - 1 if s else (1 << width) - 1
    return NumBV._from_raw(raw, dummy)


def zeros_like(other: NumBV, *, overflow: str | None = None) -> NumBV:
    """Create a zero-filled ``NumBV`` with the same format as *other*.

    Usage::

        b = nbv.zeros_like(a)
    """
    of = overflow if overflow is not None else other._overflow
    raw = np.zeros_like(other._raw)
    return NumBV._from_raw(raw, other) if of == other._overflow else _make(
        other._width, other._frac, 0.0, other._signed, of, other._rounding
    )


def full_like(
    other: NumBV,
    fill: int | float,
    *,
    overflow: str | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with *fill*, same format as *other*.

    Usage::

        b = nbv.full_like(a, 1.5)
    """
    of = overflow if overflow is not None else other._overflow
    values = np.full(other.shape, fill, dtype=np.float64) if not other._is_scalar() else float(fill)
    return _make(other._width, other._frac, values, other._signed, of, other._rounding)
