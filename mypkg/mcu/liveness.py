"""LivenessAnalysis — backward dataflow analysis on a CFG.

Liveness analysis determines, for each point in a program, which variables
are *live* (will be used before being redefined on some path).  This is the
mandatory predecessor of register allocation.

Usage::

    from mypkg.cfg import CFG
    from mypkg.mcu.liveness import LivenessAnalysis

    # Supply a callable that returns (defs, uses) for each instruction.
    def my_def_use(insn: str):
        # trivial example parser — replace with your real IR logic
        defs, uses = set(), set()
        if '=' in insn:
            lhs, rhs = insn.split('=', 1)
            defs.add(lhs.strip())
            uses.update(t.strip() for t in rhs.split() if t.strip().isidentifier())
        return defs, uses

    la = LivenessAnalysis(cfg, def_use_fn=my_def_use)
    la.run()
    print(la.live_in["entry"])   # frozenset of variable names live at entry
    print(la.live_out["bb1"])    # frozenset of variable names live at exit of bb1
"""

from __future__ import annotations

from typing import Any, Callable, FrozenSet, Tuple

from mypkg.cfg import CFG


DefUseFn = Callable[[Any], Tuple[set, set]]


class LivenessAnalysis:
    """Backward dataflow analysis computing live-in / live-out sets per block.

    The analysis uses the standard iterative worklist algorithm:

    .. code-block:: text

        live_out[b] = ⋃  live_in[s]  for each successor s of b
        live_in[b]  = use[b] ∪ (live_out[b] − def[b])

    iterate until a fixed point is reached.

    Args:
        cfg:        The :class:`~mypkg.cfg.CFG` to analyse.
        def_use_fn: A callable ``(insn) → (defs: set, uses: set)`` that
                    returns the variables *defined* and *used* by a single
                    instruction.  The sets may contain any hashable objects
                    (strings, register names, IR nodes, …).
    """

    def __init__(self, cfg: CFG, def_use_fn: DefUseFn) -> None:
        self._cfg = cfg
        self._def_use_fn = def_use_fn
        # Results — populated by run()
        self.live_in: dict[str, FrozenSet] = {}
        self.live_out: dict[str, FrozenSet] = {}
        self._block_def: dict[str, set] = {}
        self._block_use: dict[str, set] = {}
        self._computed = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Execute the iterative liveness analysis until a fixed point."""
        cfg = self._cfg
        g = cfg._graph

        # Initialise per-block def / use sets (in instruction order)
        for bb in cfg.blocks:
            blk_def: set = set()
            blk_use: set = set()
            for insn in bb.insns:
                d, u = self._def_use_fn(insn)
                # A variable used before it is defined (in this block) is live-in
                blk_use |= u - blk_def
                blk_def |= d
            self._block_def[bb.id] = blk_def
            self._block_use[bb.id] = blk_use

        # Initialise live sets
        for bb in cfg.blocks:
            self.live_in[bb.id] = frozenset()
            self.live_out[bb.id] = frozenset()

        # Worklist: start with all blocks
        worklist: set[str] = {bb.id for bb in cfg.blocks}
        while worklist:
            bid = worklist.pop()
            # live_out[b] = union of live_in of all successors
            new_out: set = set()
            for succ in g.successors(bid):
                new_out |= self.live_in[succ]
            new_out_frozen = frozenset(new_out)

            # live_in[b] = use[b] ∪ (live_out[b] − def[b])
            new_in = frozenset(
                self._block_use[bid] | (new_out - self._block_def[bid])
            )

            if new_out_frozen != self.live_out[bid] or new_in != self.live_in[bid]:
                self.live_out[bid] = new_out_frozen
                self.live_in[bid] = new_in
                # Propagate change to predecessors
                for pred in g.predecessors(bid):
                    worklist.add(pred)

        self._computed = True

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_live_at_entry(self, block_id: str, var: Any) -> bool:
        """Return True if *var* is live at the entry of *block_id*."""
        return var in self.live_in[block_id]

    def is_live_at_exit(self, block_id: str, var: Any) -> bool:
        """Return True if *var* is live at the exit of *block_id*."""
        return var in self.live_out[block_id]
