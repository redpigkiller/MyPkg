"""Template Matcher — PEG greedy matching engine.

Strategy
--------
Every `repeat` node is matched greedily: consume as many instances as
possible (up to repeat_max), then stop.  There is no backtracking.

Matching is strict: a row/col either matches its pattern exactly or the
entire block fails at that anchor.  To handle expected blank rows in the
data, declare them explicitly using EmptyRow / EmptyCol nodes in the template.

Near-miss hints
---------------
When MatchOptions.near_miss_threshold is set, failed block anchors are
re-evaluated to count how many top-level children matched before the
first failure.  If the ratio meets the threshold, a NearMissHint is
emitted — useful for debugging template mismatches without affecting the
main matching flow.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any

from mypkg.excel_extractor.types import CellCondition, Types
from mypkg.excel_extractor.template import (
    Block, Col, EmptyCol, EmptyRow, Group, Row, TemplateNode,
)
from mypkg.excel_extractor.result import (
    MatchOptions, MatchOutput, MatchResult, NearMissHint, NodeResult,
)
from mypkg.excel_extractor.normalizer import InternalCell, InternalGrid, load_and_normalize_excel


# ---------------------------------------------------------------------------
# Cell-level matching
# ---------------------------------------------------------------------------

def _cell_matches(cell: InternalCell, condition: CellCondition) -> bool:
    return condition.matches(cell.value, cell.is_merged)


# ---------------------------------------------------------------------------
# TemplateMatcher
# ---------------------------------------------------------------------------

class TemplateMatcher:
    """Matches one or more Block templates against an InternalGrid."""

    def __init__(self, grid: InternalGrid, sheet_name: str, options: MatchOptions):
        self.grid = grid
        self.sheet_name = sheet_name
        self.options = options

    # ------------------------------------------------------------------ #
    # Row / Col node matching (single-line)                               #
    # ------------------------------------------------------------------ #

    def _try_match_row(
        self,
        grid_row: int,
        start_col: int,
        row_node: Row,
    ) -> list[Any] | None:
        """Try to match a Row node against grid row *grid_row*.

        Returns the list of extracted cell values, or None on failure.
        """
        pat = row_node.pattern
        if not pat:
            return []
        cells = self.grid.get_row_slice(grid_row, start_col, len(pat))
        if len(cells) < len(pat):
            return None
        extracted = []
        for cell, cond in zip(cells, pat):
            if not _cell_matches(cell, cond):
                return None
            extracted.append(cell.original_value)
        return extracted

    def _try_match_empty_row(
        self,
        grid_row: int,
        start_col: int,
        width: int,
        allow_whitespace: bool,
    ) -> bool:
        """Check if grid row *grid_row* is considered empty."""
        for c in range(start_col, start_col + width):
            cell = self.grid.get_cell(grid_row, c)
            if cell is None:
                continue
            cond = Types.EMPTY | Types.SPACE if allow_whitespace else Types.EMPTY
            if not _cell_matches(cell, cond):
                return False
        return True

    def _try_match_col(
        self,
        start_row: int,
        grid_col: int,
        col_node: Col,
    ) -> list[Any] | None:
        """Try to match a Col node against grid column *grid_col*.

        Returns the list of extracted cell values (top-to-bottom), or None.
        """
        pat = col_node.pattern
        if not pat:
            return []
        cells = self.grid.get_col_slice(start_row, grid_col, len(pat))
        if len(cells) < len(pat):
            return None
        extracted = []
        for cell, cond in zip(cells, pat):
            if not _cell_matches(cell, cond):
                return None
            extracted.append(cell.original_value)
        return extracted

    def _try_match_empty_col(
        self,
        start_row: int,
        grid_col: int,
        height: int,
        allow_whitespace: bool,
    ) -> bool:
        """Check if grid column *grid_col* (for *height* rows) is empty."""
        for r in range(start_row, start_row + height):
            cell = self.grid.get_cell(r, grid_col)
            if cell is None:
                continue
            cond = Types.EMPTY | Types.SPACE if allow_whitespace else Types.EMPTY
            if not _cell_matches(cell, cond):
                return False
        return True

    # ------------------------------------------------------------------ #
    # Block matching (vertical)                                           #
    # ------------------------------------------------------------------ #

    def _try_match_block_vertical(
        self,
        start_row: int,
        start_col: int,
        block: Block,
    ) -> MatchResult | None:
        """Attempt to match a vertical Block anchored at (start_row, start_col).

        Returns a MatchResult on success, None on failure.
        """
        cursor = start_row
        matched_nodes: list[NodeResult] = []
        col_width = self._infer_col_width(block, start_col)

        for child in block.children:
            result = self._consume_vertical(child, cursor, start_col, col_width, matched_nodes)
            if result is None:
                return None
            cursor = result

        return MatchResult(
            block_id=block.block_id,
            sheet=self.sheet_name,
            anchor=(start_row, start_col),
            orientation="vertical",
            matched_nodes=matched_nodes,
        )

    def _infer_col_width(self, block: Block, start_col: int) -> int:
        """Infer column width by inspecting the first Row pattern length."""
        for child in block.children:
            if isinstance(child, Row):
                return len(child.pattern)
            if isinstance(child, Group):
                for gc in child.children:
                    if isinstance(gc, Row):
                        return len(gc.pattern)
        return max(1, self.grid.num_cols - start_col)

    def _consume_vertical(
        self,
        node: TemplateNode,
        cursor: int,
        start_col: int,
        col_width: int,
        matched_nodes: list[NodeResult],
    ) -> int | None:
        """Greedily consume one template node vertically.

        Returns the new cursor position, or None on failure.
        """
        if isinstance(node, Row):
            count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or count < rep_max:
                extracted = self._try_match_row(cursor, start_col, node)
                if extracted is None:
                    break
                matched_nodes.append(NodeResult("Row", node.node_id, count, extracted))
                count += 1
                cursor += 1
            if count < rep_min:
                return None
            return cursor

        elif isinstance(node, EmptyRow):
            count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or count < rep_max:
                if cursor >= self.grid.num_rows:
                    break
                if not self._try_match_empty_row(cursor, start_col, col_width, node.allow_whitespace):
                    break
                matched_nodes.append(NodeResult("EmptyRow", node.node_id, count, []))
                count += 1
                cursor += 1
            if count < rep_min:
                return None
            return cursor

        elif isinstance(node, Group):
            group_count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or group_count < rep_max:
                saved_cursor = cursor
                saved_len = len(matched_nodes)
                failed = False
                for child in node.children:
                    result = self._consume_vertical(child, cursor, start_col, col_width, matched_nodes)
                    if result is None:
                        failed = True
                        break
                    cursor = result
                if failed:
                    cursor = saved_cursor
                    del matched_nodes[saved_len:]
                    break
                group_count += 1
            if group_count < rep_min:
                return None
            return cursor

        return None  # unknown node type

    def _partial_score_vertical(
        self, start_row: int, start_col: int, block: Block
    ) -> tuple[float, str]:
        """Count the fraction of top-level children matched before first failure.

        Used for near-miss hint generation only; does not modify state.
        Returns (matched_ratio, description_of_failure).
        """
        col_width = self._infer_col_width(block, start_col)
        cursor = start_row
        total = len(block.children)
        if total == 0:
            return 1.0, ""

        for i, child in enumerate(block.children):
            dummy: list[NodeResult] = []
            result = self._consume_vertical(child, cursor, start_col, col_width, dummy)
            if result is None:
                child_desc = type(child).__name__
                if hasattr(child, "node_id") and child.node_id:
                    child_desc += f"(id={child.node_id!r})"
                return i / total, f"{child_desc} at grid row {cursor}"
            cursor = result

        return 1.0, ""

    # ------------------------------------------------------------------ #
    # Block matching (horizontal)                                         #
    # ------------------------------------------------------------------ #

    def _try_match_block_horizontal(
        self,
        start_row: int,
        start_col: int,
        block: Block,
    ) -> MatchResult | None:
        """Attempt to match a horizontal Block anchored at (start_row, start_col)."""
        cursor = start_col
        matched_nodes: list[NodeResult] = []
        row_height = self._infer_row_height(block, start_row)

        for child in block.children:
            result = self._consume_horizontal(child, start_row, cursor, row_height, matched_nodes)
            if result is None:
                return None
            cursor = result

        return MatchResult(
            block_id=block.block_id,
            sheet=self.sheet_name,
            anchor=(start_row, start_col),
            orientation="horizontal",
            matched_nodes=matched_nodes,
        )

    def _infer_row_height(self, block: Block, start_row: int) -> int:
        """Infer row height by inspecting the first Col pattern length."""
        for child in block.children:
            if isinstance(child, Col):
                return len(child.pattern)
            if isinstance(child, Group):
                for gc in child.children:
                    if isinstance(gc, Col):
                        return len(gc.pattern)
        return max(1, self.grid.num_rows - start_row)

    def _consume_horizontal(
        self,
        node: TemplateNode,
        start_row: int,
        cursor: int,
        row_height: int,
        matched_nodes: list[NodeResult],
    ) -> int | None:
        """Greedily consume one template node horizontally.

        Returns the new cursor position, or None on failure.
        """
        if isinstance(node, Col):
            count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or count < rep_max:
                extracted = self._try_match_col(start_row, cursor, node)
                if extracted is None:
                    break
                matched_nodes.append(NodeResult("Col", node.node_id, count, extracted))
                count += 1
                cursor += 1
            if count < rep_min:
                return None
            return cursor

        elif isinstance(node, EmptyCol):
            count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or count < rep_max:
                if cursor >= self.grid.num_cols:
                    break
                if not self._try_match_empty_col(start_row, cursor, row_height, node.allow_whitespace):
                    break
                matched_nodes.append(NodeResult("EmptyCol", node.node_id, count, []))
                count += 1
                cursor += 1
            if count < rep_min:
                return None
            return cursor

        elif isinstance(node, Group):
            group_count = 0
            rep_min, rep_max = node.repeat_min, node.repeat_max
            while rep_max is None or group_count < rep_max:
                saved_cursor = cursor
                saved_len = len(matched_nodes)
                failed = False
                for child in node.children:
                    result = self._consume_horizontal(child, start_row, cursor, row_height, matched_nodes)
                    if result is None:
                        failed = True
                        break
                    cursor = result
                if failed:
                    cursor = saved_cursor
                    del matched_nodes[saved_len:]
                    break
                group_count += 1
            if group_count < rep_min:
                return None
            return cursor

        return None

    def _partial_score_horizontal(
        self, start_row: int, start_col: int, block: Block
    ) -> tuple[float, str]:
        """Count the fraction of top-level children matched before first failure.

        Used for near-miss hint generation only; does not modify state.
        Returns (matched_ratio, description_of_failure).
        """
        row_height = self._infer_row_height(block, start_row)
        cursor = start_col
        total = len(block.children)
        if total == 0:
            return 1.0, ""

        for i, child in enumerate(block.children):
            dummy: list[NodeResult] = []
            result = self._consume_horizontal(child, start_row, cursor, row_height, dummy)
            if result is None:
                child_desc = type(child).__name__
                if hasattr(child, "node_id") and child.node_id:
                    child_desc += f"(id={child.node_id!r})"
                return i / total, f"{child_desc} at grid col {cursor}"
            cursor = result

        return 1.0, ""

    # ------------------------------------------------------------------ #
    # Full-sheet scan                                                     #
    # ------------------------------------------------------------------ #

    def scan_for_blocks(self, templates: list[Block]) -> MatchOutput:
        opts = self.options
        sr = opts.search_range

        row_start = sr[0] if sr else 0
        col_start = sr[1] if sr else 0
        row_end   = sr[2] if sr else self.grid.num_rows - 1
        col_end   = sr[3] if sr else self.grid.num_cols - 1

        results: list[MatchResult] = []
        near_misses: list[NearMissHint] = []

        for template in templates:
            for r in range(row_start, row_end + 1):
                for c in range(col_start, col_end + 1):
                    if template.orientation == "vertical":
                        result = self._try_match_block_vertical(r, c, template)
                    else:
                        result = self._try_match_block_horizontal(r, c, template)

                    if result is not None:
                        if opts.return_mode == "FIRST":
                            return MatchOutput(results=[result])
                        results.append(result)
                    elif opts.near_miss_threshold is not None:
                        if template.orientation == "vertical":
                            ratio, failed_at = self._partial_score_vertical(r, c, template)
                        else:
                            ratio, failed_at = self._partial_score_horizontal(r, c, template)
                        if ratio >= opts.near_miss_threshold:
                            near_misses.append(NearMissHint(
                                block_id=template.block_id,
                                sheet=self.sheet_name,
                                anchor=(r, c),
                                orientation=template.orientation,
                                matched_ratio=ratio,
                                failed_at=failed_at,
                            ))

        if opts.return_mode == "BEST":
            results = [max(results, key=lambda r: r.score)] if results else []

        return MatchOutput(results=results, near_misses=near_misses)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_template(
    file: str | Path,
    template: "Block | list[Block]",
    sheet: str | int = 0,
    options: MatchOptions | None = None,
) -> MatchOutput:
    """Extract data from an Excel file using a template description.

    Parameters
    ----------
    file     : path to the Excel file
    template : a Block or list of Block objects describing the expected layout
    sheet    : sheet name or 0-based index (default: first sheet)
    options  : MatchOptions instance; defaults to MatchOptions()

    Returns
    -------
    MatchOutput containing matched blocks (and near-miss hints if configured).
    """
    if options is None:
        options = MatchOptions()

    templates = template if isinstance(template, list) else [template]
    grid, sheet_name = load_and_normalize_excel(file, sheet)
    matcher = TemplateMatcher(grid, sheet_name, options)
    return matcher.scan_for_blocks(templates)
