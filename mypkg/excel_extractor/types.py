"""Types system for the Excel extraction engine.

CellCondition is the internal representation of a cell constraint.
The Types class exposes user-facing constants that map onto CellConditions.
"""

from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class CellCondition:
    """Internal representation of a cell match condition.

    pattern  : regex string to match against the normalised cell value string.
                An empty pattern '' means "match nothing" (used as sentinel).
    is_merged: if True, the cell must originate from a merge-expand.
    any_val  : if True, match any non-empty value regardless of pattern.
    """

    pattern: str
    is_merged: bool = False
    any_val: bool = False

    def matches(self, value: str | None, is_merged: bool) -> bool:
        """Return True if a normalised InternalCell satisfies this condition."""
        if self.is_merged and not is_merged:
            return False
        if self.any_val:
            return value is not None and value != ""
        if value is None:
            # only EMPTY pattern can match None
            return self.pattern == r"^$_NONE$"
        return bool(re.fullmatch(self.pattern, str(value)))

    def __or__(self, other: "CellCondition") -> "CellCondition":
        # any_val propagates
        if self.any_val or other.any_val:
            return CellCondition(pattern="", is_merged=self.is_merged or other.is_merged, any_val=True)
        return CellCondition(
            pattern=f"(?:{self.pattern})|(?:{other.pattern})",
            is_merged=self.is_merged or other.is_merged,
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
    Types.SPACE      → empty string ("")
    Types.EMPTY      → truly empty cell (None)
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
    EMPTY  = CellCondition(pattern=r"^$_NONE$")   # sentinel matched by is None

    @staticmethod
    def r(pattern: str) -> CellCondition:
        """Create a CellCondition from a custom regex pattern."""
        return CellCondition(pattern=pattern)
