"""Result data structures returned by the Excel extraction engine."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class NodeResult:
    """Represents one matched template node (Row or Col) in the output.

    Attributes
    ----------
    node_type    : 'Row', 'Col', 'EmptyRow', or 'EmptyCol'
    node_id      : the node_id given by the user at template definition time
    repeat_index : which repetition of the node this is (0-based)
    cells        : extracted cell values — left→right for Row, top→bottom for Col
    grid_row     : 0-based row in the sheet where this node starts (-1 if unknown)
    grid_col     : 0-based column in the sheet where this node starts (-1 if unknown)
    """
    node_type:    Literal["Row", "Col", "EmptyRow", "EmptyCol"]
    node_id:      str | None
    repeat_index: int
    cells:        list[Any]
    grid_row:     int = -1
    grid_col:     int = -1

    def __repr__(self) -> str:
        id_part = self.node_id or "?"
        return (
            f"NodeResult({self.node_type}[{id_part}]#{self.repeat_index}"
            f" @({self.grid_row},{self.grid_col}) cells={len(self.cells)})"
        )


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
    diagnostics   : list of diagnostic messages (e.g., why a repeat='+' stopped)
    """
    block_id:      str | None
    sheet:         str
    anchor:        tuple[int, int]
    orientation:   Literal["vertical", "horizontal"]
    matched_nodes: list[NodeResult]
    diagnostics:   list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"MatchResult(block_id={self.block_id!r} @{self.anchor}"
            f" {self.orientation} nodes={len(self.matched_nodes)})"
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def data_nodes(self) -> list[NodeResult]:
        """Return only Row / Col nodes (excludes EmptyRow / EmptyCol)."""
        return [n for n in self.matched_nodes if n.node_type in ("Row", "Col")]

    @property
    def bounding_box(self) -> tuple[int, int, int, int]:
        """Return the inclusive bounding box (row_start, col_start, row_end, col_end).

        Derived from the anchor and the grid coordinates of all matched nodes.
        Returns the anchor itself as a 1×1 box if no nodes carry coordinates.
        """
        r0, c0 = self.anchor
        r1, c1 = r0, c0
        for node in self.matched_nodes:
            if node.grid_row >= 0:
                r1 = max(r1, node.grid_row)
            if node.grid_col >= 0:
                c1 = max(c1, node.grid_col + max(len(node.cells) - 1, 0))
        return (r0, c0, r1, c1)

    def find_node(
        self,
        node_id: str,
        repeat_index: int = 0,
    ) -> NodeResult | None:
        """Return the NodeResult matching *node_id* and *repeat_index*.

        Parameters
        ----------
        node_id      : the node_id set on the template Row/Col/EmptyRow/EmptyCol
        repeat_index : 0-based repetition index (default 0 = first occurrence)

        Returns
        -------
        The matching NodeResult, or None if not found.
        """
        for node in self.matched_nodes:
            if node.node_id == node_id and node.repeat_index == repeat_index:
                return node
        return None

    def find_nodes(self, node_id: str) -> list[NodeResult]:
        """Return all NodeResults with the given *node_id*, sorted by repeat_index.

        Useful when a Row/Col node has ``repeat="+"`` or ``repeat="*"`` and you
        want to iterate over every captured repetition without knowing the count
        in advance.

        Example
        -------
        ::

            rows = result.find_nodes("data")   # all data rows, in order
            for row in rows:
                dept, name, salary = row.cells
        """
        return sorted(
            [n for n in self.matched_nodes if n.node_id == node_id],
            key=lambda n: n.repeat_index,
        )

    def to_dict(self, header_node: str | None = None) -> dict | list[dict]:
        """Convert the result to plain Python types.

        Without header_node: returns the full structural dump.
        With header_node: returns list[dict] using that node's cells as keys.
        """
        if header_node is not None:
            header = self.find_node(header_node)
            if header is None:
                raise ValueError(f"header_node {header_node!r} not found in matched nodes")
            keys = [str(c) if c is not None else "" for c in header.cells]
            rows = [n for n in self.data_nodes() if n.node_id != header_node]
            return [dict(zip(keys, row.cells)) for row in rows]

        return {
            "block_id": self.block_id,
            "sheet": self.sheet,
            "anchor": list(self.anchor),
            "orientation": self.orientation,
            "matched_nodes": [
                {
                    "node_type": n.node_type,
                    "node_id": n.node_id,
                    "repeat_index": n.repeat_index,
                    "cells": n.cells,
                    "grid_row": n.grid_row,
                    "grid_col": n.grid_col,
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
    expected      : description of what the template expected at the failure point
    got           : the actual cell value seen at the failure point
    """
    block_id:      str | None
    sheet:         str
    anchor:        tuple[int, int]
    orientation:   Literal["vertical", "horizontal"]
    matched_ratio: float
    failed_at:     str
    expected:      str | None = None
    got:           str | None = None


@dataclass
class MatchOutput:
    """Container for the full output of match_template().

    Attributes
    ----------
    results     : successfully matched blocks
    near_misses : blocks that nearly matched (only populated when
                  MatchOptions.near_miss_threshold is set)
    """
    results:     list[MatchResult] = field(default_factory=list)
    near_misses: list[NearMissHint] = field(default_factory=list)


@dataclass
class MatchOptions:
    """Options that control the behaviour of match_template().

    Attributes
    ----------
    return_mode             : 'ALL'   → return every match
                              'FIRST' → stop scanning after the first match
    near_miss_threshold     : if set, blocks where at least this fraction of
                              top-level children matched (but the block ultimately
                              failed) are reported as NearMissHint entries.
                              Useful for debugging template mismatches.
                              None (default) disables near-miss analysis entirely.
    search_range            : (row1, col1, row2, col2) 0-based bounding box to
                              limit scanning; None = scan the entire sheet.
                              Use the ``excel_range()`` helper to convert an
                              Excel-style reference such as ``"A1:D20"``.
    consume_matched_regions : if True, templates are sorted by estimated area
                              (largest first) and each matched region is marked
                              as consumed; later templates will not report a
                              match that overlaps an already-consumed region.
                              Prevents small templates from matching inside
                              regions already claimed by larger blocks.
                              Default: False (original behaviour).
    warn_fuzzy              : if True, emit a warning when a fuzzy match triggers
                              (similarity < 1.0). Default: True.
    """
    return_mode:             Literal["FIRST", "ALL"] = "ALL"
    near_miss_threshold:     float | None = None
    search_range:            tuple[int, int, int, int] | None = None
    consume_matched_regions: bool = False
    warn_fuzzy:              bool = True
