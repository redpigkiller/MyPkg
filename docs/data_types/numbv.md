# NumBV — Fixed-Point Arithmetic

[![English](https://img.shields.io/badge/Language-English-blue.svg)](numbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](numbv_zh.md)

Like NumPy is to matrices, NumBV is the arithmetic engine for fixed-point numbers.
Once the format is defined, all operations **automatically preserve the format** without requiring manual overflow handling.

## Quick Start

```python
from mypkg import NumBV

# Declare Q8.8 format (16-bit, 8-bit fraction)
a = NumBV(16, 8, value=0.75)

# Direct calculation — result automatically stays Q8.8
b = a * 1.5
print(b.val, b.width)            # → 1.125, 16

# Auto-saturation — prevents overflow wrapping
x = NumBV(8, 0, signed=True, value=120)
y = x + 10
print(y.val)                     # → 127 (Auto-saturated!)

# In-place operation
x *= 2                           # x is modified in-place, same object
```

---

## Features

### 1. Declaration

```python
a = NumBV(16, 8)                               # Q8.8 signed
b = NumBV(8, 4, signed=False, value=3.5)       # Q4.4 unsigned
c = NumBV(16, 8, overflow='wrap', rounding='around')
```

### 2. Arithmetic — Auto-Limit

All arithmetic results **automatically return to the format of the left operand**, handling overflow according to the `overflow` setting.

```python
# New objects (a remains unchanged)
b = a + 0.5
b = a * 1.5
b = 5.0 - a
b = -a
b = abs(a)
b = a << 4            # Bitwise left shift
b = a >> 2            # Right shift (signed = arithmetic shift)
```

### 3. In-Place Operations

```python
# Modifies self (same object)
a += 0.5
a -= 1
a *= 2
a /= 4
a <<= 1
a >>= 2
```

### 4. Auto-Saturation

```python
x = NumBV(8, 0, signed=True, value=120)
y = x + 10            # → 127 (saturate, default)

z = NumBV(8, 0, signed=True, overflow='wrap', value=120)
w = z + 10            # → -126 (wrap)
```

### 5. Bit Access

```python
a.bits                  # → int (unsigned raw value)
a.hex                   # → "0x00C0"
a.bin                   # → "0b0000000011000000"
a[15:8]                 # → 0x00 (High 8 bits)
a.bits & 0xFF           # → Python int bitwise operations
```

### 6. Comparisons & Conversions

```python
a > 1.0                 # True/False
a == b                  # NumBV vs NumBV
int(a)                  # → bits (raw int)
float(a)                # → val (float)
bool(a)                 # → True if val != 0
round(a)                # → rounded float
len(a)                  # → width
```

### 7. Format Conversion

```python
b = a.copy()                     # Independent deep copy
y = a.cast(32, 16)               # Q8.8 → Q16.16
z = a.cast(8, 4)                 # Q8.8 → Q4.4

f"{a:hex}"                       # → "0x00C0"
f"{a:bin}"                       # → "0b00..."
f"{a:.2f}"                       # → "0.75"
```

### 8. Debug Report

```python
NumBV(16, 8, value=0.75).report()
# Value     : 0.75
# Bits      : 0x00C0 (0b0000000011000000)
# Q-Format  : Q7.8 (Signed)
# Range     : [-128.0, 127.99609375]
# Precision : 0.00390625
# Overflow  : saturate
# Rounding  : trunc
```

### 9. Array Processing (`NumBVArray`)

For performance and convenience, use `NumBVArray` (vectorized operations based on `fxpmath`) instead of a list of `NumBV`.

```python
from mypkg import NumBVArray

# Create array
arr = NumBVArray(16, 8, values=[1.0, 2.0, 3.0])

# Vectorized operation (1.3x overhead vs raw fxpmath)
result = arr * 2               # → [2.0, 4.0, 6.0] (NumBVArray)

# Indexing & Slicing (List/NumPy style)
val = arr[0]                   # → Scalar NumBV (Example: 2.0)
sub = arr[0:2]                 # → Sub-NumBVArray (Example: [2.0, 4.0])

# Bit Slicing (Chaining)
# arr[0] returns a scalar, so we can chain bit slicing
bits = arr[0][15:8]            # → int (High byte of element 0)
```

> **Design Note**: Array indexing `[]` on `NumBVArray` operates on **Elements** (following Python list/NumPy conventions), while `[]` on `NumBV` (Scalar) operates on **Bits** (following SystemVerilog conventions).
> 
> - `arr[i]` → Gets the i-th element
> - `reg[h:l]` → Gets bits h~l

---

## API Reference

```python
NumBV(width, frac, signed=True, value=0, overflow='saturate', rounding='trunc')
```

| Member | Type | Description |
|:-------|:-----|:------------|
| `.val` | `float` | Current value (quantized) |
| `.bits` | `int` | Raw bit pattern (unsigned) |
| `.hex` / `.bin` | `str` | Formatted string |
| `.from_val(x)` | method | Set from real number |
| `.from_bits(x)` | method | Set from raw bits |
| `.cast(w, f)` | method | New NumBV, different format |
| `.copy()` | method | Independent deep copy |
| `.report()` | method | Print debug summary |
| `+ - * /` | operators | Arithmetic (→ new NumBV) |
| `+= -= *= /=` | in-place | Modify self |
| `<< >>` | shifts | Bit-level shift |
| `== < > <= >=` | compare | Value comparison |
| `int() float()` | builtins | Type conversion |
| `[h:l]` | slice | Read bits (inclusive) |
