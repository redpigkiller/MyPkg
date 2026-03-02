# Excel Extractor — 以樣板為基礎的 Excel 資料擷取工具

[![English](https://img.shields.io/badge/Language-English-blue.svg)](excel_extractor.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](excel_extractor_zh.md)

## 這是什麼？

讀取 Excel 的結構化資料是一件麻煩的事。一旦版面多了一行，`sheet.cell(row, col)` 就全壞了。

**Excel Extractor** 採用完全不同的方式：你描述你期望看到的資料「形狀」（樣板），引擎自動找出它在工作表上的位置。

---

## 五分鐘快速上手

### 步驟一 — 安裝

```bash
pip install -e .[excel]   # 加入 openpyxl、xlrd、rapidfuzz 依賴
```

### 步驟二 — 描述你的樣板

假設你的 Excel 長這樣：

| 部門 | 姓名  | 月薪  |
|------|-------|-------|
| IT   | Alice | 50000 |
| IT   | Bob   | 60000 |
| HR   | Carol | 55000 |

```python
from mypkg.excel_extractor import match_template, Block, Row, Types

template = Block(
    Row(pattern=["部門", "姓名", "月薪"], node_id="header"),      # 精確比對標題列
    Row(pattern=[Types.STR, Types.STR, Types.INT],
        repeat="+", node_id="data"),                      # 一或多列資料列
    block_id="salary_table",
)
```

### 步驟三 — 執行

```python
# match_template 回傳 list[list[list[BlockMatch]]]
#   維度 0: 每張掃描的工作表
#   維度 1: 每個提供的樣板
#   維度 2: 每個比對結果
results = match_template("report.xlsx", template)

for block_match in results[0][0]:       # 第一張工作表、第一個樣板
    print(block_match.start, block_match.end)
    for row in block_match.rows:
        print(row.node_id, row.row, [c.value for c in row.cells])
```

---

## 格子型別常數（`Types`）

| 常數 | 比對對象 |
|------|---------|
| `Types.STR` | 任何非空字串 |
| `Types.INT` | 整數（例如 `42`、`-7`） |
| `Types.POS_INT` | 正整數 |
| `Types.NEG_INT` | 負整數 |
| `Types.FLOAT` | 浮點數或整數（例如 `3.14`、`42`） |
| `Types.SCIENTIFIC` | 科學記號（例如 `1.5e3`） |
| `Types.PERCENT` | 百分比字串（例如 `12.5%`） |
| `Types.HEX` | 十六進位（例如 `0xFF`） |
| `Types.BIN` | 二進位（例如 `0b1010`） |
| `Types.OCT` | 八進位（例如 `0o17`） |
| `Types.DATE_ISO` | ISO 日期 `YYYY-MM-DD` |
| `Types.DATE_SLASH` | 斜線日期 `DD/MM/YYYY` |
| `Types.TIME_24H` | 24 小時制 `HH:MM` |
| `Types.MERGED` | 從合併儲存格擴展的格子 |
| `Types.ANY` | 任何值包含空值（萬用字元 `.*`） |
| `Types.ANY(n)` | 連續 `n` 個 `Types.ANY`（語法糖） |
| `Types.EMPTY` | 正規化後的空格子（`""`） |
| `Types.SPACE` | 空字串或純空白字元 |
| `Types.BLANK` | 同 `SPACE`，任何「看起來空白」的格子 |
| `Types.r("regex")` | 自訂正規表示式 |

**以 `|` 組合型別：**

```python
Types.STR | Types.INT   # 接受字串或整數
```

> [!TIP]
> 當你不在意格子是「沒有值」還是「有空白字元」時，直接用 `Types.BLANK`。

---

## 樣板元件

### `Block` — 最頂層的單元

```python
Block(*children, block_id="my_block", orientation="vertical")
```

- `orientation="vertical"`（預設）：子節點為 `Row` / `EmptyRow` / `Group`

### `Row` — 樣式節點

```python
Row(pattern, repeat=1, node_id=None, normalize=True, min_similarity=None, match_ratio=None)
```

- `normalize=True` — 比對前會將字串去頭尾空白並轉為小寫。
- `min_similarity=0.8`（需要 `rapidfuzz`）— 允許相似度 ≥ 0.8 的字串字面量比對通過。
- `match_ratio=0.9` — 該列只要 ≥ 90% 的格子通過即可算比對成功。

`pattern` 是一個列表，每個元素可以是：
- **純字串** → 精確比對（例如 `"部門"`）
- **`Types` 常數** → 以型別比對（例如 `Types.INT`）
- **`Types.r(regex)`** → 以自訂正規表示式比對

### `EmptyRow`

```python
EmptyRow(repeat=1, allow_whitespace=True, node_id=None)
```

比對所有格都為空的列。`allow_whitespace=True`（預設）會額外接受只含空白字元的格子。

### `Group` — 將多個節點組合為一個重複單元

```python
Group(Row(...), EmptyRow(...), repeat="+")
```

將多個節點組成一組並整體重複。適合有重複分組分隔的表格。

### `AltNode` — 替代方案（OR 語義）

```python
Row(pattern=["Header A", Types.INT]) | Row(pattern=["Header B", Types.INT])
```

透過 `|` 運算子建立，比對任一符合的替代方案。

### `repeat` 規格

| 值 | 意義 |
|----|------|
| `1`（預設） | 恰好一次 |
| `"?"` | 0 或 1 次 |
| `"+"` | 1 次以上（貪婪） |
| `"*"` | 0 次以上（貪婪） |
| `(2, 5)` | 2 到 5 次 |
| `(3, None)` | 3 次以上 |

---

## 處理結果

### 回傳型別

`match_template()` 回傳 `list[list[list[BlockMatch]]]`：

| 維度 | 意義 |
|------|------|
| `[i]` | 第 i 張被掃描的工作表 |
| `[i][j]` | 第 j 個樣板在第 i 張工作表上的比對結果 |
| `[i][j][k]` | 第 k 個 block 比對 |

### `BlockMatch`

```python
block_match.start       # (row, col) — 0-based 左上角座標
block_match.end         # (row, col) — 0-based 右下角座標
block_match.rows        # list[RowMatch] — 所有比對到的列
block_match.block_id    # 你給 Block 的 block_id
```

### `RowMatch`

```python
row.row                 # 在工作表中的絕對 0-based 行號
row.cells               # list[CellMatch]
row.node_id             # 你給 Row 的 node_id
```

### `CellMatch`

```python
cell.row                # 絕對 0-based 行號
cell.col                # 絕對 0-based 欄號
cell.value              # 正規化後的字串值
cell.is_merged          # 是否為合併儲存格擴展而來
```

---

## `MatchOptions` 參數參考

```python
MatchOptions(
    return_mode = 0,    # 0 = 掃描所有工作表；N > 0 = 在 N 張有結果的工作表後停止
)
```

| `return_mode` | 行為 |
|---------------|------|
| `0` | 掃描所有工作表 |
| `N`（正整數） | 在 `N` 張有比對結果的工作表後停止掃描 |

---

## 進階：掃描多個工作表

```python
# 掃描全部工作表（預設）
results = match_template("report.xlsx", template, sheet=None)

# 以名稱或 0-based 索引指定工作表
results = match_template("report.xlsx", template, sheet=["Sheet1", "Sheet3"])
results = match_template("report.xlsx", template, sheet=0)
```

---

## 進階：多樣板組合

傳入一個 Block 列表即可在一次掃描中搜尋多個模式：

```python
header_block = Block(Row(pattern=["Report", "Date"]), block_id="header")
data_block   = Block(
    Row(pattern=["部門", "姓名", "月薪"]),
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+"),
    block_id="data_table",
)

results = match_template("report.xlsx", [header_block, data_block])
# results[sheet_idx][0] → header_block 的比對結果
# results[sheet_idx][1] → data_block 的比對結果
```

---

## 完整範例

```python
from mypkg.excel_extractor import (
    match_template, Block, Row, EmptyRow, Group, Types, MatchOptions,
)

# 樣板：有合併儲存格的分組薪資表，組間以空白列分隔
template = Block(
    Row(pattern=["部門", "姓名", "月薪"], node_id="header", min_similarity=0.85),
    Group(children=[
        Row(pattern=[Types.MERGED, Types.STR, Types.INT], repeat="+", node_id="data"),
        EmptyRow(repeat="?"),
    ], repeat="+"),
    block_id="payroll",
)

results = match_template(
    "payroll.xlsx",
    template,
    sheet=None,                          # 掃描全部工作表
)

for sheet_results in results:
    for block_match in sheet_results[0]:   # 第一個（唯一的）樣板
        print(f"找到位置：{block_match.start} → {block_match.end}")
        for row in block_match.rows:
            if row.node_id == "data":
                dept, name, salary = [c.value for c in row.cells]
                print(f"  第 {row.row} 行: {dept} | {name} | {salary}")
```
