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
pip install -e .[excel]   # 加入 openpyxl 依賴
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
    Row(["部門", "姓名", "月薪"]),               # 精確比對標題列
    Row([Types.STR, Types.STR, Types.INT],
        repeat="+", node_id="data"),             # 一或多列資料列
    block_id="salary_table",
)
```

### 步驟三 — 執行

```python
output = match_template("report.xlsx", template)

for result in output.results:
    print(result.anchor)        # (row, col) 找到的位置
    df = result.to_dataframe(header_node="header")  # 以標題列欄名命名
    print(df)
```

---

## 格子型別常數（`Types`）

| 常數 | 比對對象 |
|------|---------|
| `Types.STR` | 任何非空字串 |
| `Types.INT` | 整數（例如 `42`、`-7`） |
| `Types.FLOAT` | 浮點數或整數（例如 `3.14`、`42`） |
| `Types.DATE` | ISO 日期字串 `YYYY-MM-DD` |
| `Types.TIME` | 時間字串 `HH:MM` |
| `Types.MERGED` | 從合併儲存格擴展的格子 |
| `Types.ANY` | 任何非空值（萬用字元） |
| `Types.ANY(n)` | 連續 `n` 個 `Types.ANY`（例如 `Types.ANY(3)`） |
| `Types.EMPTY` | 真正空白的格子（`None`） |
| `Types.SPACE` | 空字串或純空白字元 |
| `Types.BLANK` | **`EMPTY` 或 `SPACE`**（任何「看起來空白」的格子） |
| `Types.r("regex")` | 自訂正規表示式 |

**以 `\|` 組合型別：**

```python
Types.STR | Types.INT   # 接受字串或整數
```

> [!TIP]
> 當你不在意格子是「沒有值」還是「有空白字元」時，直接用 `Types.BLANK`，
> 不需要記住 `EMPTY` 和 `SPACE` 的差異。

---

## 樣板元件

### `Block` — 最頂層的單元

```python
Block(*children, block_id="my_block", orientation="vertical")
```

- `orientation="vertical"`（預設）：子節點為 `Row` / `EmptyRow` / `Group`
- `orientation="horizontal"`：子節點為 `Col` / `EmptyCol` / `Group`

### `Row` / `Col` — 樣式節點

```python
Row(pattern, repeat=1, node_id=None, normalize=True, fuzzy=None)
Col(pattern, repeat=1, node_id=None, normalize=True, fuzzy=None)
```

- `normalize=True` — 比對前會將字串去頭尾空白並轉為小寫。
- `fuzzy=0.8`（需要安裝 `rapidfuzz` 擴展 `[excel]`）— 允許相似度大於等於 0.8 的字串字面量比對通過。

`pattern` 是一個列表，每個元素可以是：
- **純字串** → 精確比對（例如 `"部門"`）
- **`Types` 常數** → 以型別比對（例如 `Types.INT`）
- **`Types.r(regex)`** → 以自訂正規表示式比對

### `EmptyRow` / `EmptyCol`

```python
EmptyRow(repeat=1, allow_whitespace=False, node_id=None)
```

比對所有格都為空的列/欄。`allow_whitespace=True` 會額外接受只含空白字元的格子。

### `Group` — 將多個節點組合為一個重複單元

```python
Group(Row(...), EmptyRow(...), repeat="+")
```

將多個節點組成一組並整體重複。適合有重複分組分隔的表格。

### `repeat` 規格

| 值 | 意義 |
|----|------|
| `1`（預設） | 恰好一次 |
| `"?"` | 0 或 1 次 |
| `"+"` | 1 次以上（貪婪） |
| `"*"` | 0 次以上（貪婪） |
| `(2, 5)` | 2 到 5 次 |
| `(3, None)` | 3 次以上 |

> [!WARNING]
> 所有 `repeat` 都是**貪婪且無回溯**的。若 `"+"` / `"*"` 節點後接另一個節點，
> 請確認兩者的 pattern **不重疊**，否則貪婪節點可能把後面節點需要的列/欄也吃掉。
>
> **安全範例：** 標題列用精確字串（`"部門"`），資料列用型別（`Types.STR`）→ 不重疊 ✓  
> **危險範例：** `Row([Types.STR], repeat="+")` 後接 `Row([Types.STR])` → 後面的節點可能永遠比對不到 ✗

---

## 處理結果

### `MatchOutput`

```python
output.results      # list[MatchResult] — 成功比對的結果
output.near_misses  # list[NearMissHint] — 近似比對（用於除錯）
```

### `MatchResult`

```python
result.block_id         # 你給 Block 的 block_id
result.sheet            # 找到比對結果的工作表名稱
result.anchor           # (row, col) — 0-based 左上角座標
result.bounding_box     # (r1, c1, r2, c2) — 含首尾的完整範圍
result.matched_nodes    # list[NodeResult] — 所有已比對節點
result.data_nodes()     # list[NodeResult] — 排除 EmptyRow / EmptyCol
result.to_dataframe()   # 轉換為 pandas DataFrame
result.to_dict()        # 轉換為純 dict（可 JSON 序列化）
```

### `NodeResult`

每個比對的 `Row` / `Col` / `EmptyRow` / `EmptyCol` 每次重複都產生一個 `NodeResult`：

```python
node.node_type      # "Row" | "Col" | "EmptyRow" | "EmptyCol"
node.node_id        # 你給 Row/Col 的 node_id
node.repeat_index   # 0-based 重複索引
node.cells          # 擷取出的格子值列表
node.grid_row       # 在工作表中的絕對 0-based 行號
node.grid_col       # 在工作表中的絕對 0-based 欄號
```

### `find_node` / `find_nodes` — 以 ID 定位節點

**取單一節點：**
```python
node = result.find_node("data", repeat_index=2)
# → 第 3 筆 "data" 列（0-based）

print(node.grid_row, node.grid_col)   # 精確的工作表座標
print(node.cells)                     # 擷取出的值
```

**取所有重複節點：**
```python
rows = result.find_nodes("data")   # list[NodeResult]，按 repeat_index 排序
for row in rows:
    dept, name, salary = row.cells
```

> `find_nodes` 特別適合 `repeat="+"` 或 `repeat="*"` 的節點，省去手動用 index 逐一取的麻煩。

### `to_dataframe` — 指定欄名

使用某個 `node_id` 的 `cells` 作為 DataFrame 的欄名（該節點本身不會出現在資料列中）：

```python
# 標題列 node_id="header"，資料列 node_id="data"
df = result.to_dataframe(header_node="header")
# → DataFrame 欄名 = 標題列的格子值
```

不指定時，欄名為 `0, 1, 2, ...`（整數索引）。

---

## `MatchOptions` 參數參考

```python
MatchOptions(
    return_mode             = "ALL",   # "ALL" | "FIRST"
    near_miss_threshold     = None,    # 0~1 的浮點數，或 None 停用
    search_range            = None,    # (r1, c1, r2, c2) 0-based，或 None
    consume_matched_regions = False,   # True = 啟用消耗遮罩
)
```

| `return_mode` | 行為 |
|---------------|------|
| `"ALL"` | 回傳所有比對結果 |
| `"FIRST"` | 找到第一個就停止掃描 |

### `search_range` 與 `excel_range()` helper

`search_range` 使用 0-based 座標，可用 `excel_range()` 從 Excel 格式換算：

```python
from mypkg.excel_extractor import MatchOptions, excel_range

opts = MatchOptions(search_range=excel_range("A1:F50"))
# 等同 MatchOptions(search_range=(0, 0, 49, 5))
```

---

## 進階：掃描多個工作表

```python
# 掃描全部工作表
output = match_template("report.xlsx", template, sheet=None)
# 或
output = match_template("report.xlsx", template, sheet="*")

# 掃描指定的多個工作表
output = match_template("report.xlsx", template, sheet=["Sheet1", "Sheet3"])

# 每個 MatchResult 都帶有 sheet 屬性
for result in output.results:
    print(result.sheet, result.anchor)
```

---

## 進階：多樣板工作表組合

當多個樣板描述同一張工作表時，使用 `consume_matched_regions=True` 可以防止小樣板比對到已被大 Block 佔用的區域。

```python
from mypkg.excel_extractor import MatchOptions

header_block = Block(Row(["Report", "Date"]), block_id="header")
data_block   = Block(
    Row(["部門", "姓名", "月薪"]),
    Row([Types.STR, Types.STR, Types.INT], repeat="+"),
    block_id="data_table",
)

opts = MatchOptions(consume_matched_regions=True)
output = match_template("report.xlsx", [header_block, data_block], options=opts)
```

樣板會自動依佔用面積排序（大的優先），確保大 Block 先佔住區域後，小樣板才開始掃描。

---

## 進階：近似比對除錯

```python
opts = MatchOptions(near_miss_threshold=0.5)
output = match_template("report.xlsx", template, options=opts)

for hint in output.near_misses:
    print(f"幾乎比對成功於 {hint.anchor}：{hint.matched_ratio:.0%} — 在 {hint.failed_at} 失敗")
    if hint.expected:
        print(f"  期望：{hint.expected}，實際看到：{hint.got}")
```

`NearMissHint` 的屬性：

| 屬性 | 說明 |
|------|------|
| `matched_ratio` | 有多少比例的頂層子節點成功（0.0–1.0） |
| `failed_at` | 第一個失敗點的人類可讀描述 |
| `expected` | 失敗格子期望的型別/pattern 描述 |
| `got` | 失敗格子實際的值 |

---

## 完整範例

```python
from mypkg.excel_extractor import (
    match_template, Block, Row, EmptyRow, Group, Types, MatchOptions, excel_range,
)

# 樣板：有合併儲存格的分組薪資表，組間以空白列分隔
template = Block(
    Row(["部門", "姓名", "月薪"], node_id="header"),
    Group(
        Row([Types.MERGED, Types.STR, Types.INT], repeat="+", node_id="data"),
        EmptyRow(repeat="?"),
        repeat="+",
    ),
    block_id="payroll",
)

output = match_template(
    "payroll.xlsx",
    template,
    sheet=None,                                       # 掃描全部工作表
    options=MatchOptions(
        search_range=excel_range("A1:F100"),          # 限定掃描範圍
        near_miss_threshold=0.5,                      # 啟用近似比對除錯
    ),
)

for result in output.results:
    print(f"在工作表 {result.sheet!r} 找到位置：{result.anchor}")

    # 以標題列值作為 DataFrame 欄名
    df = result.to_dataframe(header_node="header")
    print(df)

    # 取得所有資料列（不需要知道有幾筆）
    for node in result.find_nodes("data"):
        dept, name, salary = node.cells
        print(f"  第 {node.grid_row} 行: {dept} | {name} | {salary}")

# 除錯：查看近似比對
for hint in output.near_misses:
    print(f"幾乎比對於 {hint.anchor}（{hint.matched_ratio:.0%}）→ {hint.failed_at}")
    if hint.expected:
        print(f"  期望 {hint.expected}，但看到 {hint.got}")
```
