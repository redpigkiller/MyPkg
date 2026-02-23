"""Types system for the Excel extraction engine.

CellCondition is the internal representation of a cell constraint.
The Types class exposes user-facing constants that map onto CellConditions.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class CellCondition:
    """Internal representation of a cell match condition.

    pattern     : regex string to match against the normalised cell value string.
                  An empty pattern '' means "match nothing for non-None values"
                  (used as a sentinel alongside matches_none=True for EMPTY).
    is_merged   : if True, the cell must originate from a merge-expand.
    any_val     : if True, match any non-empty value regardless of pattern.
    matches_none: if True, a None cell value satisfies this condition.
    """

    pattern: str
    is_merged: bool = False
    any_val: bool = False
    matches_none: bool = False

    def matches(self, value: str | None, is_merged: bool) -> bool:
        """Return True if a normalised InternalCell satisfies this condition."""
        if self.is_merged and not is_merged:
            return False
        if self.any_val:
            return value is not None and value != ""
        if value is None:
            return self.matches_none
        # Empty pattern (with matches_none=False) matches nothing for non-None values.
        if not self.pattern:
            return False
        return bool(re.fullmatch(self.pattern, str(value)))

    def __or__(self, other: "CellCondition") -> "CellCondition":
        # any_val propagates
        if self.any_val or other.any_val:
            return CellCondition(
                pattern="",
                is_merged=self.is_merged or other.is_merged,
                any_val=True,
                matches_none=self.matches_none or other.matches_none,
            )
        # Combine patterns; skip empty parts to avoid degenerate alternations
        parts = [p for p in (self.pattern, other.pattern) if p]
        combined = f"(?:{'|'.join(f'(?:{p})' for p in parts)})" if parts else ""
        return CellCondition(
            pattern=combined,
            is_merged=self.is_merged or other.is_merged,
            matches_none=self.matches_none or other.matches_none,
        )


def _literal(s: str) -> CellCondition:
    """Turn a plain Python string into a literal-match CellCondition."""
    return CellCondition(pattern=re.escape(s))


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
    ANY   = CellCondition(pattern="", any_val=True)
    STR   = CellCondition(pattern=r".+")
    INT   = CellCondition(pattern=r"-?\d+")
    FLOAT = CellCondition(pattern=r"-?\d+(\.\d+)?")
    DATE  = CellCondition(pattern=r"\d{4}-\d{2}-\d{2}")
    TIME  = CellCondition(pattern=r"\d{2}:\d{2}")

    # --- structural types ---
    MERGED = CellCondition(pattern=r".*", is_merged=True)
    SPACE  = CellCondition(pattern=r"^\s*$")
    EMPTY  = CellCondition(pattern="", matches_none=True)   # matches None only
    BLANK  = CellCondition(pattern=r"^\s*$", matches_none=True)  # EMPTY | SPACE

    @staticmethod
    def r(pattern: str) -> CellCondition:
        """Create a CellCondition from a custom regex pattern."""
        return CellCondition(pattern=pattern)
