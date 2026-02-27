import unittest
from mypkg.excel_extractor.template import Row
from mypkg.excel_extractor.types import Types

class TestTypesAnyN(unittest.TestCase):
    def test_types_any_n(self):
        row = Row(["Name", Types.ANY(3), "Salary"])
        
        # Types.ANY(3) should be expanded to 3 Types.ANY objects
        self.assertEqual(len(row.pattern), 5)
        
        # Verify the pattern contents
        self.assertEqual(row.pattern[0].pattern, "Name")
        self.assertTrue(row.pattern[1].any_val)
        self.assertTrue(row.pattern[2].any_val)
        self.assertTrue(row.pattern[3].any_val)
        self.assertEqual(row.pattern[4].pattern, "Salary")

    def test_types_any_n_invalid(self):
        with self.assertRaises(ValueError):
            Types.ANY(-1)
        
        with self.assertRaises(ValueError):
            Types.ANY("3")
