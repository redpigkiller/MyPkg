"""Tests for mypkg.excel_extractor.

All tests use mocked openpyxl data — no real Excel files required.
"""

import sys
import types as builtins_types
import unittest
from unittest.mock import MagicMock, patch

from mypkg.excel_extractor.types import CellCondition, Types
from mypkg.excel_extractor.template import (
    Block, Col, EmptyCol, EmptyRow, Group, Row, _parse_repeat,
)
from mypkg.excel_extractor.result import MatchOptions, MatchOutput, MatchResult, NearMissHint, NodeResult
from mypkg.excel_extractor.normalizer import InternalCell, InternalGrid
from mypkg.excel_extractor.matcher import TemplateMatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_grid(rows: list[list]) -> InternalGrid:
    """Build an InternalGrid from a 2-D list of raw values.

    None  → empty cell (Types.EMPTY)
    str/int/float → normal cell
    ('M', v) → merged cell with value v
    """
    internal_rows = []
    for row in rows:
        internal_row = []
        for cell in row:
            if isinstance(cell, tuple) and cell[0] == 'M':
                internal_row.append(InternalCell(
                    value=str(cell[1]) if cell[1] is not None else None,
                    original_value=cell[1],
                    is_merged=True,
                ))
            else:
                val = None if cell is None else str(cell) if not isinstance(cell, str) else cell
                # Handle int/float values - keep numeric strings for INT pattern
                if isinstance(cell, int):
                    val = str(cell)
                elif isinstance(cell, float):
                    val = str(int(cell)) if cell == int(cell) else str(cell)
                internal_row.append(InternalCell(
                    value=val,
                    original_value=cell,
                    is_merged=False,
                ))
        internal_rows.append(internal_row)
    return InternalGrid(internal_rows)


def make_matcher(grid: InternalGrid, options: MatchOptions = None) -> TemplateMatcher:
    return TemplateMatcher(grid, "Sheet1", options or MatchOptions())


# ---------------------------------------------------------------------------
# 1. Types / CellCondition
# ---------------------------------------------------------------------------

class TestTypes(unittest.TestCase):

    def test_str_matches_string(self):
        cell = InternalCell("Alice", "Alice", False)
        self.assertTrue(Types.STR.matches(cell.value, cell.is_merged))

    def test_str_does_not_match_none(self):
        cell = InternalCell(None, None, False)
        self.assertFalse(Types.STR.matches(cell.value, cell.is_merged))

    def test_int_matches_integer_string(self):
        cell = InternalCell("1000", 1000, False)
        self.assertTrue(Types.INT.matches(cell.value, cell.is_merged))

    def test_int_does_not_match_float_string(self):
        cell = InternalCell("3.14", 3.14, False)
        self.assertFalse(Types.INT.matches(cell.value, cell.is_merged))

    def test_float_matches_float_and_int(self):
        self.assertTrue(Types.FLOAT.matches("3.14", False))
        self.assertTrue(Types.FLOAT.matches("42", False))

    def test_date_matches_iso_date(self):
        self.assertTrue(Types.DATE.matches("2024-01-15", False))
        self.assertFalse(Types.DATE.matches("15/01/2024", False))

    def test_time_matches_hhmm(self):
        self.assertTrue(Types.TIME.matches("09:30", False))
        self.assertFalse(Types.TIME.matches("9:30 AM", False))

    def test_merged_requires_is_merged_true(self):
        self.assertTrue(Types.MERGED.matches("Dept A", True))
        self.assertFalse(Types.MERGED.matches("Dept A", False))

    def test_empty_matches_none_only(self):
        self.assertTrue(Types.EMPTY.matches(None, False))
        self.assertFalse(Types.EMPTY.matches("", False))

    def test_space_matches_empty_string(self):
        self.assertTrue(Types.SPACE.matches("", False))
        self.assertTrue(Types.SPACE.matches("   ", False))
        self.assertFalse(Types.SPACE.matches("x", False))

    def test_any_matches_nonempty(self):
        self.assertTrue(Types.ANY.matches("x", False))
        self.assertTrue(Types.ANY.matches("123", False))
        self.assertFalse(Types.ANY.matches(None, False))
        self.assertFalse(Types.ANY.matches("", False))

    def test_or_operator(self):
        cond = Types.STR | Types.INT
        self.assertTrue(cond.matches("hello", False))
        self.assertTrue(cond.matches("42", False))
        self.assertFalse(cond.matches(None, False))

    def test_custom_regex(self):
        cond = Types.r(r"[A-Z]{2}\d+")
        self.assertTrue(cond.matches("AB123", False))
        self.assertFalse(cond.matches("ab123", False))

    def test_literal_in_pattern(self):
        row = Row(["姓名", Types.INT])
        cond = row.pattern[0]
        self.assertTrue(cond.matches("姓名", False))
        self.assertFalse(cond.matches("名字", False))


# ---------------------------------------------------------------------------
# 2. repeat spec parsing
# ---------------------------------------------------------------------------

class TestRepeatParsing(unittest.TestCase):

    def test_int(self):
        self.assertEqual(_parse_repeat(3), (3, 3))

    def test_question(self):
        self.assertEqual(_parse_repeat("?"), (0, 1))

    def test_plus(self):
        self.assertEqual(_parse_repeat("+"), (1, None))

    def test_star(self):
        self.assertEqual(_parse_repeat("*"), (0, None))

    def test_tuple_exact_range(self):
        self.assertEqual(_parse_repeat((2, 4)), (2, 4))

    def test_tuple_unbounded(self):
        self.assertEqual(_parse_repeat((2, None)), (2, None))

    def test_invalid_string(self):
        with self.assertRaises(ValueError):
            _parse_repeat("x")

    def test_invalid_negative(self):
        with self.assertRaises(ValueError):
            _parse_repeat(-1)

    def test_invalid_tuple_hi_lt_lo(self):
        with self.assertRaises(ValueError):
            _parse_repeat((4, 2))


# ---------------------------------------------------------------------------
# 3. Block orientation validation
# ---------------------------------------------------------------------------

class TestBlockValidation(unittest.TestCase):

    def test_vertical_rejects_col(self):
        with self.assertRaises(TypeError):
            Block(Col(["a"]), orientation="vertical")

    def test_horizontal_rejects_row(self):
        with self.assertRaises(TypeError):
            Block(Row(["a"]), orientation="horizontal")

    def test_vertical_accepts_row(self):
        b = Block(Row(["a"]), Row([Types.STR], repeat="+"))
        self.assertEqual(b.orientation, "vertical")

    def test_horizontal_accepts_col(self):
        b = Block(Col(["a"]), orientation="horizontal")
        self.assertEqual(b.orientation, "horizontal")


# ---------------------------------------------------------------------------
# 4. InternalGrid helpers
# ---------------------------------------------------------------------------

class TestInternalGrid(unittest.TestCase):

    def setUp(self):
        self.grid = make_grid([
            ["部門", "姓名", "月薪"],
            [('M', "IT"), "Alice", 1000],
            [('M', "IT"), "Bob",   2000],
            [None, None, None],
            [('M', "HR"), "Carol", 3000],
        ])

    def test_get_cell_value(self):
        c = self.grid.get_cell(0, 1)
        self.assertEqual(c.value, "姓名")

    def test_get_cell_out_of_bounds(self):
        self.assertIsNone(self.grid.get_cell(99, 0))

    def test_get_row_slice(self):
        cells = self.grid.get_row_slice(1, 0, 3)
        self.assertEqual(cells[0].is_merged, True)
        self.assertEqual(cells[1].value, "Alice")

    def test_is_row_all_empty(self):
        self.assertTrue(self.grid.is_row_all_empty(3, 0, 3))
        self.assertFalse(self.grid.is_row_all_empty(0, 0, 3))


# ---------------------------------------------------------------------------
# 5. Matcher — vertical (simple table)
# ---------------------------------------------------------------------------

class TestMatcherVertical(unittest.TestCase):

    def setUp(self):
        self.grid = make_grid([
            ["部門", "姓名", "月薪"],
            ["IT",   "Alice", 1000],
            ["IT",   "Bob",   2000],
            ["HR",   "Carol", 3000],
        ])
        self.matcher = make_matcher(self.grid)

    def test_simple_table_match(self):
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat="+"),
        )
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 1.0)
        data = result.data_nodes()
        # 1 header + 3 data rows
        self.assertEqual(len(data), 4)

    def test_no_match_wrong_header(self):
        block = Block(Row(["Name", "Salary"]))
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNone(result)

    def test_repeat_exact(self):
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=2),
        )
        # exact=2 → matches 2 data rows → should succeed
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)

    def test_repeat_tuple_range(self):
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=(2, 5)),
        )
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)

    def test_repeat_tuple_too_few_fails(self):
        # Only 3 data rows but require at least 5
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=(5, None)),
        )
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 6. Matcher — vertical with Group and EmptyRow
# ---------------------------------------------------------------------------

class TestMatcherGroup(unittest.TestCase):

    def setUp(self):
        self.grid = make_grid([
            [Types.ANY,      "姓名",  "月薪"],   # header — note: ANY won't work as data, just testing
            [('M', "IT"),   "Alice",  1000],
            [('M', "IT"),   "Bob",    2000],
            [None, None, None],                  # empty row
            [('M', "HR"),   "Carol",  3000],
            [None, None, None],                  # empty row
        ])
        # Replace Types.ANY cell in row0 col0 with actual string
        self.grid = make_grid([
            ["*",            "姓名",  "月薪"],
            [('M', "IT"),   "Alice",  1000],
            [('M', "IT"),   "Bob",    2000],
            [None, None, None],
            [('M', "HR"),   "Carol",  3000],
            [None, None, None],
        ])
        self.matcher = make_matcher(self.grid)

    def test_group_with_empty_row(self):
        # The empty row between IT and HR group is declared explicitly via
        # EmptyRow(repeat="?"), so strict matching succeeds with score 1.0.
        block = Block(
            Row([Types.ANY, "姓名", "月薪"]),
            Group(
                Row([Types.MERGED, Types.STR, Types.INT], repeat="+"),
                EmptyRow(repeat="?"),
                repeat="+",
            ),
        )
        result = self.matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 1.0)
        # Verify all 4 data rows captured (header + IT×2 + HR×1)
        data = [n for n in result.matched_nodes if n.node_type == "Row"]
        self.assertEqual(len(data), 4)



# ---------------------------------------------------------------------------
# 7. Matcher — strict matching & near-miss hints
# ---------------------------------------------------------------------------

class TestMatcherStrictAndNearMiss(unittest.TestCase):

    def setUp(self):
        # Grid with an unexpected blank row between data rows
        self.grid = make_grid([
            ["部門", "姓名", "月薪"],
            ["IT",   "Alice", 1000],
            [None,   None,    None],   # unexpected blank row
            ["HR",   "Carol", 3000],
        ])

    def test_unexpected_blank_row_fails_strict(self):
        # Require exactly 3 data rows — the blank breaks the streak → fail
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=3),
        )
        matcher = make_matcher(self.grid)
        result = matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNone(result)

    def test_explicit_empty_row_succeeds(self):
        # Declare the blank row explicitly with EmptyRow
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat="+"),
            EmptyRow(repeat=1),
            Row([Types.STR, Types.STR, Types.INT], repeat="+"),
        )
        matcher = make_matcher(self.grid)
        result = matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 1.0)

    def test_near_miss_hint_emitted(self):
        # Header matches but data rows fail (wrong header expected)
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=3),
            block_id="salary_table",
        )
        opts = MatchOptions(near_miss_threshold=0.3)
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([block])
        # The block at anchor (0,0) partially matches (header ok, then fails)
        hints = [h for h in output.near_misses if h.block_id == "salary_table"]
        self.assertTrue(len(hints) > 0)
        # matched_ratio should be 0.5 (1 of 2 top-level children succeeded)
        best = max(hints, key=lambda h: h.matched_ratio)
        self.assertGreater(best.matched_ratio, 0.0)
        self.assertLess(best.matched_ratio, 1.0)

    def test_near_miss_not_emitted_when_threshold_none(self):
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=3),
        )
        opts = MatchOptions(near_miss_threshold=None)
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([block])
        self.assertEqual(output.near_misses, [])


# ---------------------------------------------------------------------------
# 8. Matcher — horizontal (Col-based)
# ---------------------------------------------------------------------------

class TestMatcherHorizontal(unittest.TestCase):

    def setUp(self):
        # Layout: rows = [label_row, data_row]
        # col 0: label col  ["月份", "目標"]
        # col 1: Jan data   ["Jan", "100"]
        # col 2: Feb data   ["Feb", "200"]
        self.grid = make_grid([
            ["月份", "Jan", "Feb"],
            ["目標",  100,   200],
        ])
        self.matcher = make_matcher(self.grid)

    def test_horizontal_match(self):
        block = Block(
            Col(["月份", "目標"], repeat=1),
            Col([Types.STR, Types.INT], repeat="+"),
            orientation="horizontal",
        )
        result = self.matcher._try_match_block_horizontal(0, 0, block)
        self.assertIsNotNone(result)
        self.assertEqual(result.orientation, "horizontal")
        data = result.data_nodes()
        # 1 label col + 2 data cols
        self.assertEqual(len(data), 3)


# ---------------------------------------------------------------------------
# 9. scan_for_blocks — return modes
# ---------------------------------------------------------------------------

class TestScanReturnModes(unittest.TestCase):

    def setUp(self):
        # Two identical tables side by side (separated by empty col)
        self.grid = make_grid([
            ["名前", "点数", None, "名前", "点数"],
            ["Alice",  90,  None, "Bob",    85],
        ])

    def _make_block(self):
        return Block(
            Row(["名前", "点数"]),
            Row([Types.STR, Types.INT], repeat="+"),
        )

    def test_return_all(self):
        opts = MatchOptions(return_mode="ALL")
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([self._make_block()])
        self.assertGreaterEqual(len(output.results), 2)

    def test_return_first(self):
        opts = MatchOptions(return_mode="FIRST")
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([self._make_block()])
        self.assertEqual(len(output.results), 1)

    def test_return_best(self):
        opts = MatchOptions(return_mode="BEST")
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([self._make_block()])
        self.assertEqual(len(output.results), 1)


# ---------------------------------------------------------------------------
# 10. MatchResult helpers
# ---------------------------------------------------------------------------

class TestMatchResult(unittest.TestCase):

    def _make_result(self):
        nodes = [
            NodeResult("Row", None, 0, ["部門", "姓名"]),
            NodeResult("Row", None, 1, ["IT", "Alice"]),
            NodeResult("EmptyRow", None, 0, []),
        ]
        return MatchResult(
            block_id="test",
            sheet="Sheet1",
            anchor=(0, 0),
            orientation="vertical",
            matched_nodes=nodes,
            score=0.9,
        )

    def test_data_nodes_excludes_empty(self):
        r = self._make_result()
        self.assertEqual(len(r.data_nodes()), 2)

    def test_to_dict_keys(self):
        r = self._make_result()
        d = r.to_dict()
        self.assertIn("block_id", d)
        self.assertIn("matched_nodes", d)
        self.assertEqual(d["score"], 0.9)


if __name__ == "__main__":
    unittest.main()
