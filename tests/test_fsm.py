"""Tests for mypkg.fsm — FSMGraph (Interprocedural CFG)."""

import pytest

from mypkg.cfg import CFG
from mypkg.fsm import FSMGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_simple_fsm() -> FSMGraph:
    """
    IDLE → FETCH (cond=start)
    FETCH → IDLE (cond=loop)
    FETCH → DONE (cond=halt)
    reset=IDLE, terminal=DONE
    """
    fsm = FSMGraph()
    fsm.add_state("IDLE",  actions=["clr_cnt"])
    fsm.add_state("FETCH", actions=["load_insn"])
    fsm.add_state("DONE")
    fsm.add_transition("IDLE",  "FETCH", cond="start")
    fsm.add_transition("FETCH", "IDLE",  cond="loop")
    fsm.add_transition("FETCH", "DONE",  cond="halt")
    fsm.set_reset("IDLE")
    fsm.set_terminal("DONE")
    return fsm


def make_fsm_with_dead_state() -> FSMGraph:
    """Same as above but adds an orphan state with no incoming edge."""
    fsm = make_simple_fsm()
    fsm.add_state("GHOST")
    fsm.add_transition("GHOST", "DONE", cond="x")
    return fsm


def make_fsm_with_dead_loop() -> FSMGraph:
    """
    IDLE → FETCH → DONE  (normal)
    IDLE → STUCK → STUCK (dead self-loop)
    """
    fsm = FSMGraph()
    fsm.add_state("IDLE")
    fsm.add_state("FETCH")
    fsm.add_state("DONE")
    fsm.add_state("STUCK")
    fsm.add_transition("IDLE",  "FETCH", cond="ok")
    fsm.add_transition("FETCH", "DONE",  cond="halt")
    fsm.add_transition("IDLE",  "STUCK", cond="bad")
    fsm.add_transition("STUCK", "STUCK", cond="stay")
    fsm.set_reset("IDLE")
    fsm.set_terminal("DONE")
    return fsm


def make_function_cfg(name: str = "fn") -> CFG:
    """Simple function: entry_block → ret_block."""
    cfg = CFG()
    cfg.add_block(f"{name}_body", insns=["ADD R0, #1"])
    cfg.add_block(f"{name}_ret",  insns=["ret"])
    cfg.add_edge(f"{name}_body", f"{name}_ret")
    cfg.set_entry(f"{name}_body")
    cfg.set_exit(f"{name}_ret")
    return cfg


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_add_state_and_transition(self):
        fsm = FSMGraph()
        fsm.add_state("A", actions=["do_a"])
        fsm.add_state("B")
        fsm.add_transition("A", "B", cond="x")
        fsm.set_reset("A")
        cfg = fsm.get_cfg()
        assert "A" in cfg
        assert "B" in cfg

    def test_set_reset_and_terminal(self):
        fsm = make_simple_fsm()
        assert fsm.main_cfg.entry.id == "IDLE"
        assert fsm.main_cfg.exit.id == "DONE"

    def test_add_function(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        assert fsm.get_cfg("f1") is fn_cfg

    def test_add_duplicate_function_raises(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        with pytest.raises(ValueError, match="already registered"):
            fsm.add_function("f1", make_function_cfg("f1"))

    def test_add_main_as_function_raises(self):
        fsm = FSMGraph()
        with pytest.raises(ValueError, match="reserved"):
            fsm.add_function("main", CFG())

    def test_add_call_site(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        fsm.add_call_site(fn="main", block_id="FETCH", callee="f1")
        # no error = ok

    def test_add_call_site_unknown_fn_raises(self):
        fsm = make_simple_fsm()
        with pytest.raises(KeyError):
            fsm.add_call_site(fn="main", block_id="FETCH", callee="ghost_fn")

    def test_add_return_marks_block(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        fsm.add_return("f1", "f1_ret")
        # no error = ok

    def test_add_return_unknown_block_raises(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        with pytest.raises(KeyError):
            fsm.add_return("f1", "no_such_block")


# ---------------------------------------------------------------------------
# Dead state analysis
# ---------------------------------------------------------------------------

class TestDeadStates:
    def test_no_dead_states_in_clean_fsm(self):
        fsm = make_simple_fsm()
        assert fsm.find_dead_states() == []

    def test_finds_orphan_state(self):
        fsm = make_fsm_with_dead_state()
        dead = fsm.find_dead_states()
        assert len(dead) == 1
        assert dead[0].id == "GHOST"


# ---------------------------------------------------------------------------
# Dead loop analysis
# ---------------------------------------------------------------------------

class TestDeadLoops:
    def test_no_dead_loop_in_clean_fsm(self):
        fsm = make_simple_fsm()
        assert fsm.find_dead_loops() == []

    def test_detects_dead_self_loop(self):
        fsm = make_fsm_with_dead_loop()
        dead = fsm.find_dead_loops()
        assert len(dead) == 1
        assert dead[0] == ["STUCK"]

    def test_dead_loop_no_terminal(self):
        """Without terminal set, SCC with no exits = dead loop."""
        fsm = FSMGraph()
        fsm.add_state("A")
        fsm.add_state("B")
        fsm.add_state("C")
        fsm.add_transition("A", "B")
        fsm.add_transition("B", "C")
        fsm.add_transition("C", "B")   # B↔C dead loop, no outgoing edge
        fsm.set_reset("A")
        # no terminal set
        dead = fsm.find_dead_loops()
        assert len(dead) == 1
        assert set(dead[0]) == {"B", "C"}


# ---------------------------------------------------------------------------
# Call depth
# ---------------------------------------------------------------------------

class TestCallDepth:
    def test_no_calls_depth_zero(self):
        fsm = make_simple_fsm()
        assert fsm.check_call_depth() == 0

    def test_single_call_depth_one(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        fsm.add_call_site(fn="main", block_id="FETCH", callee="f1")
        assert fsm.check_call_depth() == 1

    def test_two_levels_depth_two(self):
        fsm = make_simple_fsm()
        f1_cfg = make_function_cfg("f1")
        f2_cfg = make_function_cfg("f2")
        fsm.add_function("f1", f1_cfg)
        fsm.add_function("f2", f2_cfg)
        fsm.add_call_site(fn="main", block_id="FETCH", callee="f1")
        fsm.add_call_site(fn="f1",   block_id="f1_body", callee="f2")
        assert fsm.check_call_depth() == 2

    def test_max_depth_exceeded_raises(self):
        fsm = make_simple_fsm()
        f1_cfg = make_function_cfg("f1")
        f2_cfg = make_function_cfg("f2")
        f3_cfg = make_function_cfg("f3")
        fsm.add_function("f1", f1_cfg)
        fsm.add_function("f2", f2_cfg)
        fsm.add_function("f3", f3_cfg)
        fsm.add_call_site("main", "FETCH",   "f1")
        fsm.add_call_site("f1",   "f1_body", "f2")
        fsm.add_call_site("f2",   "f2_body", "f3")
        with pytest.raises(ValueError, match="depth 3 exceeds"):
            fsm.check_call_depth(max_depth=2)

    def test_default_max_depth_from_constructor(self):
        fsm = FSMGraph(max_call_depth=1)
        fsm.add_state("A")
        fsm.add_state("B")
        fsm.add_transition("A", "B")
        fsm.set_reset("A")
        f1_cfg = make_function_cfg("f1")
        f2_cfg = make_function_cfg("f2")
        fsm.add_function("f1", f1_cfg)
        fsm.add_function("f2", f2_cfg)
        fsm.add_call_site("main", "A", "f1")
        fsm.add_call_site("f1", "f1_body", "f2")
        with pytest.raises(ValueError, match="exceeds"):
            fsm.check_call_depth()   # uses instance default


# ---------------------------------------------------------------------------
# Single return check
# ---------------------------------------------------------------------------

class TestSingleReturn:
    def test_single_return_ok(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        fsm.add_return("f1", "f1_ret")
        fsm.check_single_return("f1")   # should not raise

    def test_no_return_raises(self):
        fsm = make_simple_fsm()
        fn_cfg = make_function_cfg("f1")
        fsm.add_function("f1", fn_cfg)
        with pytest.raises(ValueError, match="no return block"):
            fsm.check_single_return("f1")

    def test_multiple_returns_raises(self):
        fsm = make_simple_fsm()
        fn_cfg = CFG()
        fn_cfg.add_block("body")
        fn_cfg.add_block("ret1", insns=["ret"])
        fn_cfg.add_block("ret2", insns=["ret"])
        fn_cfg.add_edge("body", "ret1")
        fn_cfg.add_edge("body", "ret2")
        fn_cfg.set_entry("body")
        fsm.add_function("multi_ret", fn_cfg)
        fsm.add_return("multi_ret", "ret1")
        fsm.add_return("multi_ret", "ret2")
        with pytest.raises(ValueError, match="2 return blocks"):
            fsm.check_single_return("multi_ret")


# ---------------------------------------------------------------------------
# Linearization
# ---------------------------------------------------------------------------

class TestLinearize:
    def test_linearize_contains_all_states(self):
        fsm = make_simple_fsm()
        order = fsm.linearize()
        assert set(order) == {"IDLE", "FETCH", "DONE"}

    def test_linearize_default_cfg(self):
        fsm = make_simple_fsm()
        assert fsm.get_cfg() is fsm.main_cfg
