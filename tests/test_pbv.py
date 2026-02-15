"""Comprehensive tests for the PBV (PyBitVector) library.

Phase 1 — Core features (from info.txt):
  1. Declaration (named & constant)
  2. Link & read (concat children)
  3. Link & write (split bits to children)
  4. Slice read / write
  5. Logic operators
  6. Symbolic eval
  7. Structure introspection

Phase 2 — Enhancements:
  Fixes:   #1 const warning, #2 slice ops, #3 slice link,
           #4 eq/int, #5 format, #6 re-link warning
  Features: A shift, B len, C to_hex/to_bin, D concat,
            E unlink, F copy/snapshot
  Edge cases: write-then-read, multi-layer link, cross-ref,
              extreme widths, reverse ops
"""

import warnings

import pytest
from mypkg import BV, BVSlice, BVExpr, StructSegment


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — Core Features
# ═══════════════════════════════════════════════════════════════════════════


class TestDeclaration:
    def test_named_variable(self):
        reg = BV("REG0", 16, tags={"type": "RW", "addr": 0x100})
        assert reg.name == "REG0"
        assert reg.width == 16
        assert reg.is_const is False
        assert reg.tags == {"type": "RW", "addr": 0x100}
        assert reg.value == 0

    def test_named_variable_default_tags(self):
        reg = BV("REG1", 8)
        assert reg.tags == {}

    def test_constant(self):
        c = BV(0, 2)
        assert c.name == "CONST"
        assert c.width == 2
        assert c.is_const is True
        assert c.tags is None
        assert c.value == 0

    def test_constant_value_masked(self):
        c = BV(0xFF, 4)
        assert c.value == 0xF

    def test_invalid_width(self):
        with pytest.raises(ValueError):
            BV("X", 0)


class TestLinkRead:
    def test_concat_read(self):
        reg0 = BV("REG0", 16)
        reg1 = BV("REG1", 16)
        padding = BV(0, 2)
        sram00 = BV("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        reg0.value = 0x5
        reg1.value = 0x2
        assert sram00.value == 0x52

    def test_link_width_mismatch(self):
        sram = BV("SRAM", 8)
        a = BV("A", 4)
        b = BV("B", 3)
        with pytest.raises(ValueError, match="width mismatch"):
            sram.link(a, b)


class TestLinkWrite:
    def test_write_propagates(self):
        reg0 = BV("REG0", 16)
        reg1 = BV("REG1", 16)
        padding = BV(0, 2)
        sram00 = BV("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        sram00.value = 0xFF
        assert reg0.value == 0xF
        assert reg1.value == 0x3


class TestSlicing:
    def test_slice_read(self):
        reg = BV("R", 16)
        reg.value = 0xABCD
        assert reg[7:0].value == 0xCD
        assert reg[15:8].value == 0xAB
        assert reg[3:0].value == 0xD

    def test_slice_write(self):
        reg = BV("R", 16)
        reg.value = 0x0000
        reg[7:4].value = 0xF
        assert reg.value == 0x00F0

    def test_slice_width(self):
        reg = BV("R", 16)
        s = reg[7:0]
        assert s.width == 8
        assert isinstance(s, BVSlice)

    def test_slice_invalid_range(self):
        reg = BV("R", 8)
        with pytest.raises(ValueError):
            reg[3:5]

    def test_slice_out_of_range(self):
        reg = BV("R", 8)
        with pytest.raises(ValueError):
            reg[8:0]

    def test_slice_requires_slice(self):
        reg = BV("R", 8)
        with pytest.raises(TypeError):
            reg[3]


class TestLogicOps:
    def test_and(self):
        a = BV("A", 8); b = BV("B", 8)
        a.value = 0xFF; b.value = 0x0F
        assert (a & b).value == 0x0F

    def test_or(self):
        a = BV("A", 8); b = BV("B", 8)
        a.value = 0xF0; b.value = 0x0F
        assert (a | b).value == 0xFF

    def test_xor(self):
        a = BV("A", 8); b = BV("B", 8)
        a.value = 0xFF; b.value = 0x0F
        assert (a ^ b).value == 0xF0

    def test_invert(self):
        a = BV("A", 8); a.value = 0x0F
        assert (~a).value == 0xF0

    def test_and_with_int(self):
        a = BV("A", 16); a.value = 0xABCD
        assert (a & 0x00FF).value == 0x00CD

    def test_complex_expr(self):
        reg0 = BV("REG0", 16); reg1 = BV("REG1", 16)
        reg0.value = 0xABCD; reg1.value = 0x00FF
        full_logic = (reg0 & 0x0F) | (reg1 ^ BV(0xFF, 16))
        assert full_logic.value == 0x000D


class TestEval:
    def test_eval_linked(self):
        reg0 = BV("REG0", 16); reg1 = BV("REG1", 16)
        padding = BV(0, 2)
        sram00 = BV("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        reg0.value = 0x5; reg1.value = 0x2
        simulated = sram00.eval({"REG0": 0xA, "REG1": 0x3})
        assert simulated == 0xA3
        assert reg0.value == 0x5
        assert reg1.value == 0x2
        assert sram00.value == 0x52

    def test_eval_const(self):
        assert BV(0x3, 4).eval({}) == 0x3

    def test_eval_named_fallback(self):
        reg = BV("R", 8); reg.value = 0x42
        assert reg.eval({}) == 0x42

    def test_eval_expr(self):
        a = BV("A", 8); b = BV("B", 8)
        assert (a & b).eval({"A": 0xFF, "B": 0x0F}) == 0x0F


class TestStructure:
    def test_structure_linked(self):
        reg0 = BV("REG0", 16, tags={"type": "RW"})
        reg1 = BV("REG1", 16, tags={"type": "RO"})
        padding = BV(0, 2)
        sram00 = BV("SRAM_00", 8)
        sram00.link(reg0[3:0], padding, reg1[1:0])
        layout = sram00.structure
        assert len(layout) == 3
        assert layout[0].bv.name == "REG0"
        assert layout[0].slice_range == (3, 0)
        assert layout[1].bv.name == "CONST"
        assert layout[1].slice_range is None
        assert layout[2].bv.name == "REG1"
        assert layout[2].slice_range == (1, 0)

    def test_structure_unlinked(self):
        assert BV("R", 8).structure == []

    def test_tags_query(self):
        reg0 = BV("REG0", 16, tags={"type": "RW", "addr": 256})
        assert reg0.tags["type"] == "RW"
        assert reg0.tags["addr"] == 256


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — Enhancements
# ═══════════════════════════════════════════════════════════════════════════


# ── Fix #1: Const write warning ─────────────────────────────────────────

class TestConstWriteWarning:
    def test_const_write_warns(self):
        c = BV(0x0, 4)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            c.value = 0xF
            assert len(w) == 1
            assert "constant" in str(w[0].message).lower()
        # Value should NOT have changed
        assert c.value == 0x0

    def test_link_write_to_const_child_warns(self):
        """Writing sram that contains a const child should warn."""
        reg = BV("R", 4)
        pad = BV(0, 4)
        sram = BV("SRAM", 8)
        sram.link(reg, pad)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sram.value = 0xFF
            # Expecting warning from padding's setter
            const_warns = [x for x in w if "constant" in str(x.message).lower()]
            assert len(const_warns) >= 1
        assert reg.value == 0xF
        assert pad.value == 0x0  # unchanged


# ── Fix #2: BVSlice logic operators ─────────────────────────────────────

class TestSliceLogicOps:
    def test_slice_and(self):
        a = BV("A", 16); a.value = 0xABCD
        assert (a[7:0] & 0x0F).value == 0x0D

    def test_slice_or(self):
        a = BV("A", 8); a.value = 0xF0
        assert (a[7:4] | 0x0).value == 0xF

    def test_slice_xor_slice(self):
        a = BV("A", 16); b = BV("B", 16)
        a.value = 0xFF00; b.value = 0x00FF
        assert (a[15:8] ^ b[7:0]).value == 0x00  # 0xFF ^ 0xFF

    def test_slice_invert(self):
        a = BV("A", 8); a.value = 0x0F
        assert (~a[3:0]).value == 0x0  # ~0xF masked to 4 bits = 0x0

    def test_slice_shift(self):
        a = BV("A", 8); a.value = 0x0F
        assert (a[7:0] << 4).value == 0xF0
        assert (a[7:0] >> 2).value == 0x03


# ── Fix #3: BVSlice as link target ──────────────────────────────────────

class TestSliceLinkTarget:
    def test_slice_link(self):
        reg = BV("REG", 16)
        reg.value = 0xABCD
        field_a = BV("FA", 4)
        field_b = BV("FB", 4)
        field_a.value = 0x1
        field_b.value = 0x2
        # Link reg[7:0] = {field_a, field_b}
        reg[7:0].link(field_a, field_b)
        # Upper 8 bits should be preserved (0xAB)
        # Lower 8 bits = {0x1, 0x2} = 0x12
        assert reg[7:0].value == 0x12
        assert reg[15:8].value == 0xAB

    def test_slice_link_width_mismatch(self):
        reg = BV("REG", 16)
        a = BV("A", 3)
        with pytest.raises(ValueError, match="width mismatch"):
            reg[7:0].link(a)  # 3 != 8


# ── Fix #4: __eq__ / __int__ ───────────────────────────────────────────

class TestEqInt:
    def test_bv_eq_int(self):
        a = BV("A", 8); a.value = 0x42
        assert a == 0x42
        assert not (a == 0x43)

    def test_bv_eq_bv(self):
        a = BV("A", 8); b = BV("B", 8)
        a.value = 0x10; b.value = 0x10
        assert a == b

    def test_int_conversion(self):
        a = BV("A", 8); a.value = 0x42
        assert int(a) == 0x42

    def test_slice_eq_int(self):
        a = BV("A", 16); a.value = 0xABCD
        assert a[7:0] == 0xCD

    def test_slice_int(self):
        a = BV("A", 16); a.value = 0xABCD
        assert int(a[7:0]) == 0xCD

    def test_expr_eq_int(self):
        a = BV("A", 8); a.value = 0xFF
        assert (a & 0x0F) == 0x0F

    def test_expr_int(self):
        a = BV("A", 8); a.value = 0xFF
        assert int(a & 0x0F) == 0x0F

    def test_hash_identity(self):
        """Different BVs with same value should have different identity."""
        a = BV("A", 8); b = BV("B", 8)
        a.value = 0x42; b.value = 0x42
        # They are equal in value but can be used as separate dict keys
        d = {a: "first", b: "second"}
        assert d[a] == "first"
        assert d[b] == "second"


# ── Fix #5: hex/bin formatting ──────────────────────────────────────────

class TestFormatting:
    def test_bv_to_hex(self):
        a = BV("A", 16); a.value = 0x00FF
        assert a.to_hex() == "0x00FF"

    def test_bv_to_bin(self):
        a = BV("A", 8); a.value = 0x0F
        assert a.to_bin() == "0b00001111"

    def test_bv_format_hex(self):
        a = BV("A", 8); a.value = 0xAB
        assert f"{a:x}" == "0xAB"
        assert f"{a:hex}" == "0xAB"

    def test_bv_format_bin(self):
        a = BV("A", 8); a.value = 0x0F
        assert f"{a:b}" == "0b00001111"
        assert f"{a:bin}" == "0b00001111"

    def test_slice_to_hex(self):
        a = BV("A", 16); a.value = 0xABCD
        assert a[7:0].to_hex() == "0xCD"

    def test_expr_to_hex(self):
        a = BV("A", 8); a.value = 0xFF
        assert (a & 0x0F).to_hex() == "0x0F"

    def test_format_default(self):
        a = BV("A", 8); a.value = 42
        assert f"{a:d}" == "42"


# ── Fix #6: Re-link warning ────────────────────────────────────────────

class TestRelinkWarning:
    def test_relink_warns(self):
        sram = BV("SRAM", 8)
        a = BV("A", 4); b = BV("B", 4)
        c = BV("C", 8)
        sram.link(a, b)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sram.link(c)
            assert len(w) == 1
            assert "already linked" in str(w[0].message).lower()


# ── Feature A: Shift operators ──────────────────────────────────────────

class TestShiftOps:
    def test_lshift(self):
        a = BV("A", 8); a.value = 0x0F
        assert (a << 4).value == 0xF0

    def test_rshift(self):
        a = BV("A", 8); a.value = 0xF0
        assert (a >> 4).value == 0x0F

    def test_shift_masked(self):
        a = BV("A", 8); a.value = 0xFF
        assert (a << 4).value == 0xF0  # upper bits masked out

    def test_shift_eval(self):
        a = BV("A", 8)
        assert (a << 2).eval({"A": 0x03}) == 0x0C

    def test_shift_chain(self):
        a = BV("A", 8); a.value = 0xFF
        assert ((a << 4) >> 4).value == 0x0F


# ── Feature B: __len__ ──────────────────────────────────────────────────

class TestLen:
    def test_bv_len(self):
        assert len(BV("A", 16)) == 16

    def test_slice_len(self):
        assert len(BV("A", 16)[7:0]) == 8

    def test_expr_len(self):
        a = BV("A", 8); b = BV("B", 8)
        assert len(a & b) == 8


# ── Feature C: to_hex / to_bin (also tested in Fix #5 but extra) ───────

class TestFormatMethods:
    def test_const_to_hex(self):
        c = BV(0xFF, 8)
        assert c.to_hex() == "0xFF"
        assert c.to_bin() == "0b11111111"

    def test_1bit(self):
        b = BV("B", 1); b.value = 1
        assert b.to_hex() == "0x1"
        assert b.to_bin() == "0b1"


# ── Feature D: BV.concat() ─────────────────────────────────────────────

class TestConcat:
    def test_concat_basic(self):
        a = BV("A", 4); b = BV("B", 4)
        a.value = 0xA; b.value = 0x5
        c = BV.concat(a, b)
        assert c.width == 8
        assert c.value == 0xA5

    def test_concat_with_slices(self):
        r = BV("R", 16); r.value = 0xABCD
        c = BV.concat(r[15:8], r[7:0])
        assert c.value == 0xABCD

    def test_concat_custom_name(self):
        a = BV("A", 4)
        c = BV.concat(a, name="MY_CONCAT")
        assert c.name == "MY_CONCAT"

    def test_concat_write_back(self):
        a = BV("A", 4); b = BV("B", 4)
        c = BV.concat(a, b)
        c.value = 0x37
        assert a.value == 0x3
        assert b.value == 0x7


# ── Feature E: unlink() ────────────────────────────────────────────────

class TestUnlink:
    def test_unlink_snapshots_value(self):
        a = BV("A", 4); b = BV("B", 4)
        a.value = 0xA; b.value = 0x5
        sram = BV("SRAM", 8)
        sram.link(a, b)
        assert sram.value == 0xA5
        sram.unlink()
        assert sram.value == 0xA5  # value preserved
        assert sram.structure == []  # no longer linked
        # Changing a should NOT affect sram anymore
        a.value = 0x0
        assert sram.value == 0xA5

    def test_unlink_noop_if_not_linked(self):
        a = BV("A", 8); a.value = 0x42
        a.unlink()  # should not error
        assert a.value == 0x42


# ── Feature F: copy() / snapshot() ─────────────────────────────────────

class TestCopy:
    def test_copy_basic(self):
        a = BV("A", 8, tags={"type": "RW"})
        a.value = 0x42
        b = a.copy()
        assert b.name == "A_copy"
        assert b.value == 0x42
        assert b.tags == {"type": "RW"}
        # Independent — changing a should not affect b
        a.value = 0x00
        assert b.value == 0x42

    def test_copy_custom_name(self):
        a = BV("A", 8)
        b = a.copy("B")
        assert b.name == "B"

    def test_snapshot_alias(self):
        a = BV("A", 8); a.value = 0xFF
        b = a.snapshot()
        assert b.value == 0xFF

    def test_copy_linked_bv(self):
        """Copying a linked BV should snapshot the composite value."""
        x = BV("X", 4); y = BV("Y", 4)
        x.value = 0xA; y.value = 0x5
        sram = BV("SRAM", 8)
        sram.link(x, y)
        c = sram.copy()
        assert c.value == 0xA5
        assert c.structure == []  # copy is not linked

    def test_copy_deep_tags(self):
        """Tags should be deep-copied."""
        a = BV("A", 8, tags={"nested": [1, 2, 3]})
        b = a.copy()
        b.tags["nested"].append(4)
        assert a.tags["nested"] == [1, 2, 3]  # original unaffected


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_write_then_read_consistency(self):
        """Write sram, immediately read back, should be identical."""
        a = BV("A", 4); b = BV("B", 4)
        sram = BV("SRAM", 8)
        sram.link(a, b)
        for val in [0x00, 0xFF, 0xA5, 0x5A, 0x12]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sram.value = val
                assert sram.value == val

    def test_multi_layer_link(self):
        """A links B, B links C — three-layer sync."""
        c1 = BV("C1", 4); c2 = BV("C2", 4)
        b = BV("B", 8)
        b.link(c1, c2)  # B = {C1, C2}
        a = BV("A", 8)
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
        """Same BV slices linked to different SRAMs."""
        reg = BV("REG", 16)
        reg.value = 0xABCD
        sram0 = BV("S0", 8)
        sram1 = BV("S1", 8)
        sram0.link(reg[7:0])    # S0 = REG[7:0]
        sram1.link(reg[15:8])   # S1 = REG[15:8]
        assert sram0.value == 0xCD
        assert sram1.value == 0xAB
        # Modify reg, both SRAMs reflect
        reg.value = 0x1234
        assert sram0.value == 0x34
        assert sram1.value == 0x12

    def test_1bit_bv(self):
        b = BV("B", 1)
        b.value = 1
        assert b.value == 1
        assert (~b).value == 0
        assert len(b) == 1

    def test_32bit_bv(self):
        b = BV("W", 32)
        b.value = 0xDEADBEEF
        assert b.value == 0xDEADBEEF
        assert b.to_hex() == "0xDEADBEEF"

    def test_reverse_ops_int_left(self):
        """int on the left: 0xFF & reg."""
        a = BV("A", 8); a.value = 0xAB
        assert (0x0F & a).value == 0x0B
        assert (0x0F | a).value == 0xAF
        assert (0x0F ^ a).value == 0xA4


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — Tag-Aware Eval
# ═══════════════════════════════════════════════════════════════════════════


class TestTagAwareEval:
    """Tests for BV.key() and tag-aware eval()."""

    def test_red_green_scenario(self):
        """The user's primary use case: same-name regs under different tags."""
        reg0_red = BV("REG0", 16, tags={"color": "red"})
        reg1_red = BV("REG1", 16, tags={"color": "red"})
        reg0_green = BV("REG0", 16, tags={"color": "green"})
        reg1_green = BV("REG1", 16, tags={"color": "green"})

        sram_red = BV.concat(reg0_red[3:0], reg1_red[3:0], name="SRAM_RED")
        sram_green = BV.concat(reg0_green[3:0], reg1_green[3:0], name="SRAM_GREEN")

        # Eval red scenario
        ctx_red = {
            BV.key("REG0", {"color": "red"}): 0x1,
            BV.key("REG1", {"color": "red"}): 0x2,
        }
        assert sram_red.eval(ctx_red) == 0x12

        # Eval green scenario
        ctx_green = {
            BV.key("REG0", {"color": "green"}): 0x5,
            BV.key("REG1", {"color": "green"}): 0x2,
        }
        assert sram_green.eval(ctx_green) == 0x52

    def test_tagged_key_priority_over_name(self):
        """Tagged key should take priority over name-only key."""
        reg = BV("REG0", 8, tags={"color": "red"})
        ctx = {
            "REG0": 0xAA,                              # name-only fallback
            BV.key("REG0", {"color": "red"}): 0xBB,    # tagged: exact match
        }
        assert reg.eval(ctx) == 0xBB

    def test_name_only_applies_to_all(self):
        """Name-only key applies to any BV with that name, regardless of tags."""
        reg_red = BV("REG0", 8, tags={"color": "red"})
        reg_green = BV("REG0", 8, tags={"color": "green"})
        reg_no_tags = BV("REG0", 8)

        ctx = {"REG0": 0x42}
        assert reg_red.eval(ctx) == 0x42
        assert reg_green.eval(ctx) == 0x42
        assert reg_no_tags.eval(ctx) == 0x42

    def test_tags_must_fully_match(self):
        """Partial tag match should NOT work — must be exact dict match."""
        reg = BV("REG0", 8, tags={"color": "red", "type": "RW"})

        # Only partial match: {"color": "red"} != {"color": "red", "type": "RW"}
        ctx = {
            BV.key("REG0", {"color": "red"}): 0xAA,
        }
        # Should NOT match the tagged key — falls through to fallback
        reg.value = 0x55
        assert reg.eval(ctx) == 0x55  # fallback to current value

    def test_mixed_context(self):
        """Use both tagged and name-only keys in the same context."""
        reg0_red = BV("REG0", 8, tags={"color": "red"})
        reg1 = BV("REG1", 8)  # no tags

        sram = BV.concat(reg0_red, reg1, name="SRAM")

        ctx = {
            BV.key("REG0", {"color": "red"}): 0xAB,
            "REG1": 0xCD,
        }
        assert sram.eval(ctx) == 0xABCD

    def test_eval_key_property(self):
        reg = BV("REG0", 8, tags={"color": "red"})
        assert reg.eval_key == ("REG0", frozenset({"color": "red"}.items()))

    def test_eval_key_none_for_empty_tags(self):
        reg = BV("REG0", 8)
        assert reg.eval_key is None

    def test_eval_key_none_for_const(self):
        c = BV(0, 4)
        assert c.eval_key is None

    def test_bv_key_static(self):
        k = BV.key("REG0", {"a": 1, "b": 2})
        assert isinstance(k, tuple)
        assert k[0] == "REG0"
        assert k[1] == frozenset({"a": 1, "b": 2}.items())

    def test_linked_sram_eval_with_tags(self):
        """Full end-to-end: linked SRAM with tagged regs, eval with tagged ctx."""
        reg0 = BV("REG0", 16, tags={"color": "red"})
        reg1 = BV("REG1", 16, tags={"color": "red"})
        padding = BV(0, 2)
        sram = BV("SRAM", 8)
        sram.link(reg0[3:0], padding, reg1[1:0])

        ctx = {
            BV.key("REG0", {"color": "red"}): 0xA,
            BV.key("REG1", {"color": "red"}): 0x3,
        }
        # {1010, 00, 11} = 10100011 = 0xA3
        assert sram.eval(ctx) == 0xA3

