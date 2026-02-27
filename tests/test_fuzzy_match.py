import unittest
from mypkg.excel_extractor.template import Row
from mypkg.excel_extractor.types import Types
from mypkg.excel_extractor.matcher import _cell_matches
from mypkg.excel_extractor.normalizer import InternalCell

class TestRowNormalizeFuzzy(unittest.TestCase):
    def setUp(self):
        try:
            import rapidfuzz
            self._rapidfuzz_available = True
        except ImportError:
            self._rapidfuzz_available = False

    def test_normalize_default(self):
        row = Row([Types.r("hello world")], normalize=True)
        # Assuming internal cell matches " Hello World " with "hello world"
        cell = InternalCell(value=" Hello World ", original_value=" Hello World ")
        cond = row.pattern[0]
        # Exact regex match
        self.assertTrue(_cell_matches(cell, cond, normalize=True))
        self.assertFalse(_cell_matches(cell, cond, normalize=False))
        
    def test_fuzzy_match(self):
        if not self._rapidfuzz_available:
            self.skipTest("rapidfuzz not installed")

        row = Row(["Employee Name", "Salary"], fuzzy=0.8)
        cond1 = row.pattern[0]
        
        cell1 = InternalCell(value="Empoyee Name", original_value="Empoyee Name")
        
        # Exact match should fail
        self.assertFalse(_cell_matches(cell1, cond1, fuzzy=None))
        
        # Fuzzy match should pass (ratio > 0.8)
        self.assertTrue(_cell_matches(cell1, cond1, fuzzy=0.8))

        cell2 = InternalCell(value="Completely Different", original_value="Completely Different")
        self.assertFalse(_cell_matches(cell2, cond1, fuzzy=0.8))
