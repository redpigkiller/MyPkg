"""Tests for mypkg.mcu — LivenessAnalysis and eliminate_dead_blocks."""

import pytest

from mypkg.cfg import CFG
from mypkg.mcu import LivenessAnalysis, eliminate_dead_blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def simple_def_use(insn: str):
    """
    Minimal def/use parser for test instructions.
    Format:  'DEF var'   → defines var, uses nothing
             'USE var'   → uses var, defines nothing
             'MOV a, b' → defines a, uses b
    """
    parts = insn.split()
    if not parts:
        return set(), set()
    op = parts[0].upper()
    if op == "DEF":
        return {parts[1]}, set()
    if op == "USE":
        return set(), {parts[1]}
    if op == "MOV" and len(parts) == 3:
        dst = parts[1].rstrip(",")
        src = parts[2]
        return {dst}, {src}
    return set(), set()


def make_liveness_cfg():
    """
    entry: [DEF x]
    bb1:   [MOV y, x]    uses x, defines y
    end:   [USE y]       uses y

    Expected:
        live_in[entry]  = {}      (x defined here)
        live_out[entry] = {x}     (x needed by bb1)
        live_in[bb1]    = {x}
        live_out[bb1]   = {y}
        live_in[end]    = {y}
        live_out[end]   = {}
    """
    cfg = CFG()
    cfg.add_block("entry", insns=["DEF x"])
    cfg.add_block("bb1",   insns=["MOV y, x"])
    cfg.add_block("end",   insns=["USE y"])
    cfg.add_edge("entry", "bb1")
    cfg.add_edge("bb1",   "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


def make_branch_liveness_cfg():
    """
    entry: [DEF x]
    bb1:   [USE x]
    bb2:   [USE x]
    end:   []

    Both bb1 and bb2 use x, so x is live_out of entry.
    """
    cfg = CFG()
    cfg.add_block("entry", insns=["DEF x"])
    cfg.add_block("bb1",   insns=["USE x"])
    cfg.add_block("bb2",   insns=["USE x"])
    cfg.add_block("end")
    cfg.add_edge("entry", "bb1")
    cfg.add_edge("entry", "bb2")
    cfg.add_edge("bb1",   "end")
    cfg.add_edge("bb2",   "end")
    cfg.set_entry("entry")
    cfg.set_exit("end")
    return cfg


# ---------------------------------------------------------------------------
# LivenessAnalysis
# ---------------------------------------------------------------------------

class TestLivenessAnalysis:
    def test_linear_live_sets(self):
        cfg = make_liveness_cfg()
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()

        assert la.live_in["entry"]  == frozenset()
        assert la.live_out["entry"] == frozenset({"x"})
        assert la.live_in["bb1"]    == frozenset({"x"})
        assert la.live_out["bb1"]   == frozenset({"y"})
        assert la.live_in["end"]    == frozenset({"y"})
        assert la.live_out["end"]   == frozenset()

    def test_branch_live_out(self):
        cfg = make_branch_liveness_cfg()
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        # x is live-out of entry because both successors use it
        assert "x" in la.live_out["entry"]

    def test_is_live_at_entry(self):
        cfg = make_liveness_cfg()
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        assert la.is_live_at_entry("bb1", "x") is True
        assert la.is_live_at_entry("end", "x") is False

    def test_is_live_at_exit(self):
        cfg = make_liveness_cfg()
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        assert la.is_live_at_exit("entry", "x") is True
        assert la.is_live_at_exit("end",   "y") is False

    def test_empty_insns(self):
        """Blocks with no instructions should have live_in == live_out."""
        cfg = CFG()
        cfg.add_block("a")
        cfg.add_block("b")
        cfg.add_edge("a", "b")
        cfg.set_entry("a")
        cfg.set_exit("b")
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        assert la.live_in["a"] == frozenset()
        assert la.live_out["a"] == frozenset()

    def test_variable_used_before_def_in_block(self):
        """
        If a block does USE x then DEF x, x is live-in for that block.
        """
        cfg = CFG()
        cfg.add_block("a", insns=["USE x", "DEF x"])
        cfg.add_block("end")
        cfg.add_edge("a", "end")
        cfg.set_entry("a")
        cfg.set_exit("end")
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        # x used before defined → x is live-in
        assert "x" in la.live_in["a"]

    def test_variable_defined_before_use_in_block(self):
        """
        If a block does DEF x then USE x, x is NOT live-in.
        """
        cfg = CFG()
        cfg.add_block("a", insns=["DEF x", "USE x"])
        cfg.add_block("end")
        cfg.add_edge("a", "end")
        cfg.set_entry("a")
        cfg.set_exit("end")
        la = LivenessAnalysis(cfg, simple_def_use)
        la.run()
        assert "x" not in la.live_in["a"]


# ---------------------------------------------------------------------------
# eliminate_dead_blocks
# ---------------------------------------------------------------------------

class TestEliminateDeadBlocks:
    def test_no_dead_blocks(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("end")
        cfg.add_edge("entry", "end")
        cfg.set_entry("entry")
        cfg.set_exit("end")
        removed = eliminate_dead_blocks(cfg)
        assert removed == []
        assert len(cfg) == 2

    def test_removes_orphan(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("end")
        cfg.add_block("orphan")
        cfg.add_edge("entry", "end")
        cfg.set_entry("entry")
        cfg.set_exit("end")
        removed = eliminate_dead_blocks(cfg)
        assert len(removed) == 1
        assert removed[0].id == "orphan"
        assert len(cfg) == 2
        assert "orphan" not in cfg

    def test_multiple_orphans(self):
        cfg = CFG()
        cfg.add_block("entry")
        cfg.add_block("end")
        cfg.add_block("dead1")
        cfg.add_block("dead2")
        cfg.add_edge("entry", "end")
        cfg.set_entry("entry")
        cfg.set_exit("end")
        removed = eliminate_dead_blocks(cfg)
        assert {bb.id for bb in removed} == {"dead1", "dead2"}
        assert len(cfg) == 2
