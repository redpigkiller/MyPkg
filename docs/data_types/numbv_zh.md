# NumBV — 定點數運算

[![English](https://img.shields.io/badge/Language-English-blue.svg)](numbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](numbv_zh.md)

像 numpy 之於矩陣，NumBV 是定點數的運算引擎。  
定義好格式後，所有運算**自動保持格式**，不需要手動處理溢位。

## 快速開始

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
x *= 2                           # x 被修改，為同一個物件
```

---

## 功能特色

### 1. 宣告

```python
a = NumBV(16, 8)                               # Q8.8 有號 (signed)
b = NumBV(8, 4, signed=False, value=3.5)       # Q4.4 無號 (unsigned)
c = NumBV(16, 8, overflow='wrap', rounding='around')
```

### 2. 算術運算 — Auto-Limit

所有運算結果**自動回到左運算元的格式**，並依 `overflow` 設定處理溢位。

```python
# 產生新物件（a 保持不變）
b = a + 0.5
b = a * 1.5
b = 5.0 - a
b = -a
b = abs(a)
b = a << 4            # 位元左移
b = a >> 2            # 位元右移 (若為 signed 則採算術移位)
```

### 3. 就地運算 (In-Place)

```python
# 修改自身數值（同一物件）
a += 0.5
a -= 1
a *= 2
a /= 4
a <<= 1
a >>= 2
```

### 4. 自動飽和 (Auto-Saturation)

```python
x = NumBV(8, 0, signed=True, value=120)
y = x + 10            # → 127 (saturate，為預設行為)

z = NumBV(8, 0, signed=True, overflow='wrap', value=120)
w = z + 10            # → -126 (wrap 溢位反轉)
```

### 5. 位元存取 (Bit Access)

```python
a.bits                  # → int (無號整數的原始數值)
a.hex                   # → "0x00C0"
a.bin                   # → "0b0000000011000000"
a[15:8]                 # → 0x00 (取得高 8 位元)
a.bits & 0xFF           # → Python 內建整數位元運算
```

### 6. 比較與轉換

```python
a > 1.0                 # 回傳 True 或 False
a == b                  # 比較兩個 NumBV 的數值
int(a)                  # → bits (原始整數值)
float(a)                # → val (浮點數值)
bool(a)                 # → 若 val 且 bits 不為 0 回傳 True
round(a)                # → 四捨五入後的浮點數
len(a)                  # → 回傳位元寬度 width
```

### 7. 格式轉換

```python
b = a.copy()                     # 獨立深拷貝
y = a.cast(32, 16)               # 轉換格式: Q8.8 → Q16.16
z = a.cast(8, 4)                 # 轉換格式: Q8.8 → Q4.4

f"{a:hex}"                       # → "0x00C0"
f"{a:bin}"                       # → "0b00..."
f"{a:.2f}"                       # → "0.75"
```

### 8. 除錯報告

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

### 9. 陣列處理 (`NumBVArray`)

為了效能與便利性考量，請使用 `NumBVArray`（基於 `fxpmath` 的向量化運算），強烈建議不要混用 List of `NumBV`。

```python
from mypkg import NumBVArray

# 建立陣列
arr = NumBVArray(16, 8, values=[1.0, 2.0, 3.0])

# 向量運算 (相對於原始 fxpmath 約有 1.3x 封裝消耗 overhead)
result = arr * 2               # → [2.0, 4.0, 6.0] (回傳 NumBVArray)

# 索引與切片存取 Indexing & Slicing (遵循 List / NumPy 慣用習慣)
val = arr[0]                   # → Scalar NumBV (範例: 取出 2.0 這個元素)
sub = arr[0:2]                 # → Sub-NumBVArray (範例: 切出一串元素 [2.0, 4.0])

# 位元切片 Bit Slicing (支援串聯操作 Chaining)
# 當 arr[0] 回傳 scalar 時，可以直接接著進行後續位元切片
bits = arr[0][15:8]            # → int (取得第 0 個元素的高位元 byte)
```

> **設計小提醒**: `NumBVArray` 的 `[]` 陣列索引操作是以 **元素 (Element)** 為基準單位（延續 Python list / numpy 的習慣），而 `NumBV` (純量 Scalar) 的 `[]` 位元切片操作則是以 **位元 (Bit)** 為基準（依循 SystemVerilog 傳統的從上到下位階取值慣例）。
> 
> - `arr[i]` → 取第 i 個陣列元素
> - `reg[h:l]` → 擷取該變數的位元第 h 到第 l

---

## API 參考

```python
NumBV(width, frac, signed=True, value=0, overflow='saturate', rounding='trunc')
```

| 成員 | 型別 | 說明 |
|:-------|:-----|:------------|
| `.val` | `float` | 當前的實際數值 (經過量化處理後) |
| `.bits` | `int` | 原始無號整數的位元呈現形式 |
| `.hex` / `.bin` | `str` | 以十六進位、二進位表示的格式化字串 |
| `.from_val(x)` | method | 透過實際浮點數值設定 |
| `.from_bits(x)` | method | 透過整數原始位元設定 |
| `.cast(w, f)` | method | 回傳為最新格式的 NumBV 物件 |
| `.copy()` | method | 深拷貝出一個完全獨立的新物件 |
| `.report()` | method | 印出摘要供系統除錯確認資訊 |
| `+ - * /` | operators | 一般四則運算 (結果回傳 **新** 的 NumBV) |
| `+= -= *= /=` | in-place | 就地重新宣告並調整本身的數值 |
| `<< >>` | shifts | 對該物件進行位階元位移 |
| `== < > <= >=` | compare | 以實際「數值」進行邏輯比較 |
| `int() float()` | builtins | 資料強制轉換型別 |
| `[h:l]` | slice | 讀取區間內的指定位元數 (為閉區間 inclusive) |
