"""mypkg.excel_extractor — Template-based Excel data extraction engine."""

from mypkg.excel_extractor.types import CellCondition, Types
from mypkg.excel_extractor.template import Row, Col, EmptyRow, EmptyCol, Group, Block
from mypkg.excel_extractor.result import (
    NodeResult, MatchResult, NearMissHint, MatchOutput, MatchOptions,
)
from mypkg.excel_extractor.matcher import match_template, excel_range

__all__ = [
    "match_template",
    "excel_range",
    "Block", "Row", "Col", "EmptyRow", "EmptyCol", "Group",
    "Types", "CellCondition",
    "MatchOptions", "MatchOutput", "MatchResult", "NearMissHint", "NodeResult",
]
