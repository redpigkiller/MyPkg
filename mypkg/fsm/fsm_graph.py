"""FSMGraph — Interprocedural CFG for FSM-with-subroutine analysis.

Models an FSM that supports ``call``/``ret`` instructions by maintaining:

* A *main* CFG representing the top-level state flow.
* An optional dictionary of *function* CFGs for subroutines.
* A *call graph* tracking which blocks call which functions.

This design is called an Interprocedural CFG (ICFG) in compiler literature.
The hardware target (NCTL) is still referred to as an FSM; this class simply
provides a richer representation to support analysis of its call semantics.

Typical usage::

    fsm = FSMGraph()

    # --- main flow ---
    fsm.add_state("IDLE",  actions=["clr_cnt"])
    fsm.add_state("FETCH", actions=["load_insn"])
    fsm.add_state("DONE")
    fsm.add_transition("IDLE",  "FETCH", cond="start")
    fsm.add_transition("FETCH", "IDLE",  cond="loop")
    fsm.add_transition("FETCH", "DONE",  cond="halt")
    fsm.set_reset("IDLE")
    fsm.set_terminal("DONE")

    # --- subroutine ---
    from mypkg.cfg import CFG
    fn_cfg = CFG()
    fn_cfg.add_block("fn_a", insns=["ADD R0, #1"])
    fn_cfg.add_block("fn_ret", insns=["ret"])
    fn_cfg.add_edge("fn_a", "fn_ret")
    fn_cfg.set_entry("fn_a")
    fn_cfg.set_exit("fn_ret")
    fsm.add_function("func_a", fn_cfg)
    fsm.add_call_site(fn="main", block_id="FETCH", callee="func_a")
    fsm.add_return(fn="func_a", block_id="fn_ret")

    # --- checks ---
    fsm.find_dead_states()
    fsm.find_dead_loops()
    fsm.check_call_depth(max_depth=2)
    fsm.check_single_return("func_a")
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from mypkg.cfg import CFG, BasicBlock


# The name used for the main flow when stored in self._functions
_MAIN = "main"


class FSMGraph:
    """Interprocedural CFG for FSM programs with subroutine support.

    Args:
        max_call_depth: Default maximum allowed call-stack depth used by
                        :meth:`check_call_depth` when no explicit limit is
                        passed.  Set to ``None`` to disable the default limit.
    """

    def __init__(self, max_call_depth: int | None = None) -> None:
        self._main_cfg: CFG = CFG()
        self._functions: dict[str, CFG] = {_MAIN: self._main_cfg}
        # call graph: directed graph of function names
        self._call_graph: nx.DiGraph = nx.DiGraph()
        self._call_graph.add_node(_MAIN)
        # call_sites[fn][block_id] = callee_fn_name
        self._call_sites: dict[str, dict[str, str]] = {_MAIN: {}}
        # return_blocks[fn] = set of block_ids that contain "ret"
        self._return_blocks: dict[str, set[str]] = {}
        self._max_call_depth = max_call_depth

    # ------------------------------------------------------------------
    # Main-flow construction helpers
    # ------------------------------------------------------------------

    def add_state(
        self,
        state_id: str,
        actions: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> BasicBlock:
        """Add a state to the **main** FSM flow.

        *actions* maps to ``BasicBlock.insns``; *meta* is passed through.
        """
        return self._main_cfg.add_block(state_id, insns=actions or [], meta=meta or {})

    def add_transition(
        self,
        src: str,
        dst: str,
        cond: str = "",
        label: str = "",
    ) -> None:
        """Add a transition (edge) between two states in the main flow."""
        self._main_cfg.add_edge(src, dst, label=label, cond=cond)

    def set_reset(self, state_id: str) -> None:
        """Mark *state_id* as the reset (entry) state."""
        self._main_cfg.set_entry(state_id)

    def set_terminal(self, state_id: str) -> None:
        """Mark *state_id* as the terminal (exit) state.

        If the FSM has no explicit terminal state, dead-loop detection will
        treat any SCC with no outgoing edges as a dead loop.
        """
        self._main_cfg.set_exit(state_id)

    # ------------------------------------------------------------------
    # Subroutine management
    # ------------------------------------------------------------------

    def add_function(self, name: str, cfg: CFG) -> None:
        """Register a function CFG.

        Args:
            name: Unique function name.
            cfg:  A fully constructed :class:`~mypkg.cfg.CFG` for the function.
        """
        if name == _MAIN:
            raise ValueError(f"{_MAIN!r} is reserved for the main flow.")
        if name in self._functions:
            raise ValueError(f"Function {name!r} already registered.")
        self._functions[name] = cfg
        self._call_graph.add_node(name)
        self._call_sites.setdefault(name, {})

    def add_call_site(self, fn: str, block_id: str, callee: str) -> None:
        """Record that block *block_id* inside *fn* calls *callee*.

        Args:
            fn:       Caller function name (use ``"main"`` for the main flow).
            block_id: Block id inside *fn* that contains the ``call`` instruction.
            callee:   Name of the function being called.
        """
        for n in (fn, callee):
            if n not in self._functions:
                raise KeyError(f"Function {n!r} not registered.")
        cfg = self._functions[fn]
        if block_id not in cfg:
            raise KeyError(f"Block {block_id!r} not found in function {fn!r}.")
        self._call_sites.setdefault(fn, {})[block_id] = callee
        self._call_graph.add_edge(fn, callee)

    def add_return(self, fn: str, block_id: str) -> None:
        """Mark *block_id* inside *fn* as a return block (contains ``ret``).

        Args:
            fn:       Function name (use ``"main"`` for main flow if applicable).
            block_id: Block id that ends with a ``ret`` instruction.
        """
        if fn not in self._functions:
            raise KeyError(f"Function {fn!r} not registered.")
        cfg = self._functions[fn]
        if block_id not in cfg:
            raise KeyError(f"Block {block_id!r} not found in function {fn!r}.")
        self._return_blocks.setdefault(fn, set()).add(block_id)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def find_dead_states(self) -> list[BasicBlock]:
        """Return main-flow states unreachable from the reset state.

        Delegates to :meth:`CFG.find_unreachable` on the main CFG.
        """
        return self._main_cfg.find_unreachable()

    def find_dead_loops(self) -> list[list[str]]:
        """Return dead loops in the main flow.

        A dead loop is a cycle from which no state can reach the terminal
        state.  If no terminal state is set, any SCC with no outgoing edges
        (within the reachable portion) is considered a dead loop.

        Returns:
            List of dead loops; each loop is a sorted list of state ids.
        """
        main = self._main_cfg
        exit_id = main._exit

        if exit_id is not None:
            # Standard dead-loop detection using exit reachability
            return main.find_dead_loops(exit_node=exit_id)

        # No terminal: find SCCs (with cycles) that have no outgoing edges
        dead: list[list[str]] = []
        if main._entry is None:
            return dead
        reachable_ids = {
            bb.id for bb in main.dfs()
        }
        for scc in main.find_sccs():
            scc_set = set(scc)
            if not scc_set.intersection(reachable_ids):
                continue
            # Check for self-loop or multi-node cycle
            has_cycle = len(scc) > 1 or main._graph.has_edge(scc[0], scc[0])
            if not has_cycle:
                continue
            # Check for any outgoing edge to a node outside the SCC
            has_exit_edge = any(
                succ not in scc_set
                for n in scc
                for succ in main._graph.successors(n)
            )
            if not has_exit_edge:
                dead.append(sorted(scc))
        return dead

    def check_call_depth(self, max_depth: int | None = None) -> int:
        """Check that the call graph depth does not exceed *max_depth*.

        Args:
            max_depth: Maximum allowed call-stack depth.  If ``None``, uses the
                       instance default set at construction.  If both are
                       ``None``, only the actual depth is returned without
                       raising.

        Returns:
            The actual maximum call depth found in the call graph.

        Raises:
            ValueError: If a call-graph cycle is detected (recursive calls).
            ValueError: If the depth exceeds *max_depth*.
        """
        limit = max_depth if max_depth is not None else self._max_call_depth
        try:
            # Compute longest path from "main" in the call graph DAG
            depth = nx.dag_longest_path_length(self._call_graph)
        except nx.NetworkXUnfeasible:
            raise ValueError(
                "Call graph contains a cycle (recursive calls detected)."
            )
        if limit is not None and depth > limit:
            raise ValueError(
                f"Call depth {depth} exceeds the allowed maximum of {limit}."
            )
        return depth

    def check_single_return(self, fn: str) -> None:
        """Verify that function *fn* has exactly one return block.

        Args:
            fn: Function name to check.

        Raises:
            KeyError:   If *fn* is not registered.
            ValueError: If the function has zero or more than one return block.
        """
        if fn not in self._functions:
            raise KeyError(f"Function {fn!r} not registered.")
        ret_blocks = self._return_blocks.get(fn, set())
        if len(ret_blocks) == 0:
            raise ValueError(
                f"Function {fn!r} has no return block registered. "
                "Call add_return() to mark the ret block."
            )
        if len(ret_blocks) > 1:
            raise ValueError(
                f"Function {fn!r} has {len(ret_blocks)} return blocks "
                f"({sorted(ret_blocks)}); expected exactly 1."
            )

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def linearize(self, strategy: str = "rpo") -> list[str]:
        """Return the linearized order of states in the **main** flow.

        See :meth:`CFG.linearize` for strategy options.
        """
        return self._main_cfg.linearize(strategy=strategy)  # type: ignore[arg-type]

    def get_cfg(self, fn: str = _MAIN) -> CFG:
        """Return the underlying :class:`CFG` for *fn* (default: main flow)."""
        if fn not in self._functions:
            raise KeyError(f"Function {fn!r} not registered.")
        return self._functions[fn]

    # Convenience alias kept for backward-compat
    @property
    def main_cfg(self) -> CFG:
        """The main-flow CFG object."""
        return self._main_cfg
