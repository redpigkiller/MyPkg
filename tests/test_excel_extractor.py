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
from mypkg.excel_extractor.matcher import TemplateMatcher, excel_range


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
# 1b. Types.BLANK
# ---------------------------------------------------------------------------

class TestTypesBlank(unittest.TestCase):

    def test_blank_matches_none(self):
        self.assertTrue(Types.BLANK.matches(None, False))

    def test_blank_matches_empty_string(self):
        self.assertTrue(Types.BLANK.matches("", False))

    def test_blank_matches_whitespace(self):
        self.assertTrue(Types.BLANK.matches("   ", False))

    def test_blank_does_not_match_nonempty(self):
        self.assertFalse(Types.BLANK.matches("hello", False))
        self.assertFalse(Types.BLANK.matches("0", False))

    def test_empty_or_space_equivalent_to_blank(self):
        combined = Types.EMPTY | Types.SPACE
        for val in (None, "", "  "):
            self.assertEqual(
                Types.BLANK.matches(val, False),
                combined.matches(val, False),
                msg=f"Mismatch for value={val!r}",
            )


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

    def test_block_repr(self):
        b = Block(Row(["a"]), block_id="my_block")
        self.assertIn("my_block", repr(b))
        self.assertIn("vertical", repr(b))


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

    def test_is_row_all_empty_strict_mode(self):
        """allow_whitespace=False → only None counts as empty."""
        grid = make_grid([[None, "", None]])
        # allow_whitespace=True (default): None and "" → empty  → True
        self.assertTrue(grid.is_row_all_empty(0, 0, 3, allow_whitespace=True))
        # allow_whitespace=False: "" is NOT empty → False
        self.assertFalse(grid.is_row_all_empty(0, 0, 3, allow_whitespace=False))

    def test_is_row_all_empty_strict_all_none(self):
        grid = make_grid([[None, None, None]])
        self.assertTrue(grid.is_row_all_empty(0, 0, 3, allow_whitespace=False))


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
            ["*",            "姓名",  "月薪"],
            [('M', "IT"),   "Alice",  1000],
            [('M', "IT"),   "Bob",    2000],
            [None, None, None],
            [('M', "HR"),   "Carol",  3000],
            [None, None, None],
        ])
        self.matcher = make_matcher(self.grid)

    def test_group_with_empty_row(self):
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

    def test_near_miss_hint_emitted(self):
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=3),
            block_id="salary_table",
        )
        opts = MatchOptions(near_miss_threshold=0.3)
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([block])
        hints = [h for h in output.near_misses if h.block_id == "salary_table"]
        self.assertTrue(len(hints) > 0)
        best = max(hints, key=lambda h: h.matched_ratio)
        self.assertGreater(best.matched_ratio, 0.0)
        self.assertLess(best.matched_ratio, 1.0)

    def test_near_miss_hint_has_expected_and_got(self):
        """NearMissHint should carry expected/got fields for the first failure."""
        block = Block(
            Row(["部門", "姓名", "月薪"]),
            Row([Types.STR, Types.STR, Types.INT], repeat=3),
            block_id="salary_table",
        )
        opts = MatchOptions(near_miss_threshold=0.3)
        matcher = make_matcher(self.grid, opts)
        output = matcher.scan_for_blocks([block])
        hints = [h for h in output.near_misses if h.block_id == "salary_table"]
        # At least one hint should have non-None expected/got
        with_info = [h for h in hints if h.expected is not None or h.got is not None]
        self.assertTrue(len(with_info) > 0, "Expected at least one hint with expected/got info")

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
        )

    def test_data_nodes_excludes_empty(self):
        r = self._make_result()
        self.assertEqual(len(r.data_nodes()), 2)

    def test_to_dict_keys(self):
        r = self._make_result()
        d = r.to_dict()
        self.assertIn("block_id", d)
        self.assertIn("matched_nodes", d)
        self.assertNotIn("score", d)  # score has been removed

    def test_match_result_repr(self):
        r = self._make_result()
        s = repr(r)
        self.assertIn("test", s)
        self.assertIn("vertical", s)

    def test_node_result_repr(self):
        n = NodeResult("Row", "data", 0, [1, 2, 3], grid_row=5, grid_col=0)
        s = repr(n)
        self.assertIn("data", s)
        self.assertIn("Row", s)
        self.assertIn("5,0", s)


# ---------------------------------------------------------------------------
# 11. NodeResult grid coordinates
# ---------------------------------------------------------------------------

class TestNodeResultCoordinates(unittest.TestCase):

    def test_vertical_row_coordinates(self):
        """Each Row NodeResult should carry the correct absolute grid row/col."""
        grid = make_grid([
            ["部門", "姓名"],
            ["IT",   "Alice"],
            ["HR",   "Bob"],
        ])
        block = Block(
            Row(["部門", "姓名"], node_id="header"),
            Row([Types.STR, Types.STR], repeat="+", node_id="data"),
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)

        header = result.find_node("header", 0)
        self.assertIsNotNone(header)
        self.assertEqual(header.grid_row, 0)
        self.assertEqual(header.grid_col, 0)

        data0 = result.find_node("data", 0)
        self.assertIsNotNone(data0)
        self.assertEqual(data0.grid_row, 1)
        self.assertEqual(data0.grid_col, 0)

        data1 = result.find_node("data", 1)
        self.assertIsNotNone(data1)
        self.assertEqual(data1.grid_row, 2)
        self.assertEqual(data1.grid_col, 0)

    def test_vertical_block_offset(self):
        """Grid anchored at (row=2, col=1) should propagate offsets correctly."""
        grid = make_grid([
            [None, None,    None],
            [None, None,    None],
            [None, "Header", "Val"],
            [None, "IT",     "100"],
        ])
        block = Block(
            Row(["Header", "Val"], node_id="h"),
            Row([Types.STR, Types.INT], node_id="d"),
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_vertical(2, 1, block)
        self.assertIsNotNone(result)
        self.assertEqual(result.find_node("h").grid_row, 2)
        self.assertEqual(result.find_node("h").grid_col, 1)
        self.assertEqual(result.find_node("d").grid_row, 3)
        self.assertEqual(result.find_node("d").grid_col, 1)

    def test_horizontal_col_coordinates(self):
        """Each Col NodeResult should carry the correct absolute grid col."""
        grid = make_grid([
            ["Label", "Jan", "Feb"],
            ["Target", 100,   200],
        ])
        block = Block(
            Col(["Label", "Target"], node_id="label_col"),
            Col([Types.STR, Types.INT], repeat="+", node_id="data_col"),
            orientation="horizontal",
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_horizontal(0, 0, block)
        self.assertIsNotNone(result)

        label = result.find_node("label_col", 0)
        self.assertEqual(label.grid_col, 0)
        self.assertEqual(label.grid_row, 0)

        data0 = result.find_node("data_col", 0)
        self.assertEqual(data0.grid_col, 1)

        data1 = result.find_node("data_col", 1)
        self.assertEqual(data1.grid_col, 2)

    def test_empty_row_carries_coordinates(self):
        """EmptyRow nodes should also carry grid_row / grid_col."""
        grid = make_grid([
            ["H"],
            [None],
            ["D"],
        ])
        block = Block(
            Row(["H"], node_id="h"),
            EmptyRow(repeat=1, node_id="sep"),
            Row([Types.STR], node_id="d"),
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)
        sep = result.find_node("sep", 0)
        self.assertIsNotNone(sep)
        self.assertEqual(sep.grid_row, 1)


# ---------------------------------------------------------------------------
# 12. MatchResult.bounding_box
# ---------------------------------------------------------------------------

class TestMatchResultBoundingBox(unittest.TestCase):

    def test_vertical_bounding_box(self):
        """3-row, 2-col table → box should be (0,0,2,1)."""
        grid = make_grid([
            ["名前", "点数"],
            ["Alice",  90],
            ["Bob",    85],
        ])
        block = Block(
            Row(["名前", "点数"]),
            Row([Types.STR, Types.INT], repeat="+"),
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_vertical(0, 0, block)
        self.assertIsNotNone(result)
        r1, c1, r2, c2 = result.bounding_box
        self.assertEqual(r1, 0)
        self.assertEqual(c1, 0)
        self.assertEqual(r2, 2)   # last data row
        self.assertEqual(c2, 1)   # rightmost col (0-based, 2 cols → index 1)

    def test_anchor_offset_bounding_box(self):
        """Block anchored at (1, 2) with 2 rows → box should be (1,2,2,3)."""
        grid = make_grid([
            [None, None, None, None],
            [None, None, "A",  "B"],
            [None, None, "X",  "Y"],
        ])
        block = Block(
            Row(["A", "B"]),
            Row([Types.STR, Types.STR]),
        )
        matcher = make_matcher(grid)
        result = matcher._try_match_block_vertical(1, 2, block)
        self.assertIsNotNone(result)
        r1, c1, r2, c2 = result.bounding_box
        self.assertEqual((r1, c1), (1, 2))
        self.assertEqual(r2, 2)
        self.assertEqual(c2, 3)


# ---------------------------------------------------------------------------
# 13. MatchResult.find_node
# ---------------------------------------------------------------------------

class TestFindNode(unittest.TestCase):

    def _make_result(self):
        grid = make_grid([
            ["Header",  "Val"],
            ["Row1",    10],
            ["Row2",    20],
        ])
        block = Block(
            Row(["Header", "Val"], node_id="head"),
            Row([Types.STR, Types.INT], repeat="+", node_id="data"),
        )
        return make_matcher(grid)._try_match_block_vertical(0, 0, block)

    def test_find_header(self):
        result = self._make_result()
        node = result.find_node("head")
        self.assertIsNotNone(node)
        self.assertEqual(node.cells, ["Header", "Val"])

    def test_find_data_by_repeat_index(self):
        result = self._make_result()
        d0 = result.find_node("data", 0)
        d1 = result.find_node("data", 1)
        self.assertEqual(d0.cells, ["Row1", 10])
        self.assertEqual(d1.cells, ["Row2", 20])

    def test_find_nonexistent_returns_none(self):
        result = self._make_result()
        self.assertIsNone(result.find_node("no_such_id"))

    def test_find_nonexistent_repeat_returns_none(self):
        result = self._make_result()
        self.assertIsNone(result.find_node("data", 99))


# ---------------------------------------------------------------------------
# 13b. MatchResult.find_nodes
# ---------------------------------------------------------------------------

class TestFindNodes(unittest.TestCase):

    def _make_result(self):
        grid = make_grid([
            ["Header",  "Val"],
            ["Row1",    10],
            ["Row2",    20],
            ["Row3",    30],
        ])
        block = Block(
            Row(["Header", "Val"], node_id="head"),
            Row([Types.STR, Types.INT], repeat="+", node_id="data"),
        )
        return make_matcher(grid)._try_match_block_vertical(0, 0, block)

    def test_find_nodes_returns_all(self):
        result = self._make_result()
        nodes = result.find_nodes("data")
        self.assertEqual(len(nodes), 3)

    def test_find_nodes_sorted_by_repeat_index(self):
        result = self._make_result()
        nodes = result.find_nodes("data")
        indices = [n.repeat_index for n in nodes]
        self.assertEqual(indices, sorted(indices))

    def test_find_nodes_values_correct(self):
        result = self._make_result()
        nodes = result.find_nodes("data")
        self.assertEqual(nodes[0].cells, ["Row1", 10])
        self.assertEqual(nodes[2].cells, ["Row3", 30])

    def test_find_nodes_nonexistent_returns_empty(self):
        result = self._make_result()
        self.assertEqual(result.find_nodes("no_such_id"), [])

    def test_find_nodes_header_returns_one(self):
        result = self._make_result()
        nodes = result.find_nodes("head")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].cells, ["Header", "Val"])


# ---------------------------------------------------------------------------
# 14. Consumption mask
# ---------------------------------------------------------------------------

class TestConsumptionMask(unittest.TestCase):
    """Verify that a large matched block prevents smaller templates from
    claiming the same region when consume_matched_regions=True."""

    def setUp(self):
        self.grid = make_grid([
            ["Title", "Dept", "Name"],
            ["Hdr",   "IT",   "Alice"],
            ["Hdr",   "HR",   "Bob"],
        ])

    def _large_block(self):
        return Block(
            Row(["Title", "Dept", "Name"], node_id="h"),
            Row([Types.STR, Types.STR, Types.STR], repeat="+", node_id="d"),
            block_id="large",
        )

    def _small_block(self):
        return Block(
            Row(["Dept"], node_id="h2"),
            Row([Types.STR], repeat="+", node_id="d2"),
            block_id="small",
        )

    def test_without_consumption_mask_small_matches(self):
        opts = MatchOptions(return_mode="ALL", consume_matched_regions=False)
        matcher = TemplateMatcher(self.grid, "Sheet1", opts)
        output = matcher.scan_for_blocks([self._large_block(), self._small_block()])
        block_ids = [r.block_id for r in output.results]
        self.assertIn("large", block_ids)
        self.assertIn("small", block_ids)

    def test_with_consumption_mask_small_excluded(self):
        opts = MatchOptions(return_mode="ALL", consume_matched_regions=True)
        matcher = TemplateMatcher(self.grid, "Sheet1", opts)
        output = matcher.scan_for_blocks([self._large_block(), self._small_block()])
        block_ids = [r.block_id for r in output.results]
        self.assertIn("large", block_ids)
        large_result = next(r for r in output.results if r.block_id == "large")
        r1, c1, r2, c2 = large_result.bounding_box
        for small_r in output.results:
            if small_r.block_id == "small":
                sr, sc = small_r.anchor
                self.assertFalse(
                    r1 <= sr <= r2 and c1 <= sc <= c2,
                    f"Small block at {small_r.anchor} overlaps large block {(r1,c1,r2,c2)}"
                )


# ---------------------------------------------------------------------------
# 15. is_row_all_empty with empty strings
# ---------------------------------------------------------------------------

class TestIsRowAllEmpty(unittest.TestCase):

    def test_none_cells_are_empty(self):
        grid = make_grid([[None, None, None]])
        self.assertTrue(grid.is_row_all_empty(0, 0, 3))

    def test_empty_string_cells_are_empty(self):
        grid = make_grid([["", "", ""]])
        self.assertTrue(grid.is_row_all_empty(0, 0, 3))

    def test_nonempty_cell_is_not_empty(self):
        grid = make_grid([["", "x", ""]])
        self.assertFalse(grid.is_row_all_empty(0, 0, 3))

    def test_mixed_none_and_empty_string(self):
        grid = make_grid([[None, "", None]])
        self.assertTrue(grid.is_row_all_empty(0, 0, 3))


# ---------------------------------------------------------------------------
# 16. to_dataframe with header_node
# ---------------------------------------------------------------------------

class TestToDataframeWithHeader(unittest.TestCase):

    def setUp(self):
        try:
            import pandas
            self._pandas_available = True
        except ImportError:
            self._pandas_available = False

    def _make_result(self):
        grid = make_grid([
            ["部門", "姓名", "月薪"],
            ["IT",   "Alice", 1000],
            ["HR",   "Bob",   2000],
        ])
        block = Block(
            Row(["部門", "姓名", "月薪"], node_id="header"),
            Row([Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
        )
        return make_matcher(grid)._try_match_block_vertical(0, 0, block)

    def test_to_dataframe_with_header_node(self):
        if not self._pandas_available:
            self.skipTest("pandas not installed")
        result = self._make_result()
        df = result.to_dataframe(header_node="header")
        self.assertEqual(list(df.columns), ["部門", "姓名", "月薪"])
        self.assertEqual(len(df), 2)   # 2 data rows (header excluded)

    def test_to_dataframe_default_no_column_names(self):
        if not self._pandas_available:
            self.skipTest("pandas not installed")
        result = self._make_result()
        df = result.to_dataframe()
        # Default: integer column indices
        self.assertEqual(list(df.columns), [0, 1, 2])
        self.assertEqual(len(df), 3)   # header + 2 data rows


# ---------------------------------------------------------------------------
# 17. excel_range helper
# ---------------------------------------------------------------------------

class TestExcelRange(unittest.TestCase):

    def test_a1_d20(self):
        self.assertEqual(excel_range("A1:D20"), (0, 0, 19, 3))

    def test_b3_f10(self):
        self.assertEqual(excel_range("B3:F10"), (2, 1, 9, 5))

    def test_single_row(self):
        self.assertEqual(excel_range("A5:C5"), (4, 0, 4, 2))

    def test_case_insensitive(self):
        self.assertEqual(excel_range("a1:d20"), excel_range("A1:D20"))

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            excel_range("A1D20")
        with self.assertRaises(ValueError):
            excel_range("123:456")

    def test_double_letter_column(self):
        # AA = 26, so AA1:AB2 → col 26 to 27
        r1, c1, r2, c2 = excel_range("AA1:AB2")
        self.assertEqual(c1, 26)
        self.assertEqual(c2, 27)
        self.assertEqual(r1, 0)
        self.assertEqual(r2, 1)


if __name__ == "__main__":
    unittest.main()
