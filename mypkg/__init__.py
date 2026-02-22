"""MyPkg — A collection of utilities for IC design & verification."""

from mypkg.data_types.mapbv import MapBV, MapBVSlice, MapBVExpr, StructSegment
from mypkg.scheduler import Scheduler, Job, CmdJob

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
    "MapBV", "MapBVSlice", "MapBVExpr", "StructSegment",
    "NumBV",
    "NumBVArray",
    "Scheduler", "Job", "CmdJob",
]
