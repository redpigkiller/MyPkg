"""Template Matcher — PEG greedy matching engine.

Strategy
--------
Every `repeat` node is matched greedily: consume as many instances as
possible (up to repeat_max), then stop.  There is no backtracking.

Matching is strict: a row/col either matches its pattern exactly or the
entire block fails at that anchor.  To handle expected blank rows in the
data, declare them explicitly using EmptyRow / EmptyCol nodes in the template.

.. warning::
    All ``repeat`` specs are **greedy and non-backtracking**.  If a ``"+"``
    or ``"*"`` node is immediately followed by another node whose pattern
    overlaps, the greedy node may consume rows that the following node
    needs.  Design patterns so that adjacent nodes match non-overlapping
    cell types.

Near-miss hints
---------------
When MatchOptions.near_miss_threshold is set, failed block anchors are
re-evaluated to count how many top-level children matched before the
first failure.  If the ratio meets the threshold, a NearMissHint is
emitted — useful for debugging template mismatches without affecting the
main matching flow.

Consumption mask
----------------
When MatchOptions.consume_matched_regions is True, templates are sorted
by estimated area (largest first).  Once a block is matched, every cell
in its bounding box is marked consumed and will not be claimed again.
This prevents small templates from matching inside regions already owned
by larger blocks.
"""

from __future__ import annotations
import re as _re
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
# excel_range helper
# ---------------------------------------------------------------------------

def _col_letter_to_index(letters: str) -> int:
    """Convert a column letter (A, B, …, AA, …) to a 0-based column index."""
    result = 0
    for ch in letters.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def excel_range(ref: str) -> tuple[int, int, int, int]:
    """Convert an Excel-style range reference to a 0-based tuple.

    Parameters
    ----------
    ref : a string like ``"A1:D20"`` or ``"B3:F10"``

    Returns
    -------
    ``(row1, col1, row2, col2)`` — 0-based, inclusive on both ends.

    Examples
    --------
    ::

        excel_range("A1:D20")   # → (0, 0, 19, 3)
        excel_range("B3:F10")   # → (2, 1, 9, 5)
    """
    m = _re.fullmatch(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)", ref.strip())
    if not m:
        raise ValueError(
            f"Invalid Excel range {ref!r}. Expected format like 'A1:D20'."
        )
    col1 = _col_letter_to_index(m.group(1))
    row1 = int(m.group(2)) - 1
    col2 = _col_letter_to_index(m.group(3))
    row2 = int(m.group(4)) - 1
    return (row1, col1, row2, col2)


# ---------------------------------------------------------------------------
# Cell-level matching
# ---------------------------------------------------------------------------

def _cell_matches(
    cell: InternalCell,
    condition: CellCondition,
    normalize: bool = False,
    fuzzy: float | None = None,
    warn_fuzzy: bool = False,
) -> bool:
    val = cell.value
    if val is not None and isinstance(val, str) and normalize:
        val = val.strip().lower()

    # If it's not a fuzzy match, just use the built-in matcher
    if not fuzzy or not condition.pattern or condition.any_val or condition.matches_none or condition.is_merged:
        if normalize and val is not None and condition.pattern:
            # For normalized matching of strings, we also need to normalize the pattern if it's literal-like
            # Since CellCondition pattern is a regex, lowercasing it might be tricky, but we can try ignoring case using (?i)
            # A simpler way is to let the Condition handle it, but for our simple literals, we can just lowercase the pattern if no regex special chars.
            # However, for robustness, we use rapidfuzz if available or just let CellCondition.matches handle it.
            # If normalize is true, we should pass (?i) to regex?
            # Actually, types.py doesn't have an easy way. Let's just pass the normalized value.
            pass
            
        return condition.matches(val, cell.is_merged)
    
    # Fuzzy matching requires rapidfuzz
    try:
        from rapidfuzz import fuzz
    except ImportError:
        import warnings
        warnings.warn("rapidfuzz is required for fuzzy matching. Falling back to exact match.")
        return condition.matches(val, cell.is_merged)
        
    if val is None:
        return condition.matches_none
        
    # We only apply fuzzy logic if it's a literal string match that has original_str
    if condition.original_str is None:
        return False
        
    pattern_str = condition.original_str
    if normalize:
        pattern_str = pattern_str.strip().lower()
        
    ratio = fuzz.ratio(pattern_str, val) / 100.0
    
    if ratio >= fuzzy:
        if warn_fuzzy and ratio < 1.0:
            import warnings
            warnings.warn(f"Fuzzy matched '{pattern_str}' with '{val}' (ratio: {ratio:.2f})")
        return True
        
    return False


def _condition_desc(cond: CellCondition) -> str:
    """Return a human-readable description of a CellCondition for diagnostics."""
    if cond.any_val:
        return "ANY"
    if cond.matches_none and not cond.pattern:
        return "EMPTY"
    if cond.matches_none and cond.pattern:
        return "BLANK"
    if cond.is_merged:
        return "MERGED"
    return f"pattern={cond.pattern!r}"


# ---------------------------------------------------------------------------
# Area estimator for consumption-mask sort
# ---------------------------------------------------------------------------

def _estimate_area(block: Block) -> int:
    """Return a rough area estimate (rows × cols) for sort-ordering purposes.

    Counts only the minimum required repetitions so that optional nodes
    do not artificially inflate the estimate.
    """
    if block.orientation == "vertical":
        rows = sum(
            c.repeat_min if hasattr(c, "repeat_min") else 1
            for c in block.children
        )
        cols = 0
        for c in block.children:
            if isinstance(c, Row):
                cols = max(cols, len(c.pattern))
            elif isinstance(c, Group):
                for gc in c.children:
                    if isinstance(gc, Row):
                        cols = max(cols, len(gc.pattern))
        return rows * max(cols, 1)
    else:
        cols = sum(
            c.repeat_min if hasattr(c, "repeat_min") else 1
            for c in block.children
        )
        rows = 0
        for c in block.children:
            if isinstance(c, Col):
                rows = max(rows, len(c.pattern))
            elif isinstance(c, Group):
                for gc in c.children:
                    if isinstance(gc, Col):
                        rows = max(rows, len(gc.pattern))
        return max(rows, 1) * cols


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
            if not _cell_matches(
                cell,
                cond,
                normalize=row_node.normalize,
                fuzzy=row_node.fuzzy,
                warn_fuzzy=self.options.warn_fuzzy,
            ):
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
            if not _cell_matches(cell, cond, normalize=col_node.normalize, fuzzy=col_node.fuzzy, warn_fuzzy=self.options.warn_fuzzy):
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
                matched_nodes.append(NodeResult(
                    "Row", node.node_id, count, extracted,
                    grid_row=cursor, grid_col=start_col,
                ))
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
                matched_nodes.append(NodeResult(
                    "EmptyRow", node.node_id, count, [],
                    grid_row=cursor, grid_col=start_col,
                ))
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
    ) -> tuple[float, str, str | None, str | None]:
        """Count the fraction of top-level children matched before first failure.

        Used for near-miss hint generation only; does not modify state.
        Returns (matched_ratio, description_of_failure, expected, got).
        """
        col_width = self._infer_col_width(block, start_col)
        cursor = start_row
        total = len(block.children)
        if total == 0:
            return 1.0, "", None, None

        for i, child in enumerate(block.children):
            dummy: list[NodeResult] = []
            result = self._consume_vertical(child, cursor, start_col, col_width, dummy)
            if result is None:
                child_desc = type(child).__name__
                if hasattr(child, "node_id") and child.node_id:
                    child_desc += f"(id={child.node_id!r})"
                failed_at = f"{child_desc} at grid row {cursor}"
                expected_str, got_str = self._row_failure_info_vertical(child, cursor, start_col)
                return i / total, failed_at, expected_str, got_str
            cursor = result

        return 1.0, "", None, None

    def _row_failure_info_vertical(
        self, node: TemplateNode, grid_row: int, start_col: int
    ) -> tuple[str | None, str | None]:
        """Return (expected_desc, got_value) for the first mismatching cell in a Row node.

        Scans through successful repetitions first so that repeated nodes
        (e.g. repeat=3) correctly report the row where the match broke down,
        not the starting row.
        """
        if not isinstance(node, Row) or not node.pattern:
            return None, None
        # Advance past rows that actually match, stop at the first failure
        current = grid_row
        upper = node.repeat_max if node.repeat_max is not None else self.grid.num_rows
        for _ in range(upper):
            if current >= self.grid.num_rows:
                break
            cells = self.grid.get_row_slice(current, start_col, len(node.pattern))
            for cell, cond in zip(cells, node.pattern):
                if not _cell_matches(cell, cond):
                    return _condition_desc(cond), repr(cell.value)
            current += 1
        return None, None

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
                matched_nodes.append(NodeResult(
                    "Col", node.node_id, count, extracted,
                    grid_row=start_row, grid_col=cursor,
                ))
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
                matched_nodes.append(NodeResult(
                    "EmptyCol", node.node_id, count, [],
                    grid_row=start_row, grid_col=cursor,
                ))
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
    ) -> tuple[float, str, str | None, str | None]:
        """Count the fraction of top-level children matched before first failure.

        Used for near-miss hint generation only; does not modify state.
        Returns (matched_ratio, description_of_failure, expected, got).
        """
        row_height = self._infer_row_height(block, start_row)
        cursor = start_col
        total = len(block.children)
        if total == 0:
            return 1.0, "", None, None

        for i, child in enumerate(block.children):
            dummy: list[NodeResult] = []
            result = self._consume_horizontal(child, start_row, cursor, row_height, dummy)
            if result is None:
                child_desc = type(child).__name__
                if hasattr(child, "node_id") and child.node_id:
                    child_desc += f"(id={child.node_id!r})"
                failed_at = f"{child_desc} at grid col {cursor}"
                expected_str, got_str = self._col_failure_info_horizontal(child, start_row, cursor)
                return i / total, failed_at, expected_str, got_str
            cursor = result

        return 1.0, "", None, None

    def _col_failure_info_horizontal(
        self, node: TemplateNode, start_row: int, grid_col: int
    ) -> tuple[str | None, str | None]:
        """Return (expected_desc, got_value) for the first mismatching cell in a Col node.

        Scans through successful repetitions first so that repeated nodes
        correctly report the column where the match broke down.
        """
        if not isinstance(node, Col) or not node.pattern:
            return None, None
        current = grid_col
        upper = node.repeat_max if node.repeat_max is not None else self.grid.num_cols
        for _ in range(upper):
            if current >= self.grid.num_cols:
                break
            cells = self.grid.get_col_slice(start_row, current, len(node.pattern))
            for cell, cond in zip(cells, node.pattern):
                if not _cell_matches(cell, cond):
                    return _condition_desc(cond), repr(cell.value)
            current += 1
        return None, None



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

        # Consumption mask: sort largest-first and track claimed cells.
        if opts.consume_matched_regions:
            templates = sorted(templates, key=_estimate_area, reverse=True)
        consumed: set[tuple[int, int]] = set()

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
                        # Consumption-mask overlap check
                        if opts.consume_matched_regions:
                            r1, c1, r2, c2 = result.bounding_box
                            overlap = any(
                                (rr, cc) in consumed
                                for rr in range(r1, r2 + 1)
                                for cc in range(c1, c2 + 1)
                            )
                            if overlap:
                                continue
                            # Claim all cells in this bounding box
                            for rr in range(r1, r2 + 1):
                                for cc in range(c1, c2 + 1):
                                    consumed.add((rr, cc))

                        if opts.return_mode == "FIRST":
                            return MatchOutput(results=[result])
                        results.append(result)

                    elif opts.near_miss_threshold is not None:
                        if template.orientation == "vertical":
                            ratio, failed_at, expected, got = self._partial_score_vertical(r, c, template)
                        else:
                            ratio, failed_at, expected, got = self._partial_score_horizontal(r, c, template)
                        if ratio >= opts.near_miss_threshold:
                            near_misses.append(NearMissHint(
                                block_id=template.block_id,
                                sheet=self.sheet_name,
                                anchor=(r, c),
                                orientation=template.orientation,
                                matched_ratio=ratio,
                                failed_at=failed_at,
                                expected=expected,
                                got=got,
                            ))

        return MatchOutput(results=results, near_misses=near_misses)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_template(
    file: str | Path,
    template: "Block | list[Block]",
    sheet: "str | int | list[str | int] | None" = 0,
    options: MatchOptions | None = None,
) -> MatchOutput:
    """Extract data from an Excel file using a template description.

    Parameters
    ----------
    file     : path to the Excel file
    template : a Block or list of Block objects describing the expected layout
    sheet    : sheet name, 0-based index, list of names/indices, or ``None``
               to scan **all** sheets (default: first sheet).
               Pass ``"*"`` as an alias for None (scan all sheets).
    options  : MatchOptions instance; defaults to MatchOptions()

    Returns
    -------
    MatchOutput containing matched blocks (and near-miss hints if configured).
    All MatchResult objects carry the ``sheet`` attribute indicating which
    sheet the match was found on.
    """
    if options is None:
        options = MatchOptions()

    templates = template if isinstance(template, list) else [template]

    # Resolve the list of sheets to scan
    path_str = str(file)
    wb_xls = None
    wb_xlsx = None
    
    if path_str.lower().endswith(".xls"):
        try:
            import xlrd
        except ImportError:
            raise ImportError(
                "excel_extractor requires xlrd for .xls files. "
                "Install with: pip install xlrd  or  pip install mypkg[excel]"
            )
        wb_xls = xlrd.open_workbook(path_str, formatting_info=True)
        if sheet is None or sheet == "*":
            sheets_to_scan: list[str | int] = wb_xls.sheet_names()
        elif isinstance(sheet, list):
            sheets_to_scan = sheet
        else:
            sheets_to_scan = [sheet]
    else:
        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "excel_extractor requires openpyxl. "
                "Install with: pip install openpyxl  or  pip install mypkg[excel]"
            )
        wb_xlsx = openpyxl.load_workbook(path_str, data_only=True)
        if sheet is None or sheet == "*":
            sheets_to_scan: list[str | int] = wb_xlsx.sheetnames
        elif isinstance(sheet, list):
            sheets_to_scan = sheet
        else:
            sheets_to_scan = [sheet]

    merged_results: list = []
    merged_near_misses: list = []

    from mypkg.excel_extractor.normalizer import _load_xls_from_wb, _load_xlsx_from_wb

    for sh in sheets_to_scan:
        if wb_xls is not None:
            grid, sheet_name = _load_xls_from_wb(wb_xls, sh)
        else:
            grid, sheet_name = _load_xlsx_from_wb(wb_xlsx, sh)
            
        matcher = TemplateMatcher(grid, sheet_name, options)
        output = matcher.scan_for_blocks(templates)

        if options.return_mode == "FIRST" and output.results:
            if wb_xlsx is not None:
                wb_xlsx.close()
            return output  # stop on first match across all sheets

        merged_results.extend(output.results)
        merged_near_misses.extend(output.near_misses)

    if wb_xlsx is not None:
        wb_xlsx.close()
    return MatchOutput(results=merged_results, near_misses=merged_near_misses)
