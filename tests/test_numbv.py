"""Comprehensive tests for NumBV — pure fixed-point calculator.

Sections:
  1. Declaration
  2. Bit access (.bits, .hex, .bin, slicing)
  3. Arithmetic (+, -, *, /, <<, >>, neg, abs) — with auto-limit
  4. In-place operators (+=, -=, *=, /=, <<=, >>=)
  5. Auto-limit / Saturation
  6. Comparisons
  7. Type conversions (int, float, bool, round)
  8. Copy / Resize
  9. Formatting (__format__, __repr__)
 10. Report
 11. Immutability (normal ops don't mutate)
"""

import pytest
from mypkg import NumBV


# ═══════════════════════════════════════════════════════════════════════════
# 1. Declaration
# ═══════════════════════════════════════════════════════════════════════════


class TestDeclaration:
    def test_basic(self):
        reg = NumBV(16, 8, signed=True)
        assert reg.width == 16
        assert reg.frac == 8
        assert reg.signed is True
        assert reg.val == 0.0

    def test_value_on_init(self):
        assert NumBV(16, 8, value=0.75).val == 0.75

    def test_from_val(self):
        reg = NumBV(16, 8)
        reg.from_val(0.75)
        assert reg.val == 0.75

    def test_from_bits(self):
        reg = NumBV(16, 8)
        reg.from_bits(0x00C0)
        assert reg.val == 0.75

    def test_from_val_and_bits_agree(self):
        a = NumBV(16, 8); a.from_val(0.75)
        b = NumBV(16, 8); b.from_bits(0x00C0)
        assert a.val == b.val and a.bits == b.bits

    def test_unsigned(self):
        assert NumBV(8, 4, signed=False, value=3.5).val == 3.5

    def test_custom_overflow_rounding(self):
        reg = NumBV(8, 0, overflow="wrap", rounding="around")
        assert reg.overflow == "wrap"
        assert reg.rounding == "around"

    def test_overflow_wrap_on_init(self):
        assert NumBV(8, 0, signed=True, overflow="wrap", value=130).val == -126.0

    def test_invalid_width(self):
        with pytest.raises(ValueError): NumBV(0, 0)

    def test_invalid_frac(self):
        with pytest.raises(ValueError): NumBV(8, 8)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Bit Access
# ═══════════════════════════════════════════════════════════════════════════


class TestBitAccess:
    def test_bits_int(self):
        reg = NumBV(16, 8, value=0.75)
        assert isinstance(reg.bits, int) and reg.bits == 0x00C0

    def test_negative_bits(self):
        assert NumBV(16, 8, signed=True, value=-1.0).bits == 0xFF00

    def test_hex(self):
        assert NumBV(16, 8, value=0.75).hex == "0x00C0"

    def test_bin(self):
        assert NumBV(16, 8, value=0.75).bin == "0b0000000011000000"

    def test_slice(self):
        reg = NumBV(16, 8, value=0.75)
        assert reg[15:8] == 0x00 and reg[7:0] == 0xC0

    def test_slice_errors(self):
        reg = NumBV(16, 8)
        with pytest.raises(TypeError):  reg[5]
        with pytest.raises(ValueError): reg[3:5]
        with pytest.raises(ValueError): reg[16:0]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Arithmetic (auto-limited)
# ═══════════════════════════════════════════════════════════════════════════


class TestArithmetic:
    def test_add(self):
        a = NumBV(16, 8, value=1.0)
        b = a + 0.5
        assert isinstance(b, NumBV)
        assert abs(b.val - 1.5) < 0.01

    def test_radd(self):
        assert abs((5.0 + NumBV(16, 8, value=2.0)).val - 7.0) < 0.01

    def test_sub(self):
        assert abs((NumBV(16, 8, value=2.0) - 0.5).val - 1.5) < 0.01

    def test_rsub(self):
        assert abs((5.0 - NumBV(16, 8, value=2.0)).val - 3.0) < 0.01

    def test_mul(self):
        assert abs((NumBV(16, 8, value=0.75) * 1.5).val - 1.125) < 0.01

    def test_rmul(self):
        assert abs((1.5 * NumBV(16, 8, value=2.0)).val - 3.0) < 0.01

    def test_div(self):
        assert abs((NumBV(16, 8, value=3.0) / 2).val - 1.5) < 0.01

    def test_rtruediv(self):
        assert abs((6.0 / NumBV(16, 8, value=2.0)).val - 3.0) < 0.01

    def test_neg(self):
        assert abs((-NumBV(16, 8, value=1.5)).val + 1.5) < 0.01

    def test_abs_negative(self):
        assert abs(abs(NumBV(16, 8, signed=True, value=-1.5)).val - 1.5) < 0.01

    def test_chain(self):
        """(0.75 * 2) + 0.5 = 2.0"""
        assert abs((NumBV(16, 8, value=0.75) * 2 + 0.5).val - 2.0) < 0.01

    def test_numbv_plus_numbv(self):
        a = NumBV(16, 8, value=1.0)
        b = NumBV(16, 8, value=0.5)
        assert abs((a + b).val - 1.5) < 0.01

    def test_auto_limit_format_preserved(self):
        """Result stays in the left operand's format."""
        a = NumBV(16, 8, value=1.0)
        b = a + 0.5
        assert b.width == 16 and b.frac == 8

    def test_lshift(self):
        a = NumBV(16, 8, value=1.0)
        b = a << 1
        assert b.bits == (a.bits << 1) & 0xFFFF

    def test_rshift_unsigned(self):
        a = NumBV(8, 0, signed=False, value=128)
        assert (a >> 1).val == 64

    def test_rshift_signed(self):
        a = NumBV(8, 0, signed=True, value=-4)
        assert (a >> 1).val == -2


# ═══════════════════════════════════════════════════════════════════════════
# 4. In-place Operators
# ═══════════════════════════════════════════════════════════════════════════


class TestInPlace:
    def test_iadd(self):
        a = NumBV(16, 8, value=1.0)
        a += 0.5
        assert abs(a.val - 1.5) < 0.01

    def test_isub(self):
        a = NumBV(16, 8, value=2.0)
        a -= 0.5
        assert abs(a.val - 1.5) < 0.01

    def test_imul(self):
        a = NumBV(16, 8, value=2.0)
        a *= 1.5
        assert abs(a.val - 3.0) < 0.01

    def test_itruediv(self):
        a = NumBV(16, 8, value=3.0)
        a /= 2
        assert abs(a.val - 1.5) < 0.01

    def test_ilshift(self):
        a = NumBV(16, 8, value=1.0)
        orig_bits = a.bits
        a <<= 1
        assert a.bits == (orig_bits << 1) & 0xFFFF

    def test_irshift(self):
        a = NumBV(8, 0, signed=False, value=128)
        a >>= 1
        assert a.val == 64

    def test_iadd_same_object(self):
        """In-place modifies self, not rebind."""
        a = NumBV(16, 8, value=1.0)
        b = a
        a += 0.5
        assert a is b  # same object!
        assert abs(b.val - 1.5) < 0.01

    def test_iadd_auto_limit(self):
        """In-place also auto-limits."""
        a = NumBV(8, 0, signed=True, value=120)
        a += 10
        assert a.val == 127.0  # saturated
        assert a.width == 8

    def test_imul_format_preserved(self):
        a = NumBV(16, 8, value=0.75)
        a *= 2
        assert a.width == 16 and a.frac == 8


# ═══════════════════════════════════════════════════════════════════════════
# 5. Auto-Limit / Saturation
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoLimit:
    def test_saturate_overflow(self):
        """120 + 10 = 130 > 127 → auto-saturate to 127."""
        x = NumBV(8, 0, signed=True, value=120)
        y = x + 10
        assert y.val == 127.0
        assert y.width == 8

    def test_saturate_underflow(self):
        x = NumBV(8, 0, signed=True, value=-120)
        y = x - 10
        assert y.val == -128.0

    def test_wrap_overflow(self):
        x = NumBV(8, 0, signed=True, overflow="wrap", value=120)
        y = x + 10
        assert y.val == -126.0

    def test_no_clipping(self):
        x = NumBV(8, 0, signed=True, value=50)
        y = x + 10
        assert y.val == 60.0

    def test_mul_auto_limit(self):
        """Q8.8: 100 * 2 = 200 > 127.99 → saturate."""
        x = NumBV(16, 8, signed=True, value=100)
        y = x * 2
        # Q7.8 max = 127.996...
        assert y.val <= 128.0
        assert y.width == 16

    def test_unsigned_saturation(self):
        x = NumBV(8, 0, signed=False, value=250)
        y = x + 10
        assert y.val == 255.0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Comparisons
# ═══════════════════════════════════════════════════════════════════════════


class TestCompare:
    def test_eq_float(self):  assert NumBV(16, 8, value=1.5) == 1.5
    def test_ne(self):        assert NumBV(16, 8, value=1.5) != 2.0
    def test_lt(self):        assert NumBV(16, 8, value=1.0) < 2.0
    def test_le(self):        assert NumBV(16, 8, value=1.0) <= 1.0
    def test_gt(self):        assert NumBV(16, 8, value=2.0) > 1.0
    def test_ge(self):        assert NumBV(16, 8, value=1.0) >= 1.0

    def test_eq_numbv(self):
        assert NumBV(16, 8, value=1.5) == NumBV(16, 8, value=1.5)

    def test_compare_numbv(self):
        a, b = NumBV(16, 8, value=1.0), NumBV(16, 8, value=2.0)
        assert a < b and b > a and a != b

    def test_compare_result(self):
        assert (NumBV(16, 8, value=0.75) * 2) > 1.0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Type Conversions
# ═══════════════════════════════════════════════════════════════════════════


class TestConversions:
    def test_int(self):   assert int(NumBV(16, 8, value=0.75)) == 0x00C0
    def test_float(self): assert float(NumBV(16, 8, value=0.75)) == 0.75
    def test_bool_true(self):  assert bool(NumBV(8, 0, value=1))
    def test_bool_false(self): assert not bool(NumBV(8, 0, value=0))
    def test_round(self): assert round(NumBV(16, 8, value=1.75)) == 2.0
    def test_len(self):   assert len(NumBV(16, 8)) == 16


# ═══════════════════════════════════════════════════════════════════════════
# 8. Copy / Resize
# ═══════════════════════════════════════════════════════════════════════════


class TestCopyResize:
    def test_copy_basic(self):
        a = NumBV(16, 8, value=1.5)
        b = a.copy()
        assert b.val == 1.5 and b.width == 16

    def test_copy_independent(self):
        a = NumBV(16, 8, value=1.5)
        b = a.copy()
        a.from_val(0.0)
        assert b.val == 1.5

    def test_copy_preserves_config(self):
        b = NumBV(16, 8, overflow="wrap", rounding="around").copy()
        assert b.overflow == "wrap" and b.rounding == "around"

    def test_resize_basic(self):
        y = NumBV(16, 8, value=1.5).resize(32, 16)
        assert y.width == 32 and abs(y.val - 1.5) < 0.001

    def test_resize_narrow(self):
        y = NumBV(32, 16, value=1.123).resize(8, 4)
        assert y.width == 8

    def test_resize_change_signed(self):
        y = NumBV(16, 8, signed=True, value=1.5).resize(16, 8, new_signed=False)
        assert y.signed is False

    def test_resize_with_overflow(self):
        a = NumBV(16, 8, value=1.5)
        b = a.resize(8, 4, overflow='wrap')
        assert b.width == 8 and b.frac == 4
        assert b.overflow == 'wrap'


# ═══════════════════════════════════════════════════════════════════════════
# 9. Formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestFormat:
    def test_hex(self):   assert f"{NumBV(16, 8, value=0.75):hex}" == "0x00C0"
    def test_bin(self):   assert f"{NumBV(16, 8, value=0.75):bin}" == "0b0000000011000000"
    def test_float(self): assert f"{NumBV(16, 8, value=1.5):.1f}" == "1.5"
    def test_repr(self):
        r = repr(NumBV(16, 8, signed=True, value=1.5))
        assert "NumBV" in r and "1.5" in r


# ═══════════════════════════════════════════════════════════════════════════
# 10. Report
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def test_basic(self):
        text = NumBV(16, 8, signed=True, value=0.75).report()
        assert "0.75" in text and "Q7.8" in text and "0x00C0" in text

    def test_returns_string(self):
        assert isinstance(NumBV(16, 8).report(), str)

    def test_shows_config(self):
        text = NumBV(16, 8, overflow="wrap", rounding="around").report()
        assert "wrap" in text.lower() and "around" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 11. Immutability (normal ops don't mutate)
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_add(self):
        a = NumBV(16, 8, value=1.0); _ = a + 999; assert a.val == 1.0

    def test_mul(self):
        a = NumBV(16, 8, value=0.75); _ = a * 100; assert a.val == 0.75

    def test_neg(self):
        a = NumBV(16, 8, value=1.0); _ = -a; assert a.val == 1.0

    def test_shift(self):
        a = NumBV(16, 8, value=1.0); _ = a << 4; assert a.val == 1.0
