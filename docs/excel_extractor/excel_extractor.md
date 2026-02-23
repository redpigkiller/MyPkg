# Excel Extractor — Template-Based Excel Data Extraction

[![English](https://img.shields.io/badge/Language-English-blue.svg)](excel_extractor.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](excel_extractor_zh.md)

## What is Excel Extractor?

Reading structured data from Excel files is tedious. Every time the layout shifts one row, your `sheet.cell(row, col)` code breaks.

**Excel Extractor** takes a different approach: you describe the *shape* of the data you expect (a "template"), and the engine finds it for you — wherever it lives on the sheet.

---

## 5-Minute Quick Start

### Step 1 — Install

```bash
pip install -e .[excel]   # adds openpyxl dependency
```

### Step 2 — Describe your template

Suppose your Excel sheet looks like this:

| Dept | Name  | Salary |
|------|-------|--------|
| IT   | Alice | 50000  |
| IT   | Bob   | 60000  |
| HR   | Carol | 55000  |

```python
from mypkg.excel_extractor import match_template, Block, Row, Types

template = Block(
    Row(["Dept", "Name", "Salary"], node_id="header"),  # exact header match
    Row([Types.STR, Types.STR, Types.INT],
        repeat="+", node_id="data"),                    # one or more data rows
    block_id="salary_table",
)
```

### Step 3 — Run

```python
output = match_template("report.xlsx", template)

for result in output.results:
    print(result.anchor)                             # (row, col) where block was found
    df = result.to_dataframe(header_node="header")  # use header cells as column names
    print(df)
```

---

## Cell Type Constants (`Types`)

| Constant | Matches |
|----------|---------|
| `Types.STR` | Any non-empty string |
| `Types.INT` | Integer (e.g. `42`, `-7`) |
| `Types.FLOAT` | Float or integer (e.g. `3.14`, `42`) |
| `Types.DATE` | ISO date string `YYYY-MM-DD` |
| `Types.TIME` | Time string `HH:MM` |
| `Types.MERGED` | A cell expanded from a merge region |
| `Types.ANY` | Any non-empty value (wildcard) |
| `Types.EMPTY` | Truly empty cell (`None`) |
| `Types.SPACE` | Empty string or whitespace-only |
| `Types.BLANK` | **`EMPTY` or `SPACE`** — any "visually blank" cell |
| `Types.r("regex")` | Custom regex pattern |

**Combining types with `\|`:**

```python
Types.STR | Types.INT   # matches either a string or an integer
```

> [!TIP]
> Use `Types.BLANK` when you don't care whether a cell is truly absent (`None`)
> or just contains whitespace — no need to remember the difference between
> `EMPTY` and `SPACE`.

---

## Template Building Blocks

### `Block` — The top-level unit

```python
Block(*children, block_id="my_block", orientation="vertical")
```

- `orientation="vertical"` (default) — children are `Row` / `EmptyRow` / `Group`
- `orientation="horizontal"` — children are `Col` / `EmptyCol` / `Group`

### `Row` / `Col` — Pattern nodes

```python
Row(pattern, repeat=1, node_id=None)
Col(pattern, repeat=1, node_id=None)
```

`pattern` is a list where each element is:
- A **plain string** → matched literally (e.g. `"Dept"`)
- A **`Types` constant** → matched by type (e.g. `Types.INT`)
- A **`Types.r(regex)`** → matched by custom regex

### `EmptyRow` / `EmptyCol`

```python
EmptyRow(repeat=1, allow_whitespace=False, node_id=None)
```

Matches rows/cols where every cell is empty (`None`). Set `allow_whitespace=True` to also accept cells containing only whitespace.

### `Group` — Repeat a block of nodes together

```python
Group(Row(...), EmptyRow(...), repeat="+")
```

Groups multiple nodes and repeats them as a unit. Useful for tables with repeating section separators.

### `repeat` spec

| Value | Meaning |
|-------|---------|
| `1` (default) | Exactly once |
| `"?"` | 0 or 1 times |
| `"+"` | 1 or more (greedy) |
| `"*"` | 0 or more (greedy) |
| `(2, 5)` | Between 2 and 5 times |
| `(3, None)` | 3 or more |

> [!WARNING]
> All `repeat` specs are **greedy and non-backtracking**. If a `"+"` or `"*"`
> node is immediately followed by another node whose pattern overlaps, the
> greedy node may consume rows that the next node needs.
>
> **Safe:** header row uses a literal string (`"Dept"`), data rows use a type
> (`Types.STR`) — patterns don't overlap ✓  
> **Unsafe:** `Row([Types.STR], repeat="+")` followed by `Row([Types.STR])` —
> the trailing node may never match ✗

---

## Working with Results

### `MatchOutput`

```python
output.results      # list[MatchResult] — successful matches
output.near_misses  # list[NearMissHint] — partial matches (debug only)
```

### `MatchResult`

```python
result.block_id         # the block_id you gave the Block
result.sheet            # sheet name where the match was found
result.anchor           # (row, col) — 0-based top-left corner
result.bounding_box     # (r1, c1, r2, c2) — inclusive bounding box
result.matched_nodes    # list[NodeResult] — all matched nodes
result.data_nodes()     # list[NodeResult] — excludes EmptyRow / EmptyCol
result.to_dataframe()   # convert to pandas DataFrame
result.to_dict()        # convert to plain dict (JSON-serialisable)
```

### `NodeResult`

Each matched `Row` / `Col` / `EmptyRow` / `EmptyCol` produces one `NodeResult` per repetition:

```python
node.node_type      # "Row" | "Col" | "EmptyRow" | "EmptyCol"
node.node_id        # the node_id you gave the Row/Col
node.repeat_index   # 0-based repetition index
node.cells          # list of extracted cell values
node.grid_row       # absolute 0-based row in the sheet
node.grid_col       # absolute 0-based column in the sheet
```

### `find_node` / `find_nodes` — Locate nodes by ID

**Single node:**
```python
node = result.find_node("data", repeat_index=2)
# → the 3rd repetition of "data" (0-based)

print(node.grid_row, node.grid_col)   # exact sheet coordinates
print(node.cells)                     # extracted values
```

**All repetitions at once:**
```python
rows = result.find_nodes("data")   # list[NodeResult], sorted by repeat_index
for row in rows:
    dept, name, salary = row.cells
```

> `find_nodes` is the natural choice for `repeat="+"` or `repeat="*"` nodes —
> no need to know the count in advance.

### `to_dataframe` — Named columns

Pass a `node_id` to use that node's cell values as DataFrame column names
(that node is excluded from the data rows):

```python
# header row has node_id="header", data rows have node_id="data"
df = result.to_dataframe(header_node="header")
# → DataFrame columns = values from the header row
```

Without `header_node`, columns are integer indices `0, 1, 2, ...`.

---

## `MatchOptions` Reference

```python
MatchOptions(
    return_mode             = "ALL",   # "ALL" | "FIRST"
    near_miss_threshold     = None,    # float 0–1, or None to disable
    search_range            = None,    # (r1, c1, r2, c2) 0-based, or None
    consume_matched_regions = False,   # True = overlap prevention
)
```

| `return_mode` | Behaviour |
|---------------|-----------|
| `"ALL"` | Return every match found |
| `"FIRST"` | Return only the first match, then stop scanning |

### `search_range` and the `excel_range()` helper

`search_range` uses 0-based coordinates. Use `excel_range()` to convert from
familiar Excel notation:

```python
from mypkg.excel_extractor import MatchOptions, excel_range

opts = MatchOptions(search_range=excel_range("A1:F50"))
# equivalent to MatchOptions(search_range=(0, 0, 49, 5))
```

---

## Advanced: Multi-Sheet Scanning

```python
# Scan all sheets
output = match_template("report.xlsx", template, sheet=None)
# or equivalently
output = match_template("report.xlsx", template, sheet="*")

# Scan specific sheets
output = match_template("report.xlsx", template, sheet=["Sheet1", "Sheet3"])

# Each MatchResult carries the sheet name
for result in output.results:
    print(result.sheet, result.anchor)
```

---

## Advanced: Multi-Template Sheet Composition

When multiple templates describe the same sheet, use `consume_matched_regions=True`
to prevent smaller templates from matching inside regions already claimed by larger blocks.

```python
from mypkg.excel_extractor import MatchOptions

header_block = Block(Row(["Report", "Date"]), block_id="header")
data_block   = Block(
    Row(["Dept", "Name", "Salary"]),
    Row([Types.STR, Types.STR, Types.INT], repeat="+"),
    block_id="data_table",
)

opts = MatchOptions(consume_matched_regions=True)
output = match_template("report.xlsx", [header_block, data_block], options=opts)
```

Templates are automatically sorted by footprint (largest first) so the bigger
block claims its region before smaller templates scan.

---

## Advanced: Near-Miss Debugging

```python
opts = MatchOptions(near_miss_threshold=0.5)
output = match_template("report.xlsx", template, options=opts)

for hint in output.near_misses:
    print(f"Almost matched at {hint.anchor}: {hint.matched_ratio:.0%} — failed at {hint.failed_at}")
    if hint.expected:
        print(f"  Expected: {hint.expected}, got: {hint.got}")
```

`NearMissHint` attributes:

| Attribute | Description |
|-----------|-------------|
| `matched_ratio` | Fraction of top-level children that matched (0.0–1.0) |
| `failed_at` | Human-readable description of the first failure point |
| `expected` | Description of what the template expected at the failure |
| `got` | The actual cell value seen at the failure point |

---

## Complete Example

```python
from mypkg.excel_extractor import (
    match_template, Block, Row, EmptyRow, Group, Types, MatchOptions, excel_range,
)

# Template: grouped payroll table with merged dept cells, groups separated by blank rows
template = Block(
    Row(["Dept", "Name", "Salary"], node_id="header"),
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
    sheet=None,                                          # scan all sheets
    options=MatchOptions(
        search_range=excel_range("A1:F100"),             # limit scan area
        near_miss_threshold=0.5,                         # enable debug hints
    ),
)

for result in output.results:
    print(f"Found in sheet {result.sheet!r} at {result.anchor}")

    # DataFrame with proper column names from the header row
    df = result.to_dataframe(header_node="header")
    print(df)

    # Iterate every data row without knowing the count upfront
    for node in result.find_nodes("data"):
        dept, name, salary = node.cells
        print(f"  Row {node.grid_row}: {dept} | {name} | {salary}")

# Debug: inspect near misses
for hint in output.near_misses:
    print(f"Near miss at {hint.anchor} ({hint.matched_ratio:.0%}) → {hint.failed_at}")
    if hint.expected:
        print(f"  Expected {hint.expected}, got {hint.got}")
```
