import unittest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mypkg.excel_extractor.types import Types
from mypkg.excel_extractor.template import RecordBlock, Field
from test_excel_extractor import make_matcher, make_grid
from mypkg.excel_extractor.result import MatchOptions

class TestRecordBlock(unittest.TestCase):

    def setUp(self):
        self.grid = make_grid([
            ["ignore1", "pg#", "ignore2", "rg#", "Name", "ignore3"],
            ["x",       10,    "y",       20,    "RegA", "z"],
            ["a",       11,    "b",       21,    "RegB", "c"],
            [None,      None,  None,      None,  None,   None],
        ])

    def test_record_block_basic(self):
        block = RecordBlock(
            Field("pg#", Types.INT, name="pg"),
            Field("Name", Types.STR, name="name"),
            Field("rg#", Types.INT, name="rg"),
        )
        matcher = make_matcher(self.grid)
        result = matcher._try_match_record_block(0, 0, block)
        self.assertIsNotNone(result)
        
        # Should have 1 header node, 2 data nodes
        self.assertEqual(len(result.matched_nodes), 3)
        header = result.matched_nodes[0]
        self.assertEqual(header.cells, ["pg", "name", "rg"])
        
        data1 = result.matched_nodes[1]
        self.assertEqual(data1.cells, [10, "RegA", 20])
        
        data2 = result.matched_nodes[2]
        self.assertEqual(data2.cells, [11, "RegB", 21])

    def test_record_block_missing_header(self):
        block = RecordBlock(
            Field("missing_header", Types.INT),
        )
        matcher = make_matcher(self.grid)
        result = matcher._try_match_record_block(0, 0, block)
        self.assertIsNone(result)

    def test_record_block_to_dataframe(self):
        block = RecordBlock(
            Field("pg#", Types.INT, name="pg"),
            Field("Name", Types.STR, name="name"),
        )
        matcher = make_matcher(self.grid)
        result = matcher._try_match_record_block(0, 0, block)
        self.assertIsNotNone(result)
        
        df = result.to_dataframe(header_node="header")
        self.assertEqual(list(df.columns), ["pg", "name"])
        self.assertEqual(df.iloc[0]["pg"], 10)
        self.assertEqual(df.iloc[0]["name"], "RegA")

if __name__ == "__main__":
    unittest.main()
