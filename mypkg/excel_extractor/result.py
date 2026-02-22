"""Result data structures returned by the Excel extraction engine."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False


@dataclass
class NodeResult:
    """Represents one matched template node (Row or Col) in the output.

    Attributes
    ----------
    node_type    : 'Row', 'Col', 'EmptyRow', or 'EmptyCol'
    node_id      : the node_id given by the user at template definition time
    repeat_index : which repetition of the node this is (0-based)
    cells        : extracted cell values — left→right for Row, top→bottom for Col
    """
    node_type: Literal["Row", "Col", "EmptyRow", "EmptyCol"]
    node_id: str | None
    repeat_index: int
    cells: list[Any]


@dataclass
class MatchResult:
    """The result of matching a Block template against an Excel sheet.

    Attributes
    ----------
    block_id      : the block_id given at Block definition time
    sheet         : sheet name where the match was found
    anchor        : (row, col) of the block's top-left corner, 0-based
    orientation   : 'vertical' or 'horizontal'
    matched_nodes : list of NodeResult in template-declaration order
    score         : reserved for future scoring extensions (currently always 1.0)
    """
    block_id: str | None
    sheet: str
    anchor: tuple[int, int]
    orientation: Literal["vertical", "horizontal"]
    matched_nodes: list[NodeResult]
    score: float = 1.0

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def data_nodes(self) -> list[NodeResult]:
        """Return only Row / Col nodes (excludes EmptyRow / EmptyCol)."""
        return [n for n in self.matched_nodes if n.node_type in ("Row", "Col")]

    def to_dataframe(self):
        """Convert matched data to a pandas DataFrame.

        vertical   → each NodeResult is a row
        horizontal → each NodeResult is a column (result is transposed)

        Raises ImportError if pandas is not installed.
        """
        if not _HAS_PANDAS:
            raise ImportError(
                "to_dataframe() requires pandas. "
                "Install with: pip install pandas"
            )
        nodes = self.data_nodes()
        if not nodes:
            return pd.DataFrame()

        data = [n.cells for n in nodes]
        if self.orientation == "vertical":
            return pd.DataFrame(data)
        else:
            return pd.DataFrame(data).T.reset_index(drop=True)

    def to_dict(self) -> dict:
        """Convert the result to a plain dict (JSON-serialisable types only)."""
        return {
            "block_id": self.block_id,
            "sheet": self.sheet,
            "anchor": list(self.anchor),
            "orientation": self.orientation,
            "score": self.score,
            "matched_nodes": [
                {
                    "node_type": n.node_type,
                    "node_id": n.node_id,
                    "repeat_index": n.repeat_index,
                    "cells": n.cells,
                }
                for n in self.matched_nodes
            ],
        }


@dataclass
class NearMissHint:
    """Diagnostic hint emitted when a block almost matched but ultimately failed.

    Only produced when MatchOptions.near_miss_threshold is set.

    Attributes
    ----------
    block_id      : block_id of the template that almost matched
    sheet         : sheet name
    anchor        : (row, col) where the partial match was attempted, 0-based
    orientation   : 'vertical' or 'horizontal'
    matched_ratio : fraction of top-level children that matched (0.0–1.0)
    failed_at     : human-readable description of the first failure point
    """
    block_id: str | None
    sheet: str
    anchor: tuple[int, int]
    orientation: Literal["vertical", "horizontal"]
    matched_ratio: float
    failed_at: str


@dataclass
class MatchOutput:
    """Container for the full output of match_template().

    Attributes
    ----------
    results     : successfully matched blocks
    near_misses : blocks that nearly matched (only populated when
                  MatchOptions.near_miss_threshold is set)
    """
    results: list[MatchResult] = field(default_factory=list)
    near_misses: list[NearMissHint] = field(default_factory=list)


@dataclass
class MatchOptions:
    """Options that control the behaviour of match_template().

    Attributes
    ----------
    return_mode         : 'ALL'   → return every match
                          'FIRST' → return only the first match found
                          'BEST'  → return only the highest-scoring match
    near_miss_threshold : if set, blocks where at least this fraction of
                          top-level children matched (but the block ultimately
                          failed) are reported as NearMissHint entries.
                          Useful for debugging template mismatches.
                          None (default) disables near-miss analysis entirely.
    search_range        : (row1, col1, row2, col2) 0-based bounding box to
                          limit scanning; None = scan the entire sheet
    """
    return_mode: Literal["FIRST", "ALL", "BEST"] = "ALL"
    near_miss_threshold: float | None = None
    search_range: tuple[int, int, int, int] | None = None
