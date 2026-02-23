"""mypkg.mcu — MCU compiler analysis layer (minimal, CFG-based)."""

from .liveness import LivenessAnalysis
from .dce import eliminate_dead_blocks

__all__ = [
    "LivenessAnalysis",
    "eliminate_dead_blocks",
]
