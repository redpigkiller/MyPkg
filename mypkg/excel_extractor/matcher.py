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
from pathlib import Path
from typing import Any, Literal
from dataclasses import dataclass
import re

import xlrd
import openpyxl
from rapidfuzz import fuzz
from mypkg.excel_extractor.types import CellCondition, Types
from mypkg.excel_extractor.template import (
    Block,
    EmptyRow,
    Group,
    Row,
    TemplateNode,
    AltNode,
)
from mypkg.excel_extractor.result import (
    MatchOptions,
    MatchOutput,
    MatchResult,
    NearMissHint,
    NodeResult,
)
from mypkg.excel_extractor.normalizer import InternalCell, InternalGrid
from mypkg.excel_extractor.normalizer import _load_xls_from_wb, _load_xlsx_from_wb


# ---------------------------------------------------------------------------
# TemplateMatcher
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompiledCellCondition:
    pattern: str | re.Pattern
    is_merged: bool = False


@dataclass(frozen=True)
class CompiledRule:
    rules: tuple[CompiledCellCondition, ...]
    normalize: bool
    min_similarity: float | None
    match_ratio: float | None

@dataclass
class CompiledTemplate:
    regex: re.Pattern
    symbol_map: dict[str, CompiledRule]
    width: int

class TemplateMatcher:
    """Matches one or more Block templates against an InternalGrid."""

    def __init__(self, templates: list[Block], options: MatchOptions):
        self.templates = templates
        self.options = options

        self.compiled_templates = []
        for template in self.templates:
            regex, symbol_map = self._compile(template)
            self.compiled_templates.append(
                CompiledTemplate(
                    regex=re.compile(regex),
                    symbol_map=symbol_map,
                    width=template.width
                )
            )
            
    # ------------------------------------------------------------------
    # Match Parts
    # ------------------------------------------------------------------

    def scan_for_blocks(self, grid: InternalGrid) -> list[tuple[int, int]]:
        match_result = []
        for compiled_template in self.compiled_templates:
            template_regex = compiled_template.regex
            symbol_map = compiled_template.symbol_map
            width = compiled_template.width

            match_positions = []
            for i in range(grid.num_rows):
                for j in range(grid.num_cols - width + 1):
                    sub_grid = grid[i:, j:j + width]
                    if self._match_template(sub_grid, template_regex, symbol_map):
                        match_positions.append((i, j))
            match_result.append(match_positions)
        return match_result

    def _match_template(self, sub_grid: list[list[InternalCell]], template_regex: str, symbol_map: dict[str, CompiledRule]) -> bool:
        # Step 1: scan each row → symbol or None
        symbol_sequence = []
        for row in sub_grid:
            symbol = self._match_row(row, symbol_map)
            if symbol is not None:
                symbol_sequence.append(symbol)
        
        # Step 2: join and regex match
        joined = "".join(symbol_sequence)
        return bool(re.match(template_regex, joined))

    def _match_row(self, row: list[InternalCell], symbol_map: dict[str, CompiledRule]) -> str | None:
        """Try to match a row against all compiled rules. Return symbol char or None."""
        for symbol, compiled_rule in symbol_map.items():
            if self._row_matches_rule(row, compiled_rule):
                return symbol
        return None

    def _row_matches_rule(self, row: list[InternalCell], compiled_rule: CompiledRule) -> bool:
        if len(row) != len(compiled_rule.rules):
            return False

        matched = 0
        for cell, rule in zip(row, compiled_rule.rules):
            if self._cell_matches(cell, rule, compiled_rule.normalize, compiled_rule.min_similarity):
                matched += 1

        if compiled_rule.match_ratio is not None:
            return (matched / len(row)) >= compiled_rule.match_ratio
        return matched == len(row)

    def _cell_matches(
        self,
        cell: InternalCell,
        rule: CompiledCellCondition,
        normalize: bool,
        min_similarity: float | None,
    ) -> bool:
        # Check is_merged first — fast reject
        if rule.is_merged != cell.is_merged:
            return False

        cell_value = cell.value or ""

        if isinstance(rule.pattern, str):
            if normalize:
                cell_value = cell_value.strip().lower()

            if min_similarity is not None:
                return fuzz.ratio(rule.pattern, cell_value) / 100.0 >= min_similarity
            return rule.pattern == cell_value
        return bool(rule.pattern.fullmatch(cell_value))

    # ------------------------------------------------------------------
    # Compile Parts
    # ------------------------------------------------------------------

    def _compile(self, block: Block) -> tuple[str, dict[str, CompiledRule]]:
        self._seen: dict[CompiledRule, str] = {}
        self._counter: int = 0
        self._symbol_map: dict[str, CompiledRule] = {}

        parts = [self._visit(child) for child in block.children]
        return "".join(parts), self._symbol_map

    def _register(self, node: TemplateNode) -> str:
        def _process_pattern(r: str | CellCondition, normalize: bool) -> CompiledCellCondition:
            if isinstance(r, str):
                if normalize:
                    r = r.strip().lower()
                return CompiledCellCondition(pattern=r, is_merged=False)
            return CompiledCellCondition(
                pattern=re.compile('|'.join(list(r.patterns))),
                is_merged=r.is_merged
            )

        key = CompiledRule(
            rules=tuple(
                _process_pattern(r, node.normalize)
                for r in node.rules()
            ),
            normalize=node.normalize,
            min_similarity=node.min_similarity,
            match_ratio=node.match_ratio,
        )
        if key not in self._seen:
            if self._counter > 0xFFFF:
                raise RuntimeError("Too many unique rules (>65534), this is likely a bug")
            symbol_char = chr(0xF0000 + self._counter)
            self._counter += 1
            self._seen[key] = symbol_char
            self._symbol_map[symbol_char] = key
        return self._seen[key]

    @staticmethod
    def _repeat_suffix(node: TemplateNode) -> str:
        lo, hi = node.repeat_range
        if (lo, hi) == (1, 1):
            return ""
        if (lo, hi) == (0, 1):
            return "?"
        if (lo, hi) == (0, None):
            return "*"
        if (lo, hi) == (1, None):
            return "+"
        if lo == hi:
            return f"{{{lo}}}"
        if hi is None:
            return f"{{{lo},}}"
        return f"{{{lo},{hi}}}"

    def _visit(self, node: TemplateNode) -> str:
        suffix = self._repeat_suffix(node)
        if isinstance(node, AltNode):
            parts = [self._visit(alt) for alt in node.alternatives]
            return f"({'|'.join(parts)}){suffix}"
        if isinstance(node, Group):
            parts = [self._visit(child) for child in node.children]
            return f"({''.join(parts)}){suffix}"
        return f"{self._register(node)}{suffix}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_template(
    file_path: str | Path,
    template: Block | list[Block],
    sheet: str | int | list[str | int] | None = None,
    options: MatchOptions | None = None,
) -> MatchOutput:
    """Extract data from an Excel file using a template description.

    Parameters
    ----------
    file_path   : path to the Excel file
    template    : a Block or list of Block objects describing the expected layout
    sheet       : sheet name, 0-based index, list of names/indices, or ``None``
                   to scan **all** sheets (default).
                   Pass ``"*"`` as an alias for None (scan all sheets).
    options     : MatchOptions instance; defaults to MatchOptions()

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
    path_str = str(file_path)
    wb_xlrd = None
    wb_openpyxl = None
    all_sheet_names = []

    if path_str.lower().endswith(".xls"):
        wb_xlrd = xlrd.open_workbook(path_str, formatting_info=True)
        all_sheet_names = wb_xlrd.sheet_names()

    elif path_str.lower().endswith(".xlsx") or path_str.lower().endswith(".xlsm"):
        wb_openpyxl = openpyxl.load_workbook(path_str, data_only=True)
        all_sheet_names = wb_openpyxl.sheetnames

    if (wb_xlrd is None and wb_openpyxl is None) or not all_sheet_names:
        raise ValueError(f"Can not read {path_str}")

    if sheet is None:
        sheets_to_scan = all_sheet_names
    else:
        if not isinstance(sheet, list):
            sheet = [sheet]
        try:
            sheets_to_scan: list[str] = [
                all_sheet_names[s] if isinstance(s, int) else s for s in sheet
            ]
        except IndexError as e:
            raise ValueError(
                f"Sheet index should less than {len(all_sheet_names)}."
            ) from e

        not_found_sheet = [s for s in sheets_to_scan if s not in all_sheet_names]
        if not_found_sheet:
            raise ValueError(f"Sheet {', '.join(not_found_sheet)} not found.")

    merged_results: list = []
    merged_near_misses: list = []

    template_matcher = TemplateMatcher(templates, options)

    for sheet_name in sheets_to_scan:
        if wb_xlrd is not None:
            grid = _load_xls_from_wb(wb_xlrd, sheet_name)
        elif wb_openpyxl is not None:
            grid = _load_xlsx_from_wb(wb_openpyxl, sheet_name)
        else:
            raise ValueError("Unexpected error: None of sheet is loaded.")

        # Start matching
        output = template_matcher.scan_for_blocks(grid)
        print(output)
        exit(0)
        # Check progress
        flag_complete = False
        if options.return_mode == "FIRST" and output.results:
            flag_complete = True

        merged_results.extend(output.results)
        merged_near_misses.extend(output.near_misses)

        if flag_complete:
            break

    # Clean up
    if wb_openpyxl is not None:
        wb_openpyxl.close()
    return MatchOutput(results=merged_results, near_misses=merged_near_misses)
