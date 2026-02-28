"""Types system for the Excel extraction engine.

CellCondition is the internal representation of a cell constraint.
The Types class exposes user-facing constants that map onto CellConditions.
"""

from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class CellCondition:
    """Internal representation of a cell match condition.

    pattern     : regex string to match against the normalised cell value string.
                  An empty pattern '' means "match nothing for non-None values"
                  (used as a sentinel alongside matches_none=True for EMPTY).
    is_merged   : if True, the cell must originate from a merge-expand.
    """

    patterns: frozenset[str]
    is_merged: bool | None = None   # None = dont' case

    @classmethod
    def from_pattern(cls, pattern: str, *, is_merged: bool = False) -> "CellCondition":
        if not pattern:
            return cls(frozenset(), is_merged)
        return cls(frozenset([pattern]), is_merged)
    
    def __or__(self, other: "CellCondition") -> "CellCondition":
        merged_patterns = self.patterns | other.patterns
        if self.is_merged == other.is_merged:
            is_merged = self.is_merged
        else:
            is_merged = None
        
        return CellCondition(patterns=merged_patterns, is_merged=is_merged)
    def __call__(self, n: int) -> list["CellCondition"]:
        """Syntactic sugar for repeating a condition n times in a row pattern."""
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"Repeat count must be a non-negative integer, got {n!r}")
        return [self] * n


class Types:
    """Predefined cell-type constants.

    Usage
    -----
    Types.STR        → any non-empty string
    Types.INT        → integer value
    Types.FLOAT      → floating-point or integer value
    Types.DATE       → normalised date string YYYY-MM-DD
    Types.TIME       → normalised time string HH:MM
    Types.MERGED     → value expanded from a merge cell
    Types.SPACE      → empty string ("") or whitespace-only string
    Types.EMPTY      → truly empty cell (None)
    Types.BLANK      → either EMPTY or SPACE (any kind of "looks empty")
    Types.ANY        → any non-empty value (wildcard)
    Types.r(pattern) → custom regex pattern
    """

    # --- basic value types ---
    ANY = CellCondition.from_pattern(pattern=r".*")
    STR = CellCondition.from_pattern(pattern=r".+")
    INT = CellCondition.from_pattern(pattern=r"[\+-]?\d+")
    POS_INT = CellCondition.from_pattern(pattern=r"\+?\d+")
    NEG_INT = CellCondition.from_pattern(pattern=r"-\d+")
    FLOAT = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?")
    SCIENTIFIC = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?([eE][\+-]?\d+)?")
    PERCENT = CellCondition.from_pattern(pattern=r"[\+-]?\d+(\.\d+)?%")

    HEX = CellCondition.from_pattern(pattern=r"0[xX][0-9a-fA-F]+")
    BIN = CellCondition.from_pattern(pattern=r"0[bB][01]+")
    OCT = CellCondition.from_pattern(pattern=r"0[oO][0-7]+")

    DATE_ISO = CellCondition.from_pattern(pattern=r"\d{4}-\d{2}-\d{2}")
    DATE_SLASH = CellCondition.from_pattern(pattern=r"\d{2}/\d{2}/\d{4}")

    TIME_24H = CellCondition.from_pattern(pattern=r"\d{2}:\d{2}")

    # --- structural types ---
    MERGED = CellCondition.from_pattern(pattern=r".*", is_merged=True)
    SPACE = CellCondition.from_pattern(pattern=r"^\s*$")
    EMPTY = CellCondition.from_pattern(pattern="")
    BLANK = CellCondition.from_pattern(pattern=r"^\s*$")

    @staticmethod
    def r(pattern: str, is_merged: bool = False) -> CellCondition:
        """Create a CellCondition from a custom regex pattern."""
        return CellCondition.from_pattern(pattern=pattern, is_merged=is_merged)
