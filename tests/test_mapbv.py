"""Comprehensive tests for MapBV.

API reference (new style):
  - Factory:  const(value, width)         → MapBV with typ=="CONST"
              var(name, width, tags=None) → MapBV with typ=="VAR"
  - Raw:      MapBV(parent, high, low)    — used internally by __getitem__

Key behavioural changes from old API:
  - __eq__ is identity-based; use .value_eq() to compare values
  - Single-bit indexing bv[3] is now valid (returns a 1-bit SLICE)
  - No MapBVSlice class; slices are MapBV with typ=="SLICE"
"""

import warnings

import pytest
from mypkg import MapBV, const, var


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — Core Features
# ═══════════════════════════════════════════════════════════════════════════


class TestDeclaration:
    def test_named_variable(self):
        reg = var("REG0", 16, tags={"type": "RW", "addr": 0x100})
        assert reg.name == "REG0"
        assert reg.width == 16
        assert reg.typ == "VAR"
        assert reg.tags == {"type": "RW", "addr": 0x100}
        assert reg.value == 0

    def test_named_variable_default_tags(self):
        reg = var("REG1", 8)
        assert reg.tags is None

    def test_constant(self):
        c = const(0, 2)
        assert c.name == "Constant"
        assert c.width == 2
        assert c.typ == "CONST"
        assert c.tags is None
        assert c.value == 0

    def test_constant_value_masked(self):
        # const() masks value to width automatically
        c = const(0xFF, 4)
        assert c.value == 0xF

    def test_invalid_width(self):
        with pytest.raises((ValueError, Exception)):
            MapBV("X", -1, 0)  # negative width (high < low)
        with pytest.raises((ValueError, Exception)):
            MapBV("X", 3, 1)   # low != 0 for root MapBV

    def test_invalid_name(self):
        with pytest.raises(ValueError):
            var("123bad", 8)


class TestLinkRead:
    def test_concat_read(self):
        reg0 = var("REG0", 16)
        reg1 = var("REG1", 16)
        padding = const(0, 2)
        sram00 = var("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        reg0.value = 0x5
        reg1.value = 0x2
        assert sram00.value == 0x52

    def test_link_width_mismatch(self):
        sram = var("SRAM", 8)
        a = var("A", 4)
        b = var("B", 3)
        with pytest.raises(ValueError, match="width mismatch"):
            sram.link(a, b)


class TestLinkWrite:
    def test_write_propagates(self):
        reg0 = var("REG0", 16)
        reg1 = var("REG1", 16)
        padding = const(0, 2)
        sram00 = var("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        sram00.value = 0xFF
        assert reg0.value == 0xF
        assert reg1.value == 0x3


class TestSlicing:
    def test_slice_read(self):
        reg = var("R", 16)
        reg.value = 0xABCD
        assert reg[7:0].value == 0xCD
        assert reg[15:8].value == 0xAB
        assert reg[3:0].value == 0xD

    def test_slice_write(self):
        reg = var("R", 16)
        reg.value = 0x0000
        reg[7:4].value = 0xF
        assert reg.value == 0x00F0

    def test_slice_width(self):
        reg = var("R", 16)
        s = reg[7:0]
        assert s.width == 8
        assert s.typ == "SLICE"

    def test_single_bit_indexing(self):
        reg = var("R", 8)
        s = reg[3]
        assert s.width == 1
        assert s.typ == "SLICE"

    def test_slice_invalid_range(self):
        reg = var("R", 8)
        with pytest.raises(IndexError, match="out of bounds"):
            reg[2:5]   # high=2 < low=5 invalid for 8-bit reg

    def test_slice_out_of_range(self):
        reg = var("R", 8)
        with pytest.raises(IndexError, match="out of bounds"):
            reg[8:0]   # bit 8 out of range for width-8


class TestLogicOps:
    def test_and(self):
        a = var("A", 8); b = var("B", 8)
        a.value = 0xFF; b.value = 0x0F
        assert (a & b).value_eq(0x0F)

    def test_or(self):
        a = var("A", 8); b = var("B", 8)
        a.value = 0xF0; b.value = 0x0F
        assert (a | b).value_eq(0xFF)

    def test_xor(self):
        a = var("A", 8); b = var("B", 8)
        a.value = 0xFF; b.value = 0x0F
        assert (a ^ b).value_eq(0xF0)

    def test_invert(self):
        a = var("A", 8); a.value = 0x0F
        assert (~a).value_eq(0xF0)

    def test_and_with_int(self):
        a = var("A", 16); a.value = 0xABCD
        assert (a & 0x00FF).value_eq(0x00CD)

    def test_complex_expr(self):
        reg0 = var("REG0", 16); reg1 = var("REG1", 16)
        reg0.value = 0xABCD; reg1.value = 0x00FF
        full_logic = (reg0 & 0x0F) | (reg1 ^ const(0xFF, 16))
        assert full_logic.value_eq(0x000D)


class TestEval:
    def test_eval_linked(self):
        reg0 = var("REG0", 16); reg1 = var("REG1", 16)
        padding = const(0, 2)
        sram00 = var("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        reg0.value = 0x5; reg1.value = 0x2
        simulated = sram00.eval({"REG0": 0xA, "REG1": 0x3})
        assert simulated == 0xA3
        assert reg0.value == 0x5
        assert reg1.value == 0x2
        assert sram00.value == 0x52

    def test_eval_const(self):
        assert const(0x3, 4).eval({}) == 0x3

    def test_eval_named_fallback(self):
        reg = var("R", 8); reg.value = 0x42
        assert reg.eval({}) == 0x42

    def test_eval_expr(self):
        a = var("A", 8); b = var("B", 8)
        assert (a & b).eval({"A": 0xFF, "B": 0x0F}) == 0x0F


class TestStructure:
    def test_structure_linked(self):
        reg0 = var("REG0", 16, tags={"type": "RW"})
        reg1 = var("REG1", 16, tags={"type": "RO"})
        padding = const(0, 2)
        sram00 = var("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        layout = sram00.structure
        assert len(layout) == 3
        assert layout[0].bv.name == "REG0"
        assert layout[0].slice_range == (3, 0)
        assert layout[1].bv.name == "Constant"
        assert layout[1].slice_range is None
        assert layout[2].bv.name == "REG1"
        assert layout[2].slice_range == (1, 0)

    def test_structure_unlinked(self):
        assert var("R", 8).structure == []

    def test_tags_query(self):
        reg0 = var("REG0", 16, tags={"type": "RW", "addr": 256})
        assert reg0.tags is not None and reg0.tags["type"] == "RW"
        assert reg0.tags is not None and reg0.tags["addr"] == 256


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — Enhancements
# ═══════════════════════════════════════════════════════════════════════════


# ── Fix #1: Const write warning ─────────────────────────────────────────

class TestConstWriteWarning:
    def test_const_write_warns(self):
        c = const(0x0, 4)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            c.value = 0xF
            assert len(w) == 1
            assert "constant" in str(w[0].message).lower()
        # Value should NOT have changed
        assert c.value == 0x0

    def test_link_write_to_const_child_warns(self):
        """Writing sram that contains a const child should warn."""
        reg = var("R", 4)
        pad = const(0, 4)
        sram = var("SRAM", 8)
        sram.link(reg, pad)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sram.value = 0xFF
            # Expecting warning from padding's setter
            const_warns = [x for x in w if "constant" in str(x.message).lower()]
            assert len(const_warns) >= 1
        assert reg.value == 0xF
        assert pad.value == 0x0  # unchanged


# ── Slice type (was MapBVSlice) supports logic operators ────────────────

class TestSliceLogicOps:
    def test_slice_and(self):
        a = var("A", 16); a.value = 0xABCD
        assert (a[7:0] & 0x0F).value_eq(0x0D)

    def test_slice_or(self):
        a = var("A", 8); a.value = 0xF0
        assert (a[7:4] | 0x0).value_eq(0xF)

    def test_slice_xor_slice(self):
        a = var("A", 16); b = var("B", 16)
        a.value = 0xFF00; b.value = 0x00FF
        assert (a[15:8] ^ b[7:0]).value_eq(0x00)  # 0xFF ^ 0xFF

    def test_slice_invert(self):
        a = var("A", 8); a.value = 0x0F
        assert (~a[3:0]).value_eq(0x0)  # ~0xF masked to 4 bits = 0x0

    def test_slice_shift(self):
        a = var("A", 8); a.value = 0x0F
        assert (a[7:0] << 4).value_eq(0xF0)
        assert (a[7:0] >> 2).value_eq(0x03)


# ── link() on SLICE raises TypeError ────────────────────────────────────

class TestSliceLinkNotSupported:
    def test_slice_link_raises(self):
        """link() on a SLICE MapBV should raise TypeError."""
        reg = var("REG", 16)
        fa = var("FA", 8)
        with pytest.raises(TypeError, match="SLICE"):
            reg[7:0].link(fa)


# ── identity-based __eq__ / value_eq ──────────────────────────────────────

class TestEqInt:
    def test_bv_value_eq_int(self):
        a = var("A", 8); a.value = 0x42
        assert a.value_eq(0x42)
        assert not a.value_eq(0x43)

    def test_bv_identity_eq(self):
        """Two different BVs with same value are NOT equal (identity)."""
        a = var("A", 8); b = var("B", 8)
        a.value = 0x10; b.value = 0x10
        assert a != b          # different objects
        assert a == a          # same object

    def test_int_conversion(self):
        a = var("A", 8); a.value = 0x42
        assert int(a) == 0x42

    def test_slice_value_eq_int(self):
        a = var("A", 16); a.value = 0xABCD
        assert a[7:0].value_eq(0xCD)

    def test_slice_int(self):
        a = var("A", 16); a.value = 0xABCD
        assert int(a[7:0]) == 0xCD

    def test_expr_value_eq_int(self):
        a = var("A", 8); a.value = 0xFF
        assert (a & 0x0F).value_eq(0x0F)

    def test_expr_int(self):
        a = var("A", 8); a.value = 0xFF
        assert int(a & 0x0F) == 0x0F

    def test_hash_identity(self):
        """Different BVs with same value should have different identity."""
        a = var("A", 8); b = var("B", 8)
        a.value = 0x42; b.value = 0x42
        d = {a: "first", b: "second"}
        assert d[a] == "first"
        assert d[b] == "second"


# ── Formatting ──────────────────────────────────────────────────────────

class TestFormatting:
    def test_bv_to_hex(self):
        a = var("A", 16); a.value = 0x00FF
        assert a.to_hex() == "0x00FF"

    def test_bv_to_bin(self):
        a = var("A", 8); a.value = 0x0F
        assert a.to_bin() == "0b00001111"

    def test_bv_format_hex(self):
        a = var("A", 8); a.value = 0xAB
        assert f"{a:x}" == "0xAB"
        assert f"{a:hex}" == "0xAB"

    def test_bv_format_bin(self):
        a = var("A", 8); a.value = 0x0F
        assert f"{a:b}" == "0b00001111"
        assert f"{a:bin}" == "0b00001111"

    def test_slice_to_hex(self):
        a = var("A", 16); a.value = 0xABCD
        assert a[7:0].to_hex() == "0xCD"

    def test_expr_to_hex(self):
        a = var("A", 8); a.value = 0xFF
        assert (a & 0x0F).to_hex() == "0x0F"

    def test_format_default(self):
        a = var("A", 8); a.value = 42
        assert f"{a:d}" == "42"


# ── Re-link warning ────────────────────────────────────────────────────

class TestRelinkWarning:
    def test_relink_warns(self):
        sram = var("SRAM", 8)
        a = var("A", 4); b = var("B", 4)
        c = var("C", 8)
        sram.link(a, b)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sram.link(c)
            assert len(w) == 1
            assert "already linked" in str(w[0].message).lower()


# ── Shift operators ──────────────────────────────────────────────────────

class TestShiftOps:
    def test_lshift(self):
        a = var("A", 8); a.value = 0x0F
        assert (a << 4).value_eq(0xF0)

    def test_rshift(self):
        a = var("A", 8); a.value = 0xF0
        assert (a >> 4).value_eq(0x0F)

    def test_shift_masked(self):
        a = var("A", 8); a.value = 0xFF
        assert (a << 4).value_eq(0xF0)  # upper bits masked out

    def test_shift_eval(self):
        a = var("A", 8)
        assert (a << 2).eval({"A": 0x03}) == 0x0C

    def test_shift_chain(self):
        a = var("A", 8); a.value = 0xFF
        assert ((a << 4) >> 4).value_eq(0x0F)


# ── __len__ ──────────────────────────────────────────────────────────────

class TestLen:
    def test_bv_len(self):
        assert len(var("A", 16)) == 16

    def test_slice_len(self):
        assert len(var("A", 16)[7:0]) == 8

    def test_expr_len(self):
        a = var("A", 8); b = var("B", 8)
        assert len(a & b) == 8


# ── to_hex / to_bin ─────────────────────────────────────────────────────

class TestFormatMethods:
    def test_const_to_hex(self):
        c = const(0xFF, 8)
        assert c.to_hex() == "0xFF"
        assert c.to_bin() == "0b11111111"

    def test_1bit(self):
        b = var("B", 1); b.value = 1
        assert b.to_hex() == "0x1"
        assert b.to_bin() == "0b1"


# ── MapBV.concat() ─────────────────────────────────────────────────────────

class TestConcat:
    def test_concat_basic(self):
        a = var("A", 4); b = var("B", 4)
        a.value = 0xA; b.value = 0x5
        c = MapBV.concat(a, b)
        assert c.width == 8
        assert c.value == 0xA5

    def test_concat_with_slices(self):
        r = var("R", 16); r.value = 0xABCD
        c = MapBV.concat(r[15:8], r[7:0])
        assert c.value == 0xABCD

    def test_concat_custom_name(self):
        a = var("A", 4)
        c = MapBV.concat(a, name="MY_CONCAT")
        assert c.name == "MY_CONCAT"

    def test_concat_write_back(self):
        a = var("A", 4); b = var("B", 4)
        c = MapBV.concat(a, b)
        c.value = 0x37
        assert a.value == 0x3
        assert b.value == 0x7


# ── unlink() ────────────────────────────────────────────────────────────

class TestUnlink:
    def test_unlink_snapshots_value(self):
        a = var("A", 4); b = var("B", 4)
        a.value = 0xA; b.value = 0x5
        sram = var("SRAM", 8)
        sram.link(a, b)
        assert sram.value == 0xA5
        sram.unlink()
        assert sram.value == 0xA5  # value preserved
        assert sram.structure == []  # no longer linked
        # Changing a should NOT affect sram anymore
        a.value = 0x0
        assert sram.value == 0xA5

    def test_unlink_noop_if_not_linked(self):
        a = var("A", 8); a.value = 0x42
        a.unlink()  # should not error
        assert a.value == 0x42


# ── copy() ─────────────────────────────────────────────────────────────

class TestCopy:
    def test_copy_basic(self):
        a = var("A", 8, tags={"type": "RW"})
        a.value = 0x42
        b = a.copy()
        assert b.name == "A_copy"
        assert b.value == 0x42
        assert b.tags == {"type": "RW"}
        # Independent — changing a should not affect b
        a.value = 0x00
        assert b.value == 0x42

    def test_copy_custom_name(self):
        a = var("A", 8)
        b = a.copy("B")
        assert b.name == "B"

    def test_copy_linked_bv(self):
        """Copying a linked MapBV should snapshot the composite value."""
        x = var("X", 4); y = var("Y", 4)
        x.value = 0xA; y.value = 0x5
        sram = var("SRAM", 8)
        sram.link(x, y)
        c = sram.copy()
        assert c.value == 0xA5
        assert c.structure == []  # copy is not linked

    def test_copy_deep_tags(self):
        """Tags should be deep-copied."""
        a = var("A", 8, tags={"nested": [1, 2, 3]})
        b = a.copy()
        assert b.tags is not None
        b.tags["nested"].append(4)
        assert a.tags is not None and a.tags["nested"] == [1, 2, 3]  # original unaffected


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_write_then_read_consistency(self):
        """Write sram, immediately read back, should be identical."""
        a = var("A", 4); b = var("B", 4)
        sram = var("SRAM", 8)
        sram.link(a, b)
        for val in [0x00, 0xFF, 0xA5, 0x5A, 0x12]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sram.value = val
                assert sram.value == val

    def test_multi_layer_link(self):
        """A links B, B links C — three-layer sync."""
        c1 = var("C1", 4); c2 = var("C2", 4)
        b = var("B", 8)
        b.link(c1, c2)  # B = {C1, C2}
        a = var("A", 8)
        a.link(b)        # A = {B}
        c1.value = 0xA
        c2.value = 0x5
        assert b.value == 0xA5
        assert a.value == 0xA5
        # Write top-level
        a.value = 0x37
        assert c1.value == 0x3
        assert c2.value == 0x7

    def test_cross_ref_slices(self):
        """Same MapBV slices linked to different SRAMs."""
        reg = var("REG", 16)
        reg.value = 0xABCD
        sram0 = var("S0", 8)
        sram1 = var("S1", 8)
        sram0.link(reg[7:0])    # S0 = REG[7:0]
        sram1.link(reg[15:8])   # S1 = REG[15:8]
        assert sram0.value == 0xCD
        assert sram1.value == 0xAB
        # Modify reg, both SRAMs reflect
        reg.value = 0x1234
        assert sram0.value == 0x34
        assert sram1.value == 0x12

    def test_1bit_bv(self):
        b = var("B", 1)
        b.value = 1
        assert b.value == 1
        assert (~b).value_eq(0)
        assert len(b) == 1

    def test_32bit_bv(self):
        b = var("W", 32)
        b.value = 0xDEADBEEF
        assert b.value == 0xDEADBEEF
        assert b.to_hex() == "0xDEADBEEF"

    def test_reverse_ops_int_left(self):
        """int on the left: 0xFF & reg."""
        a = var("A", 8); a.value = 0xAB
        assert (0x0F & a).value_eq(0x0B)
        assert (0x0F | a).value_eq(0xAF)
        assert (0x0F ^ a).value_eq(0xA4)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — Tag-Aware Eval
# ═══════════════════════════════════════════════════════════════════════════


class TestTagAwareEval:
    """Tests for MapBV.key() and tag-aware eval()."""

    def test_red_green_scenario(self):
        """The user's primary use case: same-name regs under different tags."""
        reg0_red = var("REG0", 16, tags={"color": "red"})
        reg1_red = var("REG1", 16, tags={"color": "red"})
        reg0_green = var("REG0", 16, tags={"color": "green"})
        reg1_green = var("REG1", 16, tags={"color": "green"})

        sram_red = MapBV.concat(reg0_red[3:0], reg1_red[3:0], name="SRAM_RED")
        sram_green = MapBV.concat(reg0_green[3:0], reg1_green[3:0], name="SRAM_GREEN")

        ctx_red = {
            MapBV.key("REG0", {"color": "red"}): 0x1,
            MapBV.key("REG1", {"color": "red"}): 0x2,
        }
        assert sram_red.eval(ctx_red) == 0x12

        ctx_green = {
            MapBV.key("REG0", {"color": "green"}): 0x5,
            MapBV.key("REG1", {"color": "green"}): 0x2,
        }
        assert sram_green.eval(ctx_green) == 0x52

    def test_tagged_key_priority_over_name(self):
        """Tagged key should take priority over name-only key."""
        reg = var("REG0", 8, tags={"color": "red"})
        ctx = {
            "REG0": 0xAA,
            MapBV.key("REG0", {"color": "red"}): 0xBB,
        }
        assert reg.eval(ctx) == 0xBB

    def test_name_only_applies_to_all(self):
        """Name-only key applies to any MapBV with that name, regardless of tags."""
        reg_red = var("REG0", 8, tags={"color": "red"})
        reg_green = var("REG0", 8, tags={"color": "green"})
        reg_no_tags = var("REG0", 8)

        ctx = {"REG0": 0x42}
        assert reg_red.eval(ctx) == 0x42
        assert reg_green.eval(ctx) == 0x42
        assert reg_no_tags.eval(ctx) == 0x42

    def test_tags_must_fully_match(self):
        """Partial tag match should NOT work — must be exact dict match."""
        reg = var("REG0", 8, tags={"color": "red", "type": "RW"})
        ctx = {
            MapBV.key("REG0", {"color": "red"}): 0xAA,
        }
        reg.value = 0x55
        assert reg.eval(ctx) == 0x55  # fallback to current value

    def test_mixed_context(self):
        """Use both tagged and name-only keys in the same context."""
        reg0_red = var("REG0", 8, tags={"color": "red"})
        reg1 = var("REG1", 8)

        sram = MapBV.concat(reg0_red, reg1, name="SRAM")

        ctx = {
            MapBV.key("REG0", {"color": "red"}): 0xAB,
            "REG1": 0xCD,
        }
        assert sram.eval(ctx) == 0xABCD

    def test_eval_key_property(self):
        reg = var("REG0", 8, tags={"color": "red"})
        assert reg.eval_key == ("REG0", frozenset({"color": "red"}.items()))

    def test_eval_key_none_for_empty_tags(self):
        reg = var("REG0", 8)
        assert reg.eval_key is None

    def test_eval_key_none_for_const(self):
        c = const(0, 4)
        assert c.eval_key is None

    def test_bv_key_static(self):
        k = MapBV.key("REG0", {"a": 1, "b": 2})
        assert isinstance(k, tuple)
        assert k[0] == "REG0"
        assert k[1] == frozenset({"a": 1, "b": 2}.items())

    def test_linked_sram_eval_with_tags(self):
        """Full end-to-end: linked SRAM with tagged regs, eval with tagged ctx."""
        reg0 = var("REG0", 16, tags={"color": "red"})
        reg1 = var("REG1", 16, tags={"color": "red"})
        padding = const(0, 2)
        sram = var("SRAM", 8)
        sram.link(reg0[3:0], padding, reg1[1:0])

        ctx = {
            MapBV.key("REG0", {"color": "red"}): 0xA,
            MapBV.key("REG1", {"color": "red"}): 0x3,
        }
        # {1010, 00, 11} = 10100011 = 0xA3
        assert sram.eval(ctx) == 0xA3


# ═══════════════════════════════════════════════════════════════════════════
# Factory Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestFactory:
    def test_const_basic(self):
        c = const(0x23, 12)
        assert c.value == 0x23
        assert c.width == 12
        assert c.typ == "CONST"
        assert c.name == "Constant"

    def test_const_masks_value(self):
        c = const(0xFF, 4)
        assert c.value == 0xF  # 0xFF masked to 4 bits

    def test_const_zero(self):
        c = const(0, 2)
        assert c.value == 0
        assert c.width == 2

    def test_var_basic(self):
        r = var("reg", 12)
        assert r.name == "reg"
        assert r.width == 12
        assert r.typ == "VAR"
        assert r.tags is None

    def test_var_with_tags(self):
        r = var("REG0", 16, tags={"type": "RW"})
        assert r.tags == {"type": "RW"}

    def test_const_is_immutable(self):
        c = const(0x5, 8)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            c.value = 0xFF
            assert any("constant" in str(x.message).lower() for x in w)
        assert c.value == 0x5

    def test_factory_module_level_import(self):
        """const/var should be importable directly from mypkg."""
        from mypkg import const as c_fn, var as v_fn
        reg = v_fn("X", 8)
        pad = c_fn(0, 4)
        assert reg.typ == "VAR"
        assert pad.typ == "CONST"
