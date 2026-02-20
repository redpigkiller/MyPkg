# MapBV — 暫存器與位元映射

[![English](https://img.shields.io/badge/Language-English-blue.svg)](mapbv.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](mapbv_zh.md)

透過直覺的 Python 物件描述暫存器、SRAM 映射以及邏輯運算 — 完整支援**雙向數值同步**、**符號化求值**與**結構內省**。

## 快速開始

```python
from mypkg import MapBV

# 宣告暫存器
reg0 = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})
reg1 = MapBV("REG1", 16, tags={"type": "RO"})
padding = MapBV(0, 2)               # 2-bit 常數

# 定義 SRAM word: {REG0[3:0], padding, REG1[1:0]}
sram = MapBV("SRAM_00", 8)
sram.link(reg0[3:0], padding, reg1[1:0])

# 寫入 regs → 讀取 SRAM
reg0.value = 0x5;  reg1.value = 0x2
print(sram.to_hex())                 # → 0x52

# 寫入 SRAM → regs 自動更新
sram.value = 0xFF
print(reg0.to_hex(), reg1.to_hex())  # → 0x000F 0x0003
```

---

## 功能特色

### 1. 宣告

```python
# 帶有 metadata 的具名變數
reg = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})

# 常數 (不可變 — 若嘗試寫入會觸發警告)
padding = MapBV(0, 4)
```

### 2. 連結與雙向同步 (Linking & Bidirectional Sync)

將零碎的訊號連結成一個較大的字組 (word)。數值變更會自動在**雙向**傳遞。

```python
sram = MapBV("SRAM", 8)
sram.link(reg0[3:0], padding, reg1[1:0])   # 從 MSB 到 LSB 排列

# 讀取: 將子元件當前的數值連接起來
# 寫入: 將位元切割並推送（覆寫）到每個子元件
```

多個 SRAM 可以連結到**同一個暫存器的一部份位元** — 它們都會保持同步：

```python
sram_red   = MapBV.concat(reg0[7:0], reg1[7:0], name="RED")
sram_green = MapBV.concat(reg0[7:0], reg1[7:0], name="GREEN")
```

### 3. 切片 (Slicing) `[high:low]`

提供類似硬體語法的包含性 (inclusive) 位元範圍選擇：

```python
reg.value = 0xABCD
reg[7:0].value          # → 0xCD
reg[15:8].value         # → 0xAB
reg[7:4].value = 0xF    # 僅設定 bits 7~4
```

切片物件也可以當作**被連結的目標**：

```python
reg[7:0].link(field_a, field_b)  # 重新建構部分區域的內容
```

### 4. 邏輯與移位運算

```python
result = reg0 & reg1            # AND
result = reg0 | 0x00FF          # OR (與整數)
result = ~reg0                  # 逐元反相
result = reg0 << 4              # 左移
result = reg0[7:0] ^ reg1[7:0]  # 切片同樣支援運算

# 支援串聯式撰寫
expr = (reg0 & 0x0F) | (reg1 ^ MapBV(0xFF, 16))
print(expr.value)
```

### 5. 符號化求值 `.eval()`

進行 "What-if"（假設性）分析，**不會**改變真實數值：

```python
reg0.value = 0x5;  reg1.value = 0x2

# "如果 REG0 是 0xA 且 REG1 是 0x3，SRAM 會變怎樣？"
simulated = sram.eval({"REG0": 0xA, "REG1": 0x3})
print(hex(simulated))           # → 0xA3

# 實際數值保持原樣
print(sram.to_hex())            # → 0x52  (仍然跟原本一樣)
```

#### 基於 Tag 的 Eval

當不同情境下存在同名暫存器（例如 "red" vs. "green"），可以使用 `MapBV.key()` 來建立包含標籤資訊的上下文 key：

```python
reg0_red   = MapBV("REG0", 16, tags={"color": "red"})
reg0_green = MapBV("REG0", 16, tags={"color": "green"})

sram_red.eval({
    MapBV.key("REG0", {"color": "red"}): 0x1,
    MapBV.key("REG1", {"color": "red"}): 0x2,
})  # → 0x12
```

### 6. 結構內省 `.structure`

檢查已連結的 MapBV 是由哪些元件組成 — 非常適合用於**設計規則檢查 (DRC)**：

```python
for seg in sram.structure:
    print(f"{seg.bv.name} {seg.slice_range} tags={seg.bv.tags}")

# 輸出:
# REG0 (3, 0) tags={'type': 'RW', ...}
# CONST None   tags=None
# REG1 (1, 0) tags={'type': 'RO'}
```

### 7. 格式化輸出

```python
reg.to_hex()        # → "0x00FF"
reg.to_bin()        # → "0b0000000011111111"
f"{reg:hex}"        # → "0x00FF"   (透過 __format__)
f"{reg:bin}"        # → "0b0000000011111111"
```

### 8. 實用方法

```python
word = MapBV.concat(a, b, c, name="WORD")
backup = reg.copy("REG0_backup")
sram.unlink()
len(reg)            # → 16  (位元寬度)
int(reg)            # → 轉為整數值
reg == 0x42         # → True/False
```

---

## API 參考

| 類別 | 說明 |
|:------|:------------|
| `MapBV(name_or_value, width, tags=None)` | 主 BitVector 類別 — 具名變數或常數 |
| `MapBVSlice` | 來自 `bv[high:low]` 的存取代理，支援讀寫/連結/運算 |
| `MapBVExpr` | 邏輯/位移運算所產生的表達樹 |
| `StructSegment` | Frozen dataclass，包含 `.bv` 與 `.slice_range` |

### `MapBV` 屬性與方法

| 成員 | 型別 | 說明 |
|:-------|:-----|:------------|
| `.name` | `str` | MapBV 的名稱（常數為 `"CONST"`） |
| `.width` | `int` | 位元寬度 |
| `.value` | `int` | 當前數值（支援讀寫；若有連結則採雙向更新） |
| `.is_const` | `bool` | 是否為常數 |
| `.tags` | `dict\|None` | 使用者 metadata（常數時為 `None`） |
| `.link(*parts)` | method | 定義為輸入元件的拼接組合 (MSB→LSB) |
| `.unlink()` | method | 解除連結並紀錄當下快照數值 |
| `.eval(ctx)` | method | 使用上下文字典進行符號化求值 |
| `.structure` | `list[StructSegment]` | 已連結的元件組成列（未連結則為空） |
| `.copy(name)` | method | 深拷貝成獨立的 MapBV |
| `.snapshot(name)` | method | `.copy()` 的別名 |
| `.to_hex()` | method | 補零的十六進位字串 |
| `.to_bin()` | method | 補零的二進位字串 |
| `.concat(*parts)` | classmethod | 利用來源元件建立並連結出新的 MapBV |
