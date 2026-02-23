"""CFG — Control Flow Graph with analysis algorithms.

Backend: networkx DiGraph.  All graph operations delegate to networkx;
this class adds CFG semantics (entry/exit, basic block storage, analysis).
"""

from __future__ import annotations

from typing import Any, Callable, Generator, Iterable, Literal

import networkx as nx

from .block import BasicBlock


# ---------------------------------------------------------------------------
# Helper dataclass
# ---------------------------------------------------------------------------

class NaturalLoop:
    """Represents a natural loop found in the CFG."""

    def __init__(self, header: str, body: set[str], back_edge: tuple[str, str]):
        self.header = header        # loop-header block id
        self.body = body            # all block ids in the loop (includes header)
        self.back_edge = back_edge  # (tail, header)

    def __repr__(self) -> str:
        return f"NaturalLoop(header={self.header!r}, body={sorted(self.body)!r})"


# ---------------------------------------------------------------------------
# CFG
# ---------------------------------------------------------------------------

class CFG:
    """Control Flow Graph.

    Nodes are identified by string *block ids*.  Each node stores the
    corresponding :class:`BasicBlock` as a node attribute.

    Example::

        cfg = CFG()
        cfg.add_block("entry", insns=["MOV A, #0"])
        cfg.add_block("bb1",   insns=["ADD A, #5"])
        cfg.add_block("end",   insns=["NOP"])
        cfg.add_edge("entry", "bb1", label="cond_true")
        cfg.add_edge("entry", "end", label="cond_false")
        cfg.add_edge("bb1",   "end")
        cfg.set_entry("entry")
        cfg.set_exit("end")
    """

    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._entry: str | None = None
        self._exit: str | None = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_block(
        self,
        block_id: str,
        insns: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> BasicBlock:
        """Add a :class:`BasicBlock` and return it.

        Calling ``add_block`` with an existing *block_id* raises
        :class:`ValueError`.
        """
        if block_id in self._g:
            raise ValueError(f"Block {block_id!r} already exists in CFG.")
        bb = BasicBlock(id=block_id, insns=list(insns or []), meta=dict(meta or {}))
        self._g.add_node(block_id, block=bb)
        return bb

    def add_edge(
        self,
        src: str,
        dst: str,
        label: str = "",
        cond: str = "",
        **attrs: Any,
    ) -> None:
        """Add a directed edge from *src* to *dst*.

        Both blocks must already exist.  Duplicate edges are silently ignored
        (networkx DiGraph behaviour).
        """
        for bid in (src, dst):
            if bid not in self._g:
                raise KeyError(f"Block {bid!r} not found in CFG.")
        self._g.add_edge(src, dst, label=label, cond=cond, **attrs)

    def set_entry(self, block_id: str) -> None:
        """Designate *block_id* as the CFG entry (start) block."""
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        self._entry = block_id

    def set_exit(self, block_id: str) -> None:
        """Designate *block_id* as the CFG exit (end / halt) block."""
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        self._exit = block_id

    # ------------------------------------------------------------------
    # Block / edge access
    # ------------------------------------------------------------------

    def get_block(self, block_id: str) -> BasicBlock:
        """Return the :class:`BasicBlock` for *block_id*."""
        if block_id not in self._g:
            raise KeyError(f"Block {block_id!r} not found in CFG.")
        return self._g.nodes[block_id]["block"]

    @property
    def blocks(self) -> list[BasicBlock]:
        """All blocks in insertion order."""
        return [data["block"] for _, data in self._g.nodes(data=True)]

    @property
    def entry(self) -> BasicBlock | None:
        return self._g.nodes[self._entry]["block"] if self._entry else None

    @property
    def exit(self) -> BasicBlock | None:
        return self._g.nodes[self._exit]["block"] if self._exit else None

    def predecessors(self, block_id: str) -> list[BasicBlock]:
        return [self.get_block(p) for p in self._g.predecessors(block_id)]

    def successors(self, block_id: str) -> list[BasicBlock]:
        return [self.get_block(s) for s in self._g.successors(block_id)]

    def edge_attrs(self, src: str, dst: str) -> dict[str, Any]:
        return dict(self._g[src][dst])

    def __contains__(self, block_id: str) -> bool:
        return block_id in self._g

    def __len__(self) -> int:
        return len(self._g)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def _start(self) -> str:
        """Return the entry block id, or raise if not set."""
        if self._entry is None:
            raise RuntimeError("CFG entry is not set. Call set_entry() first.")
        return self._entry

    def dfs(self, start: str | None = None) -> Generator[BasicBlock, None, None]:
        """Yield blocks in depth-first order starting from *start* (or entry)."""
        root = start or self._start()
        for nid in nx.dfs_preorder_nodes(self._g, root):
            yield self.get_block(nid)

    def bfs(self, start: str | None = None) -> Generator[BasicBlock, None, None]:
        """Yield blocks in breadth-first order starting from *start* (or entry)."""
        root = start or self._start()
        for nid in nx.bfs_tree(self._g, root).nodes:
            yield self.get_block(nid)

    def reverse_postorder(self, start: str | None = None) -> list[BasicBlock]:
        """Return blocks in Reverse Post-Order (standard for dataflow analysis).

        RPO is computed via DFS: blocks are appended on leaving, then the list
        is reversed.  This guarantees that a block appears *before* all its
        successors in a forward pass (for DAG portions) and loop headers appear
        before their loop bodies.
        """
        root = start or self._start()
        visited: set[str] = set()
        postorder: list[str] = []

        def _dfs(node: str) -> None:
            visited.add(node)
            for succ in self._g.successors(node):
                if succ not in visited:
                    _dfs(succ)
            postorder.append(node)

        _dfs(root)
        return [self.get_block(nid) for nid in reversed(postorder)]

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def can_reach(self, src: str, dst: str) -> bool:
        """Return True if there is a path from *src* to *dst*."""
        return nx.has_path(self._g, src, dst)

    def find_unreachable(self, start: str | None = None) -> list[BasicBlock]:
        """Return all blocks that cannot be reached from *start* (or entry)."""
        root = start or self._start()
        reachable = nx.descendants(self._g, root) | {root}
        return [self.get_block(nid) for nid in self._g.nodes if nid not in reachable]

    def find_sccs(self) -> list[list[str]]:
        """Return all Strongly Connected Components as lists of block ids.

        Uses Kosaraju via networkx ``strongly_connected_components``.
        SCCs are returned in topological order of the condensation DAG
        (source SCCs first).
        """
        condensation = nx.condensation(self._g)
        result: list[list[str]] = []
        for node in nx.topological_sort(condensation):
            members = sorted(condensation.nodes[node]["members"])
            result.append(members)
        return result

    # ------------------------------------------------------------------
    # Loop analysis
    # ------------------------------------------------------------------

    def find_back_edges(self, start: str | None = None) -> list[tuple[str, str]]:
        """Return all DFS back-edges (edges pointing to an ancestor in DFS tree).

        A back-edge (u → v) means v is an ancestor of u in the DFS tree —
        the hallmark of a loop.
        """
        root = start or self._start()
        back_edges: list[tuple[str, str]] = []
        # networkx dfs_labeled_edges yields (u, v, direction)
        in_stack: set[str] = set()
        visited: set[str] = set()

        def _dfs(node: str) -> None:
            visited.add(node)
            in_stack.add(node)
            for succ in self._g.successors(node):
                if succ not in visited:
                    _dfs(succ)
                elif succ in in_stack:
                    back_edges.append((node, succ))
            in_stack.discard(node)

        _dfs(root)
        return back_edges

    def find_natural_loops(self, start: str | None = None) -> list[NaturalLoop]:
        """Return all natural loops identified by their back-edges.

        For each back-edge (tail → header), the natural loop body is the set of
        nodes that can reach *tail* without going through *header*.
        """
        back_edges = self.find_back_edges(start)
        loops: list[NaturalLoop] = []
        for tail, header in back_edges:
            # Nodes that can reach `tail` in the subgraph that excludes `header`
            # (except header itself which is always in the body)
            rev = self._g.reverse()
            subgraph = nx.subgraph_view(
                rev,
                filter_node=lambda n, h=header: n != h or n == h,
            )
            # BFS/DFS backward from tail, stopping at header
            body: set[str] = {header}
            worklist = [tail]
            while worklist:
                node = worklist.pop()
                if node in body:
                    continue
                body.add(node)
                for pred in rev.successors(node):  # pred in original = succ in rev
                    if pred != header and pred not in body:
                        worklist.append(pred)
            loops.append(NaturalLoop(header=header, body=body, back_edge=(tail, header)))
        return loops

    def find_dead_loops(
        self,
        start: str | None = None,
        exit_node: str | None = None,
    ) -> list[list[str]]:
        """Return SCCs that form dead loops — cycles with no path to *exit_node*.

        A dead loop is an SCC (with ≥ 1 back-edge, or a self-loop) from which
        no node can reach *exit_node*.  If *exit_node* is None, ``self._exit``
        is used; if neither is set, a :class:`RuntimeError` is raised.

        Args:
            start:     Entry block id (defaults to ``self._entry``).
            exit_node: Exit / terminal block id (defaults to ``self._exit``).

        Returns:
            List of dead loops, each represented as a sorted list of block ids.
        """
        exit_id = exit_node or self._exit
        if exit_id is None:
            raise RuntimeError(
                "CFG exit is not set.  Call set_exit() or pass exit_node."
            )

        # Reachable from entry
        root = start or self._start()
        reachable_from_entry = nx.descendants(self._g, root) | {root}

        # Nodes that can reach the exit (reverse reachability from exit)
        rev = self._g.reverse()
        can_reach_exit = nx.descendants(rev, exit_id) | {exit_id}

        dead: list[list[str]] = []
        for scc in self.find_sccs():
            if len(scc) == 1:
                node = scc[0]
                # Self-loop?
                if not self._g.has_edge(node, node):
                    continue
            # SCC with a cycle: check if it's reachable AND cannot exit
            if any(n in reachable_from_entry for n in scc):
                if not any(n in can_reach_exit for n in scc):
                    dead.append(scc)
        return dead

    # ------------------------------------------------------------------
    # Dominance analysis
    # ------------------------------------------------------------------

    def dominators(self, start: str | None = None) -> dict[str, str]:
        """Return the immediate dominator mapping {node: idom}.

        Uses networkx ``immediate_dominators`` (Lengauer-Tarjan).
        The entry node maps to itself.

        Note: networkx does not include the start node in its return dict;
        we add it explicitly so callers can always do ``idom[root] == root``.
        """
        root = start or self._start()
        idom = nx.immediate_dominators(self._g, root)
        idom.setdefault(root, root)   # networkx 3.x omits the root node
        return idom

    def post_dominators(self, exit_node: str | None = None) -> dict[str, str]:
        """Return the immediate post-dominator mapping {node: ipost_dom}.

        Computed as dominators on the reversed graph from *exit_node*.

        Note: networkx does not include the start node in its return dict;
        we add it explicitly so callers can always do ``idom[exit] == exit``.
        """
        exit_id = exit_node or self._exit
        if exit_id is None:
            raise RuntimeError(
                "CFG exit is not set.  Call set_exit() or pass exit_node."
            )
        idom = nx.immediate_dominators(self._g.reverse(), exit_id)
        idom.setdefault(exit_id, exit_id)   # networkx 3.x omits the root node
        return idom

    def dominator_tree(self, start: str | None = None) -> nx.DiGraph:
        """Return the dominator tree as a networkx DiGraph (idom → node edges)."""
        idom = self.dominators(start)
        tree = nx.DiGraph()
        tree.add_nodes_from(self._g.nodes)
        for node, dom in idom.items():
            if node != dom:
                tree.add_edge(dom, node)
        return tree

    # ------------------------------------------------------------------
    # Linearisation
    # ------------------------------------------------------------------

    def linearize(
        self,
        strategy: Literal["rpo", "topological"] = "rpo",
        start: str | None = None,
    ) -> list[str]:
        """Return an ordered list of block ids suitable for code emission.

        Args:
            strategy: ``"rpo"`` (default) — Reverse Post-Order, handles cycles.
                      ``"topological"`` — topological sort, raises if the graph
                      contains a cycle.
            start:    Entry block (defaults to ``self._entry``).

        Returns:
            Ordered list of block ids.
        """
        if strategy == "rpo":
            return [bb.id for bb in self.reverse_postorder(start)]
        elif strategy == "topological":
            try:
                return list(nx.topological_sort(self._g))
            except nx.NetworkXUnfeasible as exc:
                raise ValueError(
                    "Cannot use topological strategy: CFG contains a cycle. "
                    "Use strategy='rpo' instead."
                ) from exc
        else:
            raise ValueError(f"Unknown linearize strategy: {strategy!r}")

    # ------------------------------------------------------------------
    # Internal graph access (for subclasses / advanced use)
    # ------------------------------------------------------------------

    @property
    def _graph(self) -> nx.DiGraph:
        """Direct access to the underlying networkx DiGraph (advanced use)."""
        return self._g
