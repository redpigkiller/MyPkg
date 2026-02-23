"""Dead-block elimination for CFG — structural optimisation.

This module removes unreachable basic blocks from a CFG.  It operates
purely on the *structure* of the graph (reachability from the entry block)
and does not inspect instruction semantics.
"""

from __future__ import annotations

from mypkg.cfg import CFG, BasicBlock


def eliminate_dead_blocks(cfg: CFG, start: str | None = None) -> list[BasicBlock]:
    """Remove all blocks unreachable from *start* (or ``cfg.entry``).

    Args:
        cfg:   The :class:`~mypkg.cfg.CFG` to modify **in place**.
        start: Entry block id.  Defaults to ``cfg._entry``.

    Returns:
        The list of :class:`~mypkg.cfg.BasicBlock` objects that were removed.

    Raises:
        RuntimeError: If no entry is set and *start* is not provided.
    """
    dead = cfg.find_unreachable(start)
    for bb in dead:
        cfg._graph.remove_node(bb.id)
    return dead
