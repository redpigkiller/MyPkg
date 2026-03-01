"""
MapBitVector (MapBV) — A lightweight BitVector library for IC design & verification.

Classes:
    MapBV            — The main BitVector node (named variable or constant).
    MapBVSlice       — A lightweight proxy returned by MapBV[high:low].
    MapBVExpr        — A logic expression node produced by &, |, ^, ~ operators.
    StructSegment    — A data object returned by MapBV.structure for introspection.
"""

from __future__ import annotations

import uuid
import warnings
from copy import deepcopy
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Union

# Type alias for anything that carries a .value / .width
_Operand = Union["MapBV", "MapBVSlice", "MapBVExpr", int]


# ---------------------------------------------------------------------------
# StructSegment  — returned by MapBV.structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StructSegment:
    """Describes one piece of a linked MapBV's composition.

    Attributes:
        bv:          The source MapBV object.
        slice_range: (high, low) tuple if this segment is a slice,
                     or None if the full MapBV is used.
    """
    bv: "MapBV"
    slice_range: tuple[int, int] | None


# ---------------------------------------------------------------------------
# _LogicOpsMixin  — shared logic for MapBV, MapBVSlice, MapBVExpr
# ---------------------------------------------------------------------------

class _BVBase(ABC):
    """Mixin providing &, |, ^, ~, <<, >> operators."""
    __slots__ = ()

    @property
    @abstractmethod
    def value(self) -> int:
        ...

    @property
    @abstractmethod
    def width(self) -> int:
        ...

    # -- dunder -------------------------------------------------------------

    def __len__(self) -> int:
        return self.width

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.value == other
        if isinstance(other, (MapBV, MapBVSlice, MapBVExpr)):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return id(self)

    # -- formatting ---------------------------------------------------------

    def to_hex(self) -> str:
        """Return value as hex string, e.g. ``'0x000F'``."""
        ndigits = (self.width + 3) // 4
        return f"0x{self.value:0{ndigits}X}"

    def to_bin(self) -> str:
        """Return value as binary string, e.g. ``'0b0000000000001111'``."""
        return f"0b{self.value:0{self.width}b}"

    def __format__(self, spec: str) -> str:
        if spec in ("x", "X", "hex"):
            return self.to_hex()
        if spec in ("b", "bin"):
            return self.to_bin()
        return format(self.value, spec)

    # -- formatting ---------------------------------------------------------

    def __and__(self, other: _Operand) -> "MapBVExpr":
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("&", [self, other], w)

    def __rand__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("&", [other, self], self.width)

    def __or__(self, other: _Operand) -> "MapBVExpr":
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("|", [self, other], w)

    def __ror__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("|", [other, self], self.width)

    def __xor__(self, other: _Operand) -> "MapBVExpr":
        w = self.width if isinstance(other, int) else max(self.width, other.width)
        return MapBVExpr("^", [self, other], w)

    def __rxor__(self, other: int) -> "MapBVExpr":
        return MapBVExpr("^", [other, self], self.width)

    def __invert__(self) -> "MapBVExpr":
        return MapBVExpr("~", [self], self.width)

    def __lshift__(self, n: int) -> "MapBVExpr":
        if not isinstance(n, int):
            return NotImplemented
        return MapBVExpr("<<", [self, n], self.width)

    def __rshift__(self, n: int) -> "MapBVExpr":
        if not isinstance(n, int):
            return NotImplemented
        return MapBVExpr(">>", [self, n], self.width)
    

# ---------------------------------------------------------------------------
# MapBVExpr  — logic expression node
# ---------------------------------------------------------------------------

class MapBVExpr(_BVBase):
    """Represents a combinational logic expression.

    Produced by ``&``, ``|``, ``^``, ``~``, ``<<``, ``>>`` operators.
    """

    __slots__ = ("_op", "_operands", "_width", "_mask")

    def __init__(self, op: str, operands: list, width: int) -> None:
        self._op = op
        self._operands = operands
        self._width = width
        self._mask = (1 << self._width) - 1

    # -- resolve helper -----------------------------------------------------

    @staticmethod
    def _resolve(operand: _Operand, ctx: dict[str, int] | None = None) -> int:
        if isinstance(operand, int):
            return operand
        if ctx is not None:
            return operand.eval(ctx)
        return operand.value

    # -- value / eval -------------------------------------------------------

    @property
    def value(self) -> int:
        return self._evaluate(ctx=None)

    @property
    def width(self) -> int:
        return self._width
    
    def eval(self, ctx: dict[str, int]) -> int:
        return self._evaluate(ctx)

    def _evaluate(self, ctx: dict[str, int] | None) -> int:
        a = self._resolve(self._operands[0], ctx)
        if self._op == "~":
            return (~a) & self._mask
        b = self._resolve(self._operands[1], ctx)
        if self._op == "&":
            return (a & b) & self._mask
        if self._op == "|":
            return (a | b) & self._mask
        if self._op == "^":
            return (a ^ b) & self._mask
        if self._op == "<<":
            return (a << b) & self._mask
        if self._op == ">>":
            return (a >> b) & self._mask
        raise ValueError(f"Unknown operator: {self._op}")

    # -- dunder -------------------------------------------------------------

    def __len__(self) -> int:
        return self._width

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.value == other
        if isinstance(other, (MapBV, MapBVSlice, MapBVExpr)):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        ops = ", ".join(repr(o) for o in self._operands)
        return f"MapBVExpr({self._op}, [{ops}])"

    # -- formatting ---------------------------------------------------------

    def to_hex(self) -> str:
        ndigits = (self._width + 3) // 4
        return f"0x{self.value:0{ndigits}X}"

    def to_bin(self) -> str:
        return f"0b{self.value:0{self._width}b}"

    def __format__(self, spec: str) -> str:
        if spec in ("x", "X", "hex"):
            return self.to_hex()
        if spec in ("b", "bin"):
            return self.to_bin()
        return format(self.value, spec)


# ---------------------------------------------------------------------------
# MapBVSlice  — proxy returned by MapBV.__getitem__
# ---------------------------------------------------------------------------

class MapBVSlice(_BVBase):
    """Lightweight proxy representing ``parent[high:low]``.

    The slice uses *inclusive* bounds on both ends, matching the hardware
    convention ``[7:0]`` = 8 bits.
    """

    __slots__ = ("_parent", "_high", "_low")

    def __init__(self, parent: "MapBV", high: int, low: int) -> None:
        if high < low:
            raise ValueError(
                f"MapBVSlice high ({high}) must be >= low ({low})"
            )
        if high >= parent.width:
            raise ValueError(
                f"MapBVSlice high bit {high} exceeds parent width {parent.width}"
            )
        if low < 0:
            raise ValueError(f"MapBVSlice low bit must be >= 0, got {low}")
        self._parent = parent
        self._high = high
        self._low = low


    # -- value access -------------------------------------------------------

    @property
    def value(self) -> int:
        """Read bits [high:low] from the parent MapBV."""
        mask = (1 << self.width) - 1
        return (self._parent.value >> self._low) & mask

    @value.setter
    def value(self, val: int) -> None:
        """Write bits [high:low] into the parent MapBV."""
        mask = (1 << self.width) - 1
        val &= mask
        parent_val = self._parent.value
        clear_mask = ~(mask << self._low) & ((1 << self._parent.width) - 1)
        self._parent.value = (parent_val & clear_mask) | (val << self._low)

    # -- symbolic eval ------------------------------------------------------

    def eval(self, ctx: Dict[str, int]) -> int:
        """Evaluate this slice symbolically using *ctx*."""
        parent_val = self._parent.eval(ctx)
        mask = (1 << self.width) - 1
        return (parent_val >> self._low) & mask

    # -- linking (slice as target) ------------------------------------------

    def link(self, *parts: Union["MapBV", "MapBVSlice"]) -> None:
        """Link parts into this slice of the parent MapBV.

        This restructures the parent so that the slice region
        ``[high:low]`` is replaced by a linked sub-MapBV composed of *parts*.
        The upper and lower bit regions (if any) are preserved as
        independent helper BVs to avoid self-referencing recursion.
        """
        total = sum(p.width for p in parts)
        if total != self.width:
            raise ValueError(
                f"Link width mismatch: parts total {total} bits, "
                f"but slice [{self._high}:{self._low}] is {self.width} bits"
            )
        if self._parent._is_linked:
            import warnings
            warnings.warn(
                f"MapBV '{self._parent.name}' is already linked. "
                f"Overwriting existing link structure via slice link.",
                UserWarning,
                stacklevel=2,
            )

        # Snapshot the current raw value BEFORE restructuring
        cur_val = self._parent.value
        parent_w = self._parent.width

        # Build a helper MapBV for the linked slice region
        helper = MapBV(f"_{self._parent.name}[{self._high}:{self._low}]", self.width)
        helper._is_linked = True
        helper._link_member = list(parts)

        # Build independent BVs for preserved upper/lower regions
        pieces: List[Union[MapBV, MapBVSlice]] = []
        if self._high < parent_w - 1:
            upper_w = parent_w - 1 - self._high
            upper_bv = MapBV(f"_{self._parent.name}[{parent_w-1}:{self._high+1}]", upper_w)
            upper_bv._raw_value = (cur_val >> (self._high + 1)) & ((1 << upper_w) - 1)
            pieces.append(upper_bv)
        pieces.append(helper)
        if self._low > 0:
            lower_w = self._low
            lower_bv = MapBV(f"_{self._parent.name}[{self._low-1}:0]", lower_w)
            lower_bv._raw_value = cur_val & ((1 << lower_w) - 1)
            pieces.append(lower_bv)

        self._parent._is_linked = True
        self._parent._link_member = pieces

    # -- dunder -------------------------------------------------------------

    def __len__(self) -> int:
        return self.width

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.value == other
        if isinstance(other, (MapBV, MapBVSlice, MapBVExpr)):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        return f"MapBVSlice({self._parent.name}[{self._high}:{self._low}])"

    # -- formatting ---------------------------------------------------------

    def to_hex(self) -> str:
        """Return value as hex string, e.g. ``'0x0F'``."""
        ndigits = (self.width + 3) // 4
        return f"0x{self.value:0{ndigits}X}"

    def to_bin(self) -> str:
        """Return value as binary string, e.g. ``'0b00001111'``."""
        return f"0b{self.value:0{self.width}b}"

    def __format__(self, spec: str) -> str:
        if spec in ("x", "X", "hex"):
            return self.to_hex()
        if spec in ("b", "bin"):
            return self.to_bin()
        return format(self.value, spec)



# ---------------------------------------------------------------------------
# MapBV  — the main BitVector class
# ---------------------------------------------------------------------------

class MapBV(_BVBase):
    """A symbolic BitVector node.

    Usage::

        # Named variable
        reg0 = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})

        padding = MapBV(0, 2)
    """

    _name: str
    _value: int
    _tags: dict | None
    _is_const: bool

    _width: int
    _link_member: list[MapBV | MapBVSlice]

    _mask: int
    _eval_key: int

    def __init__(
        self,
        name: str,
        width: int,
        value: int = 0,
        is_const: bool = False,
        tags: dict | None = None,
    ) -> None:
        if width <= 0:
            raise ValueError(f"Width must be > 0, got {width}")
        if not name.isidentifier():
            raise ValueError(f"Invalid name: {name}")
        if not 0 <= value < (1 << width):
            raise ValueError(f"Invalid value: {value}")

        self._name = name
        self._value = value
        self._width = width
        self._is_const = is_const
        if is_const:
            self._tags = None
        else:
            self._tags = tags
        self._mask = (1 << width) - 1

        # Pre-compute hashable eval key: (name, frozenset(tags.items()))
        # Falls back to None if tags contain unhashable values (e.g. lists)
        if self._tags:
            try:
                self._eval_key = (self._name, frozenset(self._tags.items()))
            except TypeError:
                self._eval_key = None
        else:
            self._eval_key = None

        # Linking state
        self._link_member: list[MapBV | MapBVSlice] = []

    # -- basic properties ---------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def width(self) -> int:
        return self._width

    @property
    def tags(self) -> dict | None:
        return self._tags

    # -- value access -------------------------------------------------------

    @property
    def value(self) -> int:
        """Read value.  If linked, concatenate children (MSB-first)."""
        if self._link_member:
            result = 0
            for child in self._link_member:
                result = (result << child.width) | child.value
            return result
        return self._value

    @value.setter
    def value(self, val: int) -> None:
        """Write value.  If linked, split and push to children.

        Writing to a constant raises a warning and is ignored.
        """
        val &= self._mask
        if self._is_const:
            warnings.warn(
                f"Attempted to write 0x{val:X} to constant MapBV "
                f"(width={self._width}). Write ignored.",
                UserWarning,
                stacklevel=2,
            )
            return
        if self._link_member:
            offset = 0
            for child in reversed(self._link_member):
                child_mask = (1 << child.width) - 1
                child.value = (val >> offset) & child_mask
                offset += child.width
        else:
            self._value = val

    # -- linking ------------------------------------------------------------

    def link(self, *parts: Union["MapBV", "MapBVSlice"], force: bool = False) -> None:
        """Define this MapBV as a concatenation of *parts* (MSB → LSB order).

        The total width of all parts must equal ``self.width``.
        Re-linking a MapBV that is already linked emits a warning.
        """
        total = sum(p.width for p in parts)
        if total != self._width:
            raise ValueError(
                f"Link width mismatch: parts total {total} bits, "
                f"but {self._name} is {self._width} bits"
            )
        if self._link_member and force is False:
            warnings.warn(
                f"MapBV '{self._name}' is already linked. "
                f"Overwriting existing link structure.",
                UserWarning,
                stacklevel=2,
            )
        self._link_member = list(parts)

    def unlink(self) -> None:
        """Remove the link structure, snapshot the current value.

        After unlinking, the MapBV holds its last computed value as a raw value.
        """
        if not self._link_member:
            return
        # Snapshot the current composite value
        self._value = self.value
        self._link_member = []

    # -- slicing ------------------------------------------------------------

    def __getitem__(self, key: int | slice) -> MapBVSlice:
        """``bv[high:low]`` → MapBVSlice (inclusive both ends)."""
        if isinstance(key, int):
            high = low = key
        elif isinstance(key, slice):
            high, low = key.start, key.stop
        else:
            raise TypeError("MapBV indexing requires a slice, e.g. bv[7:0]")
        
        if high is None or low is None:
            raise ValueError("Both high and low must be specified: bv[high:low]")
        return MapBVSlice(self, high, low)

    # -- symbolic eval ------------------------------------------------------

    def eval(self, ctx: dict) -> int:
        """Evaluate this MapBV symbolically using the context dict.

        Context keys can be:

        - **str** — matches any MapBV with that name (regardless of tags).
        - **MapBV.key(name, tags)** — matches only if *both* name AND tags
          dict are an exact match.

        Lookup priority:
          1. Tagged key ``(name, frozenset(tags))`` — exact match
          2. Name-only string key — applies to all
          3. Current ``.value`` (fallback)
        """
        if self._is_const:
            return self._value

        if self._link_member:
            result = 0
            for child in self._link_member:
                result = (result << child.width) | child.eval(ctx)
            return result

        # 1. Try tagged key (exact tags match)
        if self._eval_key is not None and self._eval_key in ctx:
            return ctx[self._eval_key] & self._mask

        # 2. Try name-only key (applies to all with this name)
        if self._name in ctx:
            return ctx[self._name] & self._mask

        # 3. Fallback to current value
        return self._value

    # -- structure introspection --------------------------------------------

    @property
    def structure(self) -> List[StructSegment]:
        """Return the linked composition as a list of ``StructSegment``."""
        if not self._link_member:
            return []
        segments: List[StructSegment] = []
        for child in self._link_member:
            if isinstance(child, MapBVSlice):
                segments.append(
                    StructSegment(bv=child.parent, slice_range=(child.high, child.low))
                )
            else:
                segments.append(StructSegment(bv=child, slice_range=None))
        return segments

    # -- class methods / static helpers ---------------------------------------

    @staticmethod
    def key(name: str, tags: dict) -> tuple:
        """Create a hashable context key for :meth:`eval`.

        Usage::

            ctx = {
                MapBV.key("REG0", {"color": "red"}): 0xAA,
                "REG1": 0xBB,          # name-only, applies to all REG1
            }
            sram.eval(ctx)
        """
        return (name, frozenset(tags.items()))

    @property
    def eval_key(self) -> Optional[tuple]:
        """The hashable key that ``eval()`` uses to look up this MapBV.

        Returns ``None`` for constants or BVs with empty tags.
        """
        return self._eval_key

    @classmethod
    def concat(cls, *parts: Union["MapBV", "MapBVSlice"], name: str = "CONCAT") -> "MapBV":
        """Create a new linked MapBV by concatenating *parts* (MSB → LSB).

        Automatically computes the total width.
        """
        total = sum(p.width for p in parts)
        bv = cls(name, total)
        bv._link_member = list(parts)
        return bv

    # -- copy / snapshot ----------------------------------------------------

    def copy(self, new_name: Optional[str] = None) -> "MapBV":
        """Create an independent copy with the current value, no links."""
        n = new_name if new_name is not None else f"{self._name}_copy"
        new_bv = MapBV(n, self._width, tags=deepcopy(self._tags))
        new_bv.value = self.value  # snapshot composite value
        return new_bv

    snapshot = copy  # alias

    def __repr__(self) -> str:
        if self._is_const:
            return f"MapBV(0x{self._value:X}, {self.width})"
        return f"MapBV(\"{self.name}\", {self.width})"