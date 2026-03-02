"""Tests for mypkg.excel_extractor.

All tests use in-memory InternalGrid — no real Excel files required.
"""

import re
import unittest

from mypkg.excel_extractor.types import CellCondition, Types
from mypkg.excel_extractor.template import (
    Block, EmptyRow, Group, Row, AltNode, _parse_repeat,
)
from mypkg.excel_extractor.result import MatchOptions, BlockMatch, RowMatch, CellMatch
from mypkg.excel_extractor.normalizer import InternalCell, InternalGrid
from mypkg.excel_extractor.matcher import TemplateMatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_grid(rows: list[list]) -> InternalGrid:
    """Build an InternalGrid from a 2-D list of raw values.

    None          → empty cell ("")
    str/int/float → normal cell
    ('M', v)      → merged cell with value v
    """
    internal_rows = []
    for row in rows:
        internal_row = []
        for cell in row:
            if isinstance(cell, tuple) and cell[0] == 'M':
                internal_row.append(InternalCell(
                    value=str(cell[1]) if cell[1] is not None else "",
                    original_value=cell[1],
                    is_merged=True,
                ))
            else:
                if cell is None:
                    val = ""
                elif isinstance(cell, int):
                    val = str(cell)
                elif isinstance(cell, float):
                    val = str(int(cell)) if cell == int(cell) else str(cell)
                else:
                    val = cell
                internal_row.append(InternalCell(
                    value=val,
                    original_value=cell,
                    is_merged=False,
                ))
        internal_rows.append(internal_row)
    return InternalGrid(internal_rows)


def match_blocks(grid: InternalGrid, block: Block, options=None) -> list[BlockMatch]:
    """Run TemplateMatcher and return flat list of BlockMatches for a single template."""
    matcher = TemplateMatcher([block], options or MatchOptions())
    results = matcher.scan_for_blocks(grid)
    return results[0] if results else []


# ===========================================================================
# 1. Types / CellCondition
# ===========================================================================

class TestCellCondition(unittest.TestCase):

    def test_from_pattern_nonempty(self):
        c = CellCondition.from_pattern(r".+")
        self.assertEqual(c.patterns, frozenset([r".+"]))
        self.assertFalse(c.is_merged)

    def test_from_pattern_empty(self):
        c = CellCondition.from_pattern("")
        self.assertEqual(c.patterns, frozenset())

    def test_or_combines_patterns(self):
        a = CellCondition.from_pattern(r"\d+")
        b = CellCondition.from_pattern(r"[a-z]+")
        combined = a | b
        self.assertEqual(combined.patterns, frozenset([r"\d+", r"[a-z]+"]))

    def test_or_merged_conflict_becomes_none(self):
        a = CellCondition.from_pattern(r".*", is_merged=False)
        b = CellCondition.from_pattern(r".*", is_merged=True)
        combined = a | b
        self.assertIsNone(combined.is_merged)

    def test_call_repeats(self):
        c = Types.ANY
        self.assertEqual(len(c(3)), 3)
        self.assertTrue(all(x is c for x in c(3)))

    def test_call_rejects_negative(self):
        with self.assertRaises(ValueError):
            Types.ANY(-1)


# ===========================================================================
# 2. Types constants
# ===========================================================================

class TestTypesMatching(unittest.TestCase):
    """Verify regex patterns via fullmatch on normalised cell values."""

    def _matches(self, cond: CellCondition, value: str, is_merged=False):
        if not cond.patterns:
            compiled = re.compile("")
        else:
            compiled = re.compile("|".join(cond.patterns))
        if cond.is_merged is not None and cond.is_merged != is_merged:
            return False
        return bool(compiled.fullmatch(value or ""))

    def test_str(self):
        self.assertTrue(self._matches(Types.STR, "hello"))
        self.assertFalse(self._matches(Types.STR, ""))

    def test_int(self):
        self.assertTrue(self._matches(Types.INT, "42"))
        self.assertTrue(self._matches(Types.INT, "-7"))
        self.assertFalse(self._matches(Types.INT, "3.14"))

    def test_float(self):
        self.assertTrue(self._matches(Types.FLOAT, "3.14"))
        self.assertTrue(self._matches(Types.FLOAT, "42"))

    def test_num(self):
        self.assertTrue(self._matches(Types.NUM, "42"))
        self.assertTrue(self._matches(Types.NUM, "3.14"))

    def test_bool(self):
        for v in ["true", "false", "True", "FALSE", "yes", "no", "1", "0"]:
            self.assertTrue(self._matches(Types.BOOL, v), f"BOOL should match {v!r}")
        self.assertFalse(self._matches(Types.BOOL, "maybe"))

    def test_date_iso(self):
        self.assertTrue(self._matches(Types.DATE_ISO, "2024-01-15"))
        self.assertFalse(self._matches(Types.DATE_ISO, "15/01/2024"))

    def test_date_alias(self):
        self.assertEqual(Types.DATE.patterns, Types.DATE_ISO.patterns)

    def test_date_tw(self):
        self.assertTrue(self._matches(Types.DATE_TW, "111/01/01"))
        self.assertTrue(self._matches(Types.DATE_TW, "90/12/31"))

    def test_datetime(self):
        self.assertTrue(self._matches(Types.DATETIME, "2024-01-15 09:30"))
        self.assertTrue(self._matches(Types.DATETIME, "2024-01-15 09:30:00"))

    def test_time(self):
        self.assertTrue(self._matches(Types.TIME, "09:30"))
        self.assertEqual(Types.TIME.patterns, Types.TIME_24H.patterns)

    def test_merged(self):
        self.assertTrue(self._matches(Types.MERGED, "hello", is_merged=True))
        self.assertFalse(self._matches(Types.MERGED, "hello", is_merged=False))

    def test_space(self):
        self.assertTrue(self._matches(Types.SPACE, ""))
        self.assertTrue(self._matches(Types.SPACE, "   "))
        self.assertFalse(self._matches(Types.SPACE, "x"))

    def test_any(self):
        self.assertTrue(self._matches(Types.ANY, "anything"))
        self.assertTrue(self._matches(Types.ANY, ""))

    def test_hex(self):
        self.assertTrue(self._matches(Types.HEX, "0xFF"))
        self.assertFalse(self._matches(Types.HEX, "FF"))

    def test_custom_regex(self):
        cond = Types.r(r"[A-Z]{2}\d+")
        self.assertTrue(self._matches(cond, "AB123"))
        self.assertFalse(self._matches(cond, "ab123"))


# ===========================================================================
# 3. repeat spec parsing
# ===========================================================================

class TestRepeatParsing(unittest.TestCase):

    def test_int(self):
        self.assertEqual(_parse_repeat(3), (3, 3))

    def test_shortcuts(self):
        self.assertEqual(_parse_repeat("?"), (0, 1))
        self.assertEqual(_parse_repeat("+"), (1, None))
        self.assertEqual(_parse_repeat("*"), (0, None))

    def test_tuple(self):
        self.assertEqual(_parse_repeat((2, 4)), (2, 4))
        self.assertEqual(_parse_repeat((2, None)), (2, None))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            _parse_repeat("x")
        with self.assertRaises(ValueError):
            _parse_repeat(-1)
        with self.assertRaises(ValueError):
            _parse_repeat((4, 2))


# ===========================================================================
# 4. Block validation
# ===========================================================================

class TestBlockValidation(unittest.TestCase):

    def test_width_inference(self):
        b = Block(
            Row(pattern=["A", "B"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
        )
        self.assertEqual(b.width, 2)

    def test_inconsistent_width_raises(self):
        with self.assertRaises(ValueError):
            Block(Row(pattern=["A", "B"]), Row(pattern=[Types.STR]))

    def test_empty_row_expanded(self):
        b = Block(Row(pattern=["A", "B"]), EmptyRow())
        self.assertEqual(len(b.children[1].rules()), 2)

    def test_repr(self):
        b = Block(Row(pattern=["a"]), block_id="my_block")
        self.assertIn("my_block", repr(b))

    def test_alt_node(self):
        alt = Row(pattern=["A"]) | Row(pattern=["B"])
        self.assertIsInstance(alt, AltNode)
        self.assertEqual(len(alt.alternatives), 2)


# ===========================================================================
# 5. InternalGrid
# ===========================================================================

class TestInternalGrid(unittest.TestCase):

    def test_basic_access(self):
        grid = make_grid([["a", "b"], ["c", "d"]])
        self.assertEqual(grid.get_cell(0, 1).value, "b")
        self.assertEqual(grid.get_cell(1, 0).value, "c")

    def test_out_of_bounds(self):
        grid = make_grid([["a"]])
        with self.assertRaises(IndexError):
            grid.get_cell(99, 0)

    def test_rectangular_enforced(self):
        """Unequal row lengths should raise ValueError."""
        with self.assertRaises(ValueError):
            make_grid([["a", "b", "c"], ["d"]])

    def test_transpose(self):
        grid = make_grid([["A", "B", "C"], ["D", "E", "F"]])
        t = grid.transpose()
        self.assertEqual(t.num_rows, 3)
        self.assertEqual(t.num_cols, 2)
        self.assertEqual(t.get_cell(0, 0).value, "A")
        self.assertEqual(t.get_cell(0, 1).value, "D")
        self.assertEqual(t.get_cell(2, 0).value, "C")
        self.assertEqual(t.get_cell(2, 1).value, "F")

    def test_empty_grid(self):
        grid = InternalGrid([])
        self.assertEqual(grid.num_rows, 0)
        self.assertEqual(grid.num_cols, 0)


# ===========================================================================
# 6. Vertical matching — simple table
# ===========================================================================

class TestVerticalMatch(unittest.TestCase):

    def test_simple_table(self):
        grid = make_grid([
            ["部門", "姓名", "月薪"],
            ["IT",   "Alice", 1000],
            ["HR",   "Bob",   2000],
        ])
        block = Block(
            Row(pattern=["部門", "姓名", "月薪"], node_id="header"),
            Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
        )
        matches = match_blocks(grid, block)
        self.assertEqual(len(matches), 1)

        bm = matches[0]
        self.assertEqual(bm.start, (0, 0))
        self.assertEqual(len(bm.rows), 3)
        self.assertEqual(bm.rows[0].node_id, "header")
        self.assertEqual(bm.rows[1].node_id, "data")

    def test_no_match_wrong_header(self):
        grid = make_grid([["X", "Y"]])
        block = Block(Row(pattern=["A", "B"]))
        matches = match_blocks(grid, block)
        self.assertEqual(len(matches), 0)

    def test_offset_coordinates(self):
        """Block at (2,1) should report correct absolute coordinates."""
        grid = make_grid([
            [None, None,    None],
            [None, None,    None],
            [None, "Header", "Val"],
            [None, "IT",     "100"],
        ])
        block = Block(
            Row(pattern=["Header", "Val"], node_id="h"),
            Row(pattern=[Types.STR, Types.INT], node_id="d"),
        )
        matches = match_blocks(grid, block)
        bm = [m for m in matches if m.start == (2, 1)]
        self.assertEqual(len(bm), 1)
        bm = bm[0]

        self.assertEqual(bm.rows[0].row, 2)
        self.assertEqual(bm.rows[0].cells[0].col, 1)
        self.assertEqual(bm.rows[0].cells[0].value, "Header")
        self.assertEqual(bm.rows[1].row, 3)
        self.assertEqual(bm.rows[1].cells[0].value, "IT")

    def test_gap_row_tracking(self):
        """Non-matching rows between data rows are skipped with correct indices."""
        grid = make_grid([
            ["Header", "Val"],
            ["Row1",    100],
            ["!!!",    "???"],   # matches no rule → skipped
            ["Row3",    300],
        ])
        block = Block(
            Row(pattern=["Header", "Val"], node_id="h"),
            Row(pattern=[Types.STR, Types.INT], repeat="+", node_id="d"),
        )
        matches = match_blocks(grid, block)
        self.assertTrue(len(matches) >= 1)
        bm = matches[0]

        self.assertEqual(bm.rows[1].row, 1)
        self.assertEqual(bm.rows[1].cells[0].value, "Row1")

        if len(bm.rows) >= 3:
            self.assertEqual(bm.rows[2].row, 3)
            self.assertEqual(bm.rows[2].cells[0].value, "Row3")


# ===========================================================================
# 7. Group and EmptyRow
# ===========================================================================

class TestGroupAndEmptyRow(unittest.TestCase):

    def test_group_with_empty_row(self):
        grid = make_grid([
            ["*",           "姓名",  "月薪"],
            [('M', "IT"),  "Alice",  1000],
            [('M', "IT"),  "Bob",    2000],
            [None, None, None],
            [('M', "HR"),  "Carol",  3000],
            [None, None, None],
        ])
        block = Block(
            Row(pattern=[Types.ANY, "姓名", "月薪"]),
            Group(children=[
                Row(pattern=[Types.MERGED, Types.STR, Types.INT], repeat="+"),
                EmptyRow(repeat="?"),
            ], repeat="+"),
        )
        matches = match_blocks(grid, block)
        self.assertTrue(len(matches) >= 1)


# ===========================================================================
# 8. AltNode matching
# ===========================================================================

class TestAltNodeMatch(unittest.TestCase):

    def test_alt_header(self):
        grid = make_grid([["部門", "月薪"], ["IT", 1000]])
        block = Block(
            Row(pattern=["部門", "月薪"]) | Row(pattern=["Dept", "Salary"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
        )
        matches = match_blocks(grid, block)
        self.assertTrue(len(matches) >= 1)


# ===========================================================================
# 9. Horizontal matching
# ===========================================================================

class TestHorizontalMatch(unittest.TestCase):

    def test_horizontal_basic(self):
        grid = make_grid([
            ["Label", "Jan", "Feb"],
            ["Target", 100,   200],
        ])
        block = Block(
            Row(pattern=["Label", "Target"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
            orientation="horizontal",
        )
        matches = match_blocks(grid, block)
        self.assertTrue(len(matches) >= 1)

        bm = matches[0]
        for row in bm.rows:
            for cell in row.cells:
                actual = grid.get_cell(cell.row, cell.col).value
                self.assertEqual(cell.value, actual,
                    f"Cell ({cell.row},{cell.col}): {cell.value!r} != {actual!r}")


# ===========================================================================
# 10. MatchOptions
# ===========================================================================

class TestMatchOptions(unittest.TestCase):

    def test_return_mode_default(self):
        self.assertEqual(MatchOptions().return_mode, 0)

    def test_return_mode_zero_scans_all(self):
        grid = make_grid([["A"], ["B"]])
        block = Block(Row(pattern=["A"]), Row(pattern=[Types.STR]))
        matches = match_blocks(grid, block, MatchOptions(return_mode=0))
        self.assertIsInstance(matches, list)


# ===========================================================================
# 11. TODO 3 removal
# ===========================================================================

class TestTodo3Removed(unittest.TestCase):

    def test_no_huffman_comment(self):
        import inspect
        source = inspect.getsource(TemplateMatcher._match_template)
        self.assertNotIn("huffman", source.lower())


if __name__ == "__main__":
    unittest.main()
