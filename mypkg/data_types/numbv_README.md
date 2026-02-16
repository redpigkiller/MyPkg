# NumBV — Fixed-Point Arithmetic

像 numpy 之於矩陣，NumBV 是定點數的運算引擎。  
定義好格式後，所有運算**自動保持格式**，不需要手動處理溢位。

## Quick Start

```python
from mypkg import NumBV

# 宣告 Q8.8 格式 (16-bit, 8-bit fraction)
a = NumBV(16, 8, value=0.75)

# 直接運算 — 結果自動保持 Q8.8
b = a * 1.5
print(b.val, b.width)            # → 1.125, 16

# 自動飽和 — 不會溢位反轉
x = NumBV(8, 0, signed=True, value=120)
y = x + 10
print(y.val)                     # → 127 (自動飽和！)

# In-place 運算
x *= 2                           # x 被修改, 同一個物件
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

所有運算結果**自動回到左運算元的格式**，依 `overflow` 設定處理溢位。

```python
# 新物件（a 不變）
b = a + 0.5
b = a * 1.5
b = 5.0 - a
b = -a
b = abs(a)
b = a << 4            # 位元左移
b = a >> 2            # 右移 (signed = 算術移位)
```

### 3. In-Place 運算

```python
# 修改自身（同一個物件）
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
y = x + 10            # → 127 (saturate，預設)

z = NumBV(8, 0, signed=True, overflow='wrap', value=120)
w = z + 10            # → -126 (wrap)
```

### 5. Bit Access

```python
a.bits                  # → int (unsigned raw value)
a.hex                   # → "0x00C0"
a.bin                   # → "0b0000000011000000"
a[15:8]                 # → 0x00 (高 8 位元)
a.bits & 0xFF           # → Python int 位元運算
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
y = a.resize(32, 16)             # Q8.8 → Q16.16
z = a.resize(8, 4)               # Q8.8 → Q4.4

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

### 9. Array of NumBV

NumBV 是 scalar。需要 array 時，用 Python list：

```python
data = [NumBV(16, 8, value=v) for v in [0.1, 0.2, 0.5]]
output = [x * 0.707 for x in data]
values = [x.val for x in output]
```

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
| `.resize(w, f)` | method | New NumBV, different format |
| `.copy()` | method | Independent copy |
| `.report()` | method | Print debug summary |
| `+ - * /` | operators | Arithmetic (→ new NumBV) |
| `+= -= *= /=` | in-place | Modify self |
| `<< >>` | shifts | Bit-level shift |
| `== < > <= >=` | compare | Value comparison |
| `int() float()` | builtins | Type conversion |
| `[h:l]` | slice | Read bits (inclusive) |
