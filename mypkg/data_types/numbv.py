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

import os
import warnings
from typing import Literal, Callable, Any, cast

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Optional Numba JIT Support
# ---------------------------------------------------------------------------

_DISABLE_JIT = os.environ.get("NUMBV_DISABLE_JIT", "0") == "1"

try:
    if _DISABLE_JIT:
        # Manually trigger the fallback if the user disabled JIT
        raise ImportError

    from numba import njit

    HAS_NUMBA = True

except ImportError:
    HAS_NUMBA = False

    # Define a dummy njit that matches the signature of the real one.
    # It simply returns the function unchanged.
    def njit(*args: Any, **kwargs: Any) -> Callable:
        """Fallback: No-op decorator when Numba is missing."""
        if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
            return args[0]  # Used as @njit

        def decorator(func: Callable) -> Callable:
            return func  # Used as @njit(cache=True)

        return decorator


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


def _precision_loss_check(
    original: np.ndarray,
    quantized: np.ndarray,
    width: int,
    frac: int,
) -> None:
    """Emit warning or error if quantization changed the value."""
    reconstructed = quantized.astype(np.float64) / (1 << frac)
    if not np.array_equal(original, reconstructed):
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


@njit(cache=True)
def _clip(
    raw: np.ndarray,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Apply overflow handling (saturate or wrap) to raw int64 values."""
    if is_wrap:
        raw = raw & mask
        if signed:
            raw = np.where(raw > hi, raw - wrap_offset, raw)
    else:
        raw = np.clip(raw, lo, hi)
    return raw


@njit(cache=True)
def _raw_add(
    a: np.ndarray,
    b: np.ndarray,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Add two same-format raw arrays. No float conversion."""
    return _clip(a + b, lo, hi, mask, wrap_offset, is_wrap, signed)


@njit(cache=True)
def _raw_sub(
    a: np.ndarray,
    b: np.ndarray,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Subtract two same-format raw arrays. No float conversion."""
    return _clip(a - b, lo, hi, mask, wrap_offset, is_wrap, signed)


@njit(cache=True)
def _raw_mul(
    a: np.ndarray,
    b: np.ndarray,
    frac: int,
    is_round: bool,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Multiply two same-format raw arrays.

    ``(a * b) >> frac`` with rounding, then overflow clip.
    """
    product = a * b
    if is_round and frac > 0:
        product += 1 << (frac - 1)
    result = product >> frac
    return _clip(result, lo, hi, mask, wrap_offset, is_wrap, signed)


@njit(cache=True)
def _raw_div(
    a: np.ndarray,
    b: np.ndarray,
    frac: int,
    is_round: bool,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Divide two same-format raw arrays.

    ``(a << frac) / b`` (integer division with rounding), then overflow clip.
    """
    # Guard against division by zero — replace 0 with 1 (result will be
    # clipped to lo/hi by overflow handling anyway).
    safe_b = np.where(b == 0, np.int64(1), b)
    numerator = a << frac
    if is_round:
        half_b = np.abs(safe_b) >> 1
        numerator = np.where(numerator >= 0, numerator + half_b, numerator - half_b)
    result = np.where(
        b == 0, np.where(a >= 0, np.int64(hi), np.int64(lo)), numerator // safe_b
    )
    return _clip(result, lo, hi, mask, wrap_offset, is_wrap, signed)


@njit(cache=True)
def _raw_neg(
    a: np.ndarray,
    lo: int,
    hi: int,
    mask: int,
    wrap_offset: int,
    is_wrap: bool,
    signed: bool,
) -> np.ndarray:
    """Negate raw array. No float conversion."""
    return _clip(-a, lo, hi, mask, wrap_offset, is_wrap, signed)


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
        "_is_wrap",
        "_is_round",
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
        self._is_wrap = overflow == "wrap"
        self._is_round = rounding == "round"

        # Pre-compute data
        self._scale = 1 << self._frac
        self._mask = (1 << self._width) - 1
        self._wrap_offset = 1 << self._width
        self._lo = -(1 << (self._width - 1)) if self._signed else 0
        self._hi = (
            (1 << (self._width - 1)) - 1 if self._signed else (1 << self._width) - 1
        )

        # int64 ndarray, shape () or (n,)
        self._raw: NDArray[np.int64] = self._quantize(raw)

    # -- internal helpers --------------------------------------------------

    def _quantize(self, values: np.ndarray | int | float) -> NDArray[np.int64]:
        """Quantize real-valued *values* into a fixed-point raw integer array.

        Returns a numpy int64 array of raw (signed two's complement) integers.
        """
        # Shift fractional bits into the integer domain using the precomputed scale.
        # This avoids recalculating (1 << frac) on every function call.
        scaled = values * self._scale

        if self._is_round:
            # round: Add 0.5 before flooring for standard half-up rounding.
            raw = np.floor(scaled + 0.5).astype(np.int64)
        else:
            # trunc: Simply dropping the fractional bits.
            raw = np.floor(scaled).astype(np.int64)

        if self._is_wrap:
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
    
    def _other_raw(self, other: NumBV | int | float) -> np.ndarray:
        """Get *other*'s value as raw int64 in **self's** format.

        - Same-format ``NumBV``: returns ``other._raw`` directly (zero cost).
        - Different-format ``NumBV``: aligns fractional bits via fast integer bit-shifting.
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
            
            # Fractional bits are exactly the same — zero cost.
            # (Width differences are handled safely by the caller's overflow logic)
            if other._frac == self._frac:
                return other._raw 
            
            # Different format: Align via integer bit-shifting instead of float conversion.
            frac_diff = self._frac - other._frac
            if frac_diff > 0:
                # Upcast: shift left to add fractional bits
                return other._raw << frac_diff
            else:
                # Downcast: shift right to drop fractional bits
                shift = -frac_diff
                raw = other._raw
                if self._is_round:
                    # Add half (1 << (shift - 1)) before shifting for standard half-up rounding
                    raw = raw + (1 << (shift - 1))
                return raw >> shift

        return self._quantize(np.asarray(other, dtype=np.float64))

    @classmethod
    def _from_raw(
        cls,
        raw: np.ndarray,
        width: int,
        frac: int,
        signed: bool,
        is_wrap: bool,
        is_round: bool,
    ) -> NumBV:
        """Internal: wrap an already-quantized int64 ndarray into a new NumBV.

        Bypasses ``__init__`` so the raw bits are **not** re-quantized.
        Use only when ``raw`` is guaranteed to be a valid int64 array.
        """
        obj = object.__new__(cls)
        obj._width = width
        obj._frac = frac
        obj._signed = signed
        obj._is_wrap = is_wrap
        obj._is_round = is_round
        obj._scale = 1 << frac
        obj._mask = (1 << width) - 1
        obj._wrap_offset = 1 << width
        obj._lo = -(1 << (width - 1)) if signed else 0
        obj._hi = (1 << (width - 1)) - 1 if signed else (1 << width) - 1
        obj._raw = raw
        return obj

    # -- properties --------------------------------------------------------

    @property
    def val(self) -> NDArray[np.float64]:
        """Current value(s). ``float`` for scalar, ``np.ndarray`` for array."""
        return self._raw.astype(np.float64) / self._scale

    @property
    def bits(self) -> NDArray[np.int64]:
        """Unsigned bit pattern(s). ``int`` for scalar, ``np.ndarray`` for array."""
        return self._raw & self._mask

    @property
    def hex(self) -> str | list[str]:
        """Hex string(s), e.g. ``'0x00C0'``."""
        ndigits = (self._width + 3) // 4
        values = self.bits
        if values.ndim == 0:
            return f"0x{int(values):0{ndigits}X}"
        return [f"0x{b:0{ndigits}X}" for b in values]

    @property
    def bin(self) -> str | list[str]:
        """Binary string(s), e.g. ``'0b1100...'``."""
        values = self.bits
        if values.ndim == 0:
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
        return "wrap" if self._is_wrap else "saturate"

    @property
    def rounding(self) -> Literal["trunc", "round"]:
        return "round" if self._is_round else "trunc"

    @property
    def shape(self) -> tuple:
        """numpy-style shape. ``()`` for scalar, ``(n,)`` for array."""
        return self._raw.shape

    @property
    def size(self) -> int:
        """Total number of elements."""
        return int(self._raw.size)
    
    @property
    def ndim(self) -> int:
        """Number of array dimensions."""
        return self._raw.ndim

    @property
    def T(self) -> NumBV:
        """View of the transposed array."""
        return self.transpose()

    def transpose(self, *axes) -> NumBV:
        """Permute the dimensions of the array."""
        if self._raw.ndim < 2:
            return self.copy()
        return NumBV._from_raw(
            self._raw.transpose(*axes),
            self._width, self._frac, self._signed, self._is_wrap, self._is_round
        )

    def reshape(self, *shape) -> NumBV:
        """Give a new shape to an array without changing its data."""
        return NumBV._from_raw(
            self._raw.reshape(*shape),
            self._width, self._frac, self._signed, self._is_wrap, self._is_round
        )

    def flatten(self) -> NumBV:
        """Return a copy of the array collapsed into one dimension."""
        return NumBV._from_raw(
            self._raw.flatten(),
            self._width, self._frac, self._signed, self._is_wrap, self._is_round
        )
    
    # -- arithmetic operators (raw int64 fast path) ------------------------

    def __add__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_add(
                self._raw,
                self._other_raw(other),
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __radd__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_add(
                self._other_raw(other),
                self._raw,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __sub__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_sub(
                self._raw,
                self._other_raw(other),
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __rsub__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_sub(
                self._other_raw(other),
                self._raw,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __mul__(self, other: NumBV | int | float) -> NumBV:
        other_width = other._width if isinstance(other, NumBV) else self._width
        if self._width + other_width > 62:
             raise OverflowError(
                 f"Intermediate multiplication requires {self._width + other_width} bits, "
                 "which exceeds NumPy's int64 safe limit. Keep width <= 31 for multiplication."
             )
        return NumBV._from_raw(
            _raw_mul(
                self._raw,
                self._other_raw(other),
                self._frac,
                self._is_round,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __rmul__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_mul(
                self._other_raw(other),
                self._raw,
                self._frac,
                self._is_round,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __truediv__(self, other: NumBV | int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_div(
                self._raw,
                self._other_raw(other),
                self._frac,
                self._is_round,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __rtruediv__(self, other: int | float) -> NumBV:
        return NumBV._from_raw(
            _raw_div(
                self._other_raw(other),
                self._raw,
                self._frac,
                self._is_round,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __neg__(self) -> NumBV:
        return NumBV._from_raw(
            _raw_neg(
                self._raw,
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    def __abs__(self) -> NumBV:
        return NumBV._from_raw(
            _clip(
                np.abs(self._raw),
                self._lo,
                self._hi,
                self._mask,
                self._wrap_offset,
                self._is_wrap,
                self._signed,
            ),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    # -- in-place operators (raw int64 fast path) --------------------------

    def __iadd__(self, other: NumBV | int | float) -> NumBV:
        self._raw[...] = _raw_add(
            self._raw,
            self._other_raw(other),
            self._lo,
            self._hi,
            self._mask,
            self._wrap_offset,
            self._is_wrap,
            self._signed,
        )
        return self

    def __isub__(self, other: NumBV | int | float) -> NumBV:
        self._raw[...] = _raw_sub(
            self._raw,
            self._other_raw(other),
            self._lo,
            self._hi,
            self._mask,
            self._wrap_offset,
            self._is_wrap,
            self._signed,
        )
        return self

    def __imul__(self, other: NumBV | int | float) -> NumBV:
        self._raw[...] = _raw_mul(
            self._raw,
            self._other_raw(other),
            self._frac,
            self._is_round,
            self._lo,
            self._hi,
            self._mask,
            self._wrap_offset,
            self._is_wrap,
            self._signed,
        )
        return self

    def __itruediv__(self, other: NumBV | int | float) -> NumBV:
        self._raw[...] = _raw_div(
            self._raw,
            self._other_raw(other),
            self._frac,
            self._is_round,
            self._lo,
            self._hi,
            self._mask,
            self._wrap_offset,
            self._is_wrap,
            self._signed,
        )
        return self

    # -- comparison operators (raw int64 fast path) ------------------------

    def __eq__(self, other: object) -> bool | np.ndarray:   # type: ignore
        """Element-wise equality. Same-format compares raw integers directly."""
        if isinstance(other, NumBV):
            if self._frac == other._frac and self._width == other._width:
                return np.equal(self._raw, other._raw)
            
            max_frac = max(self._frac, other._frac)
            self_aligned = self._raw << (max_frac - self._frac)
            other_aligned = other._raw << (max_frac - other._frac)
            
            return np.equal(self_aligned, other_aligned)
        
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
                NumBV(r, ref.width, ref.frac, ref.signed, ref.overflow, ref.rounding)
                for r in result
            )

        out = NumBV(result, ref.width, ref.frac, ref.signed, ref.overflow, ref.rounding)
        # Skip the precision check entirely when silent (default) — no function call overhead.
        if config.on_precision_loss != "silent":
            _precision_loss_check(result, out._raw, ref.width, ref.frac)
        return out

    # -- array indexing ----------------------------------------------------

    def __getitem__(self, key: int | slice) -> NumBV:
        """Element access following numpy conventions.

        - ``arr[i]``   → scalar ``NumBV`` (view)
        - ``arr[i:j]`` → array ``NumBV`` (view, exclusive end)

        Raises ``TypeError`` on scalar ``NumBV``.
        """
        if self._raw.ndim == 0:
            raise TypeError(
                "NumBV scalar does not support indexing. "
                "Use .val to get the float value."
            )
        raw = self._raw[key]
        return NumBV._from_raw(
            raw, self._width, self._frac, self._signed, self._is_wrap, self._is_round
        )

    def __setitem__(
        self,
        key: int | slice,
        value: NumBV | int | float,
    ) -> None:
        """Element assignment. Quantizes value to self's format."""
        if self._raw.ndim == 0:
            raise TypeError("Cannot index-assign to a scalar NumBV.")
        self._raw[key] = self._other_raw(value)

    def __len__(self) -> int:
        """Number of elements. Scalar returns ``1`` (like MATLAB ``length``)."""
        if self._raw.ndim == 0:
            raise TypeError("len() of scalar NumBV")
        return len(self._raw)

    # -- bit-level operations ---------------------------------------------

    def get_bits(self, high: int, low: int) -> int | NDArray[np.int64]:
        """Read bits ``[high:low]`` inclusive.

        Bit indices are 0-based from LSB. ``high`` must be >= ``low``.
        Returns ``int`` for scalar, ``np.ndarray`` for array.

        Usage::

            a.get_bits(15, 8)        # scalar → int
            arr.get_bits(15, 8)     # array  → ndarray
        """
        if high < low:
            raise ValueError(f"high ({high}) must be >= low ({low})")
        if high >= self._width:
            raise ValueError(f"high bit {high} exceeds width {self._width}")
        w = high - low + 1
        result = (self.bits >> low) & ((1 << w) - 1)
        return int(result) if self._raw.ndim == 0 else result

    def set_bits(self, high: int, low: int, val: int | NDArray[np.int64]) -> None:
        """Write ``val`` into bits ``[high:low]`` inclusive. In-place.

        *val* may be a scalar ``int`` or an array matching the NumBV shape.
        """
        if high < low:
            raise ValueError(f"high ({high}) must be >= low ({low})")
        if high >= self._width:
            raise ValueError(f"high bit {high} exceeds width {self._width}")
        w = high - low + 1
        field_mask = (1 << w) - 1
        clear_mask = self._mask & ~(field_mask << low)  # pre-masked to width
        new_bits = (self.bits & clear_mask) | (
            (np.asarray(val, dtype=np.int64) & field_mask) << low
        )
        # Two's complement reinterpret for signed: unsigned bits > hi → map to negative
        if self._signed:
            new_bits = np.where(
                new_bits > self._hi, new_bits - self._wrap_offset, new_bits
            )
        self._raw = new_bits.astype(np.int64)

    # -- format conversion -------------------------------------------------

    def cast(
        self,
        width: int,
        frac: int,
        signed: bool | None = None,
        overflow: Literal["saturate", "wrap"] | None = None,
    ) -> NumBV:
        """Convert to a different Q-format. Value is preserved subject to overflow.

        Usage::

            b = a.cast(8, 4)                    # Q16.8 → Q8.4
            c = a.cast(8, 4, signed=False)      # change signedness
            d = a.cast(16, 14)                  # upcast before np.sin
        """
        s = signed if signed is not None else self._signed
        of = overflow if overflow is not None else self.overflow
        # Convert via float using precomputed _scale.
        return NumBV(
            self._raw.astype(np.float64) / self._scale,
            width,
            frac,
            s,
            of,
            self.rounding,
        )

    def copy(self) -> NumBV:
        """Return an independent copy with the same format and value."""
        return NumBV._from_raw(
            self._raw.copy(),
            self._width,
            self._frac,
            self._signed,
            self._is_wrap,
            self._is_round,
        )

    # -- debug -------------------------------------------------------------

    def report(self) -> str:
        """Print and return a formatted debug summary."""
        int_bits = self._width - self._frac - (1 if self._signed else 0)
        sign_label = "Signed" if self._signed else "Unsigned"

        if self._signed:
            lo = -(1 << (self._width - 1)) / self._scale
            hi = ((1 << (self._width - 1)) - 1) / self._scale
        else:
            lo = 0.0
            hi = ((1 << self._width) - 1) / self._scale

        precision = 1.0 / self._scale

        lines = [
            f"Q-Format  : Q{int_bits}.{self._frac} ({sign_label})",
            f"Range     : [{lo}, {hi}]",
            f"Precision : {precision}",
            f"Overflow  : {self.overflow}",
            f"Rounding  : {self.rounding}",
        ]

        if self._raw.ndim == 0:
            lines.insert(0, f"Value     : {self.val}")
            lines.insert(1, f"Bits      : {self.hex} ({self.bin})")
        else:
            lines.insert(0, f"Shape     : {self.shape}")
            lines.insert(1, f"Values    : {self.val}")

        text = "\n".join(lines)
        return text

    # -- dunder helpers ----------------------------------------------------

    def __float__(self) -> float:
        if self._raw.ndim != 0:
            raise TypeError("Cannot convert array NumBV to float. Use .val instead.")
        return float(self.val)

    def __int__(self) -> int:
        if self._raw.ndim != 0:
            raise TypeError("Cannot convert array NumBV to int. Use .bits instead.")
        return int(self.val)

    def __bool__(self) -> bool:
        if self._raw.ndim != 0:
            raise TypeError("Cannot convert array NumBV to bool.")
        return bool(self._raw != 0)

    def __repr__(self) -> str:
        s = "s" if self._signed else "u"
        if self._raw.ndim == 0:
            return f"NumBV(w={self._width}, f={self._frac}, {s}, val={self.val})"
        return f"NumBV(w={self._width}, f={self._frac}, {s}, n={self.size})"

    def __format__(self, spec: str) -> str:
        if self._raw.ndim == 0:
            if spec in ("x", "X", "hex"):
                return cast(str, self.hex)
            if spec in ("b", "bin"):
                return cast(str, self.bin)
        return format(self.val, spec)


# ---------------------------------------------------------------------------
# Factory functions  — preferred user-facing API
# ---------------------------------------------------------------------------


def _make_arr(values, *, is_bits: bool = False) -> np.ndarray:
    """Convert *values* to a 0-d or 1-d numpy array.

    Scalars (Python int/float, 0-d arrays) are collapsed to shape ``()``.
    Set ``is_bits=True`` to use int64 dtype instead of float64.
    """
    dtype = np.int64 if is_bits else np.float64
    arr = np.asarray(values, dtype=dtype)
    if arr.ndim == 0 or (arr.ndim == 1 and arr.size == 1 and np.ndim(values) == 0):
        arr = arr.reshape(())
    return arr


def zeros(
    width: int,
    frac: int,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: Literal["saturate", "wrap"] | None = None,
    rounding: Literal["trunc", "round"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with zeros.

    Usage::

        a = nbv.zeros(16, 8)            # scalar
        b = nbv.zeros(16, 8, n=1024)   # array of 1024 zeros
    """
    of = overflow if overflow is not None else config.overflow
    rd = rounding if rounding is not None else config.rounding
    values = np.zeros(n, dtype=np.float64) if n is not None else 0.0
    return NumBV(_make_arr(values), width, frac, signed, of, rd)


def ones(
    width: int,
    frac: int,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: Literal["saturate", "wrap"] | None = None,
    rounding: Literal["trunc", "round"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with ones (quantized to format).

    Usage::

        a = nbv.ones(16, 8)            # scalar, value = 1.0
        b = nbv.ones(16, 8, n=1024)   # array of 1024 ones
    """
    of = overflow if overflow is not None else config.overflow
    rd = rounding if rounding is not None else config.rounding
    values = np.ones(n, dtype=np.float64) if n is not None else 1.0
    return NumBV(_make_arr(values), width, frac, signed, of, rd)


def full(
    width: int,
    frac: int,
    fill: int | float,
    n: int | None = None,
    *,
    signed: bool = True,
    overflow: Literal["saturate", "wrap"] | None = None,
    rounding: Literal["trunc", "round"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with *fill*.

    Usage::

        a = nbv.full(16, 8, 1.5)           # scalar
        b = nbv.full(16, 8, 1.5, n=1024)   # array
    """
    of = overflow if overflow is not None else config.overflow
    rd = rounding if rounding is not None else config.rounding
    values = np.full(n, fill, dtype=np.float64) if n is not None else float(fill)
    return NumBV(_make_arr(values), width, frac, signed, of, rd)


def array(
    width: int,
    frac: int,
    data: list | np.ndarray,
    *,
    signed: bool = True,
    overflow: Literal["saturate", "wrap"] | None = None,
    rounding: Literal["trunc", "round"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` from a Python list or ``np.ndarray``.

    Usage::

        a = nbv.array(16, 8, [0.5, 1.0, 1.5])
        b = nbv.array(16, 8, np.linspace(0, 1, 512))
    """
    of = overflow if overflow is not None else config.overflow
    rd = rounding if rounding is not None else config.rounding
    return NumBV(_make_arr(data), width, frac, signed, of, rd)


def from_bits(
    width: int,
    frac: int,
    data: int | list | np.ndarray,
    *,
    signed: bool = True,
    overflow: Literal["saturate", "wrap"] | None = None,
    rounding: Literal["trunc", "round"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` from raw unsigned bit pattern(s).

    Useful for loading hardware register dumps or binary files.

    Usage::

        a = nbv.from_bits(16, 8, 0x0180)                    # scalar
        b = nbv.from_bits(16, 8, [0x0100, 0x0200, 0x0300]) # array
    """
    of = overflow if overflow is not None else config.overflow
    rd = rounding if rounding is not None else config.rounding

    raw = _make_arr(data, is_bits=True)
    # Mask to width and reinterpret as two's complement if signed.
    raw &= (1 << width) - 1
    if signed:
        raw = np.where(raw & (1 << (width - 1)), raw - (1 << width), raw)

    # Bypass __init__ — raw is already a valid int64 representation.
    return NumBV._from_raw(raw, width, frac, signed, of == "wrap", rd == "round")


def zeros_like(
    other: NumBV, *, overflow: Literal["saturate", "wrap"] | None = None
) -> NumBV:
    """Create a zero-filled ``NumBV`` with the same format as *other*.

    Usage::

        b = nbv.zeros_like(a)
    """
    of = overflow if overflow is not None else other.overflow
    # zeros raw is always 0 regardless of overflow mode — skip float round-trip.
    return NumBV._from_raw(
        np.zeros_like(other._raw),
        other._width,
        other._frac,
        other._signed,
        of == "wrap",
        other._is_round,
    )


def full_like(
    other: NumBV,
    fill: int | float,
    *,
    overflow: Literal["saturate", "wrap"] | None = None,
) -> NumBV:
    """Create a ``NumBV`` filled with *fill*, same format as *other*.

    Usage::

        b = nbv.full_like(a, 1.5)
    """
    of = overflow if overflow is not None else other.overflow
    values = (
        np.full(other.shape, fill, dtype=np.float64)
        if other._raw.ndim != 0
        else float(fill)
    )
    return NumBV(
        _make_arr(values), other._width, other._frac, other._signed, of, other.rounding
    )
