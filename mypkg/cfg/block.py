"""BasicBlock — the fundamental node of a Control Flow Graph."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BasicBlock:
    """A node in a CFG representing a straight-line sequence of instructions.

    Attributes:
        id:    Unique identifier for this block (also used as the graph node key).
        insns: Ordered list of instructions.  Elements may be plain strings or
               any IR object — CFG does not interpret them.
        meta:  Arbitrary key/value metadata (e.g. source location, block type).
    """

    id: str
    insns: list[Any] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"BasicBlock({self.id!r}, insns={len(self.insns)})"

    def __hash__(self) -> int:          # so it can be used in sets / dict keys
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BasicBlock):
            return self.id == other.id
        return NotImplemented
