"""MyPkg — A collection of utilities for IC design & verification."""

from mypkg.data_types.mapbv import MapBV, MapBVExpr, const, var, concat
from mypkg.scheduler import JobManager, Job, CmdJob, FuncJob

try:
    from mypkg.cfg import CFG, BasicBlock, NaturalLoop
    from mypkg.fsm import FSMGraph
    from mypkg.mcu import LivenessAnalysis, eliminate_dead_blocks
    HAS_CFG = True
except ImportError:
    HAS_CFG = False

try:
    from mypkg.excel_extractor import (
        match_template,
        Block, Row, EmptyRow, Group,
        Types, CellCondition,
        MatchOptions, BlockMatch, RowMatch, CellMatch,
    )
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False

try:
    from mypkg.data_types.numbv import NumBV
    from mypkg.data_types.numbvarray import NumBVArray
    HAS_MATH = True
except ImportError:
    class NumBV:
        def __init__(self, *args, **kwargs):
            raise ImportError("NumBV requires fxpmath. Install with: pip install mypkg[math] or pip install fxpmath")
            
    class NumBVArray:
        def __init__(self, *args, **kwargs):
            raise ImportError("NumBVArray requires fxpmath. Install with: pip install mypkg[math] or pip install fxpmath")
    HAS_MATH = False

__all__ = [
    # data types
    "MapBV", "MapBVExpr",
    "const", "var", "concat",
    "NumBV",
    "NumBVArray",
    # scheduler
    "JobManager", "Job", "CmdJob", "FuncJob",
    # excel
    "match_template",
    "Block", "Row", "EmptyRow", "Group",
    "Types", "CellCondition",
    "MatchOptions", "BlockMatch", "RowMatch", "CellMatch",
    # cfg / fsm / mcu
    "CFG", "BasicBlock", "NaturalLoop",
    "FSMGraph",
    "LivenessAnalysis", "eliminate_dead_blocks",
]
