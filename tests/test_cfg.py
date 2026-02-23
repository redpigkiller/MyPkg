"""Tests for mypkg.cfg — CFG core module."""

import pytest
import networkx as nx

from mypkg.cfg import CFG, BasicBlock, NaturalLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_linear_cfg() -> CFG:
    """entry → bb1 → end  (pure DAG, no cycle)"""
    cfg = CFG()
    cfg.add_block("entry", insns=["MOV A, #0"])
    cfg.add_block("bb1",   insns=["ADD A, #5"])
    cfg.add_block("end",   insns=["NOP"])
    cfg.add_edge("entry", "bb1")
    cfg.add_edge("bb1",   "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_diamond_cfg() -> CFG:
    """
    entry → bb1 (cond_true)
    entry → bb2 (cond_false)
    bb1   → end
    bb2   → end
    """
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("bb1")
    cfg.add_block("bb2")
    cfg.add_block("end")
    cfg.add_edge("entry", "bb1", label="true")
    cfg.add_edge("entry", "bb2", label="false")
    cfg.add_edge("bb1",   "end")
    cfg.add_edge("bb2",   "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_loop_cfg() -> CFG:
    """
    entry → header → body → header  (loop)
           header → end
    """
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("header")
    cfg.add_block("body")
    cfg.add_block("end")
    cfg.add_edge("entry",  "header")
    cfg.add_edge("header", "body")
    cfg.add_edge("body",   "header")   # back-edge
    cfg.add_edge("header", "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_dead_loop_cfg() -> CFG:
    """
    entry → good → end
    entry → stuck → stuck  (self-loop, no exit)
    """
    cfg = CFG()
    cfg.add_block("entry")
    cfg.add_block("good")
    cfg.add_block("stuck")
    cfg.add_block("end")
    cfg.add_edge("entry", "good")
    cfg.add_edge("entry", "stuck")
    cfg.add_edge("good",  "end")
    cfg.add_edge("stuck", "stuck")   # dead loop
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_add_block_and_retrieve(self):
        cfg = CFG()
        bb = cfg.add_block("a", insns=["NOP"], meta={"src": 1})
        assert isinstance(bb, BasicBlock)
        assert bb.id == "a"
        assert bb.insns == ["NOP"]
        assert bb.meta == {"src": 1}
        assert cfg.get_block("a") is bb

    def test_add_duplicate_block_raises(self):
        cfg = CFG()
        cfg.add_block("a")
        with pytest.raises(ValueError, match="already exists"):
            cfg.add_block("a")

    def test_add_edge_unknown_block_raises(self):
        cfg = CFG()
        cfg.add_block("a")
        with pytest.raises(KeyError):
            cfg.add_edge("a", "b")

    def test_set_entry_exit(self):
        cfg = make_linear_cfg()
        assert cfg.entry.id == "entry"
        assert cfg.exit.id == "end"

    def test_set_entry_unknown_raises(self):
        cfg = CFG()
        with pytest.raises(KeyError):
            cfg.set_entry("ghost")

    def test_len_and_contains(self):
        cfg = make_linear_cfg()
        assert len(cfg) == 3
        assert "entry" in cfg
        assert "ghost" not in cfg

    def test_blocks_property(self):
        cfg = make_linear_cfg()
        ids = [bb.id for bb in cfg.blocks]
        assert set(ids) == {"entry", "bb1", "end"}

    def test_predecessors_successors(self):
        cfg = make_diamond_cfg()
        succs = {bb.id for bb in cfg.successors("entry")}
        assert succs == {"bb1", "bb2"}
        preds = {bb.id for bb in cfg.predecessors("end")}
        assert preds == {"bb1", "bb2"}

    def test_edge_attrs(self):
        cfg = make_diamond_cfg()
        attrs = cfg.edge_attrs("entry", "bb1")
        assert attrs["label"] == "true"


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------

class TestTraversal:
    def test_dfs_visits_all_reachable(self):
        cfg = make_diamond_cfg()
        visited = {bb.id for bb in cfg.dfs()}
        assert visited == {"entry", "bb1", "bb2", "end"}

    def test_bfs_visits_all_reachable(self):
        cfg = make_diamond_cfg()
        visited = {bb.id for bb in cfg.bfs()}
        assert visited == {"entry", "bb1", "bb2", "end"}

    def test_rpo_entry_first(self):
        cfg = make_loop_cfg()
        order = cfg.reverse_postorder()
        ids = [bb.id for bb in order]
        # entry must come before header, header before body/end
        assert ids.index("entry") < ids.index("header")
        assert ids.index("header") < ids.index("body")

    def test_dfs_no_entry_raises(self):
        cfg = CFG()
        cfg.add_block("a")
        with pytest.raises(RuntimeError):
            list(cfg.dfs())


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------

class TestReachability:
    def test_can_reach_true(self):
        cfg = make_linear_cfg()
        assert cfg.can_reach("entry", "end") is True

    def test_can_reach_false(self):
        cfg = make_diamond_cfg()
        # no edge from bb1 to bb2
        assert cfg.can_reach("bb1", "bb2") is False

    def test_find_unreachable_empty(self):
        cfg = make_linear_cfg()
        assert cfg.find_unreachable() == []

    def test_find_unreachable_detects_orphan(self):
        cfg = make_linear_cfg()
        cfg.add_block("orphan")       # not connected
        dead = cfg.find_unreachable()
        assert len(dead) == 1
        assert dead[0].id == "orphan"

    def test_find_sccs_no_cycle(self):
        cfg = make_linear_cfg()
        sccs = cfg.find_sccs()
        # all singletons
        all_members = [m for scc in sccs for m in scc]
        assert set(all_members) == {"entry", "bb1", "end"}
        assert all(len(scc) == 1 for scc in sccs)

    def test_find_sccs_detects_cycle(self):
        cfg = make_loop_cfg()
        sccs = cfg.find_sccs()
        # header + body form an SCC
        multi = [scc for scc in sccs if len(scc) > 1]
        assert len(multi) == 1
        assert set(multi[0]) == {"header", "body"}


# ---------------------------------------------------------------------------
# Loop analysis
# ---------------------------------------------------------------------------

class TestLoopAnalysis:
    def test_find_back_edges_no_loop(self):
        cfg = make_linear_cfg()
        assert cfg.find_back_edges() == []

    def test_find_back_edges_loop(self):
        cfg = make_loop_cfg()
        backs = cfg.find_back_edges()
        assert len(backs) == 1
        assert backs[0] == ("body", "header")

    def test_find_natural_loops(self):
        cfg = make_loop_cfg()
        loops = cfg.find_natural_loops()
        assert len(loops) == 1
        lp = loops[0]
        assert isinstance(lp, NaturalLoop)
        assert lp.header == "header"
        assert "body" in lp.body
        assert "header" in lp.body

    def test_no_dead_loop_in_good_cfg(self):
        cfg = make_loop_cfg()
        assert cfg.find_dead_loops() == []

    def test_dead_loop_self_loop(self):
        cfg = make_dead_loop_cfg()
        dead = cfg.find_dead_loops()
        assert len(dead) == 1
        assert dead[0] == ["stuck"]

    def test_dead_loop_requires_exit(self):
        cfg = make_loop_cfg()
        cfg._exit = None
        with pytest.raises(RuntimeError):
            cfg.find_dead_loops()

    def test_dead_loop_multi_node(self):
        """Two-node mutual loop with no exit."""
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("a")
        cfg.add_block("b")
        cfg.add_block("end")
        cfg.add_edge("entry", "a")
        cfg.add_edge("a", "b")
        cfg.add_edge("b", "a")   # dead loop: a↔b, no path to end
        cfg.add_edge("entry", "end")
        cfg.set_entry("entry")
        cfg.set_exit("end")
        dead = cfg.find_dead_loops()
        assert len(dead) == 1
        assert set(dead[0]) == {"a", "b"}


# ---------------------------------------------------------------------------
# Dominance
# ---------------------------------------------------------------------------

class TestDominance:
    def test_dominators_linear(self):
        cfg = make_linear_cfg()
        idom = cfg.dominators()
        assert idom["entry"] == "entry"
        assert idom["bb1"] == "entry"
        assert idom["end"] == "bb1"

    def test_dominators_diamond(self):
        cfg = make_diamond_cfg()
        idom = cfg.dominators()
        # entry dominates everything
        assert idom["bb1"] == "entry"
        assert idom["bb2"] == "entry"
        # "end" is dominated by entry (bb1 and bb2 both reach it)
        assert idom["end"] == "entry"

    def test_post_dominators(self):
        cfg = make_linear_cfg()
        idom = cfg.post_dominators()
        # post-dom from exit
        assert idom["end"] == "end"
        assert idom["bb1"] == "end"
        assert idom["entry"] == "bb1"

    def test_post_dominators_requires_exit(self):
        cfg = CFG()
        cfg.add_block("a")
        cfg.set_entry("a")
        with pytest.raises(RuntimeError):
            cfg.post_dominators()

    def test_dominator_tree_is_dag(self):
        cfg = make_diamond_cfg()
        tree = cfg.dominator_tree()
        assert nx.is_directed_acyclic_graph(tree)


# ---------------------------------------------------------------------------
# Linearisation
# ---------------------------------------------------------------------------

class TestLinearize:
    def test_rpo_linear(self):
        cfg = make_linear_cfg()
        order = cfg.linearize("rpo")
        assert order.index("entry") < order.index("bb1")
        assert order.index("bb1") < order.index("end")

    def test_topological_linear(self):
        cfg = make_linear_cfg()
        order = cfg.linearize("topological")
        assert order.index("entry") < order.index("bb1")
        assert order.index("bb1") < order.index("end")

    def test_topological_raises_on_cycle(self):
        cfg = make_loop_cfg()
        with pytest.raises(ValueError, match="cycle"):
            cfg.linearize("topological")

    def test_unknown_strategy_raises(self):
        cfg = make_linear_cfg()
        with pytest.raises(ValueError, match="Unknown"):
            cfg.linearize("magic")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_cfg(self):
        cfg = CFG()
        assert len(cfg) == 0
        assert cfg.blocks == []

    def test_single_block(self):
        cfg = CFG()
        cfg.add_block("a", insns=["NOP"])
        cfg.set_entry("a")
        cfg.set_exit("a")
        assert list(cfg.dfs()) == [cfg.get_block("a")]
        assert cfg.find_unreachable() == []

    def test_self_loop_is_cycle(self):
        cfg = CFG()
        cfg.add_block("a")
        cfg.add_block("end")
        cfg.add_edge("a", "a")   # self-loop
        cfg.add_edge("a", "end")
        cfg.set_entry("a")
        cfg.set_exit("end")
        backs = cfg.find_back_edges()
        assert ("a", "a") in backs

    def test_self_loop_not_dead_if_can_exit(self):
        """Self-loop that also has an edge to exit is NOT a dead loop."""
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("a")
        cfg.add_block("end")
        cfg.add_edge("entry", "a")
        cfg.add_edge("a", "a")    # self-loop
        cfg.add_edge("a", "end")  # but can exit
        cfg.set_entry("entry")
        cfg.set_exit("end")
        assert cfg.find_dead_loops() == []
