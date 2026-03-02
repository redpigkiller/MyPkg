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
pip install -e .[excel]   # adds openpyxl, xlrd, rapidfuzz dependencies
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
    Row(pattern=["Dept", "Name", "Salary"], node_id="header"),  # exact header match
    Row(pattern=[Types.STR, Types.STR, Types.INT],
        repeat="+", node_id="data"),                    # one or more data rows
    block_id="salary_table",
)
```

### Step 3 — Run

```python
# match_template returns list[list[list[BlockMatch]]]
#   dim 0: per sheet scanned
#   dim 1: per template provided
#   dim 2: per match found
results = match_template("report.xlsx", template)

for block_match in results[0][0]:       # first sheet, first template
    print(block_match.start, block_match.end)
    for row in block_match.rows:
        print(row.node_id, row.row, [c.value for c in row.cells])
```

---

## Cell Type Constants (`Types`)

| Constant | Matches |
|----------|---------|
| `Types.STR` | Any non-empty string |
| `Types.INT` | Integer (e.g. `42`, `-7`) |
| `Types.POS_INT` | Positive integer |
| `Types.NEG_INT` | Negative integer |
| `Types.FLOAT` | Float or integer (e.g. `3.14`, `42`) |
| `Types.SCIENTIFIC` | Scientific notation (e.g. `1.5e3`) |
| `Types.PERCENT` | Percentage string (e.g. `12.5%`) |
| `Types.HEX` | Hexadecimal (e.g. `0xFF`) |
| `Types.BIN` | Binary (e.g. `0b1010`) |
| `Types.OCT` | Octal (e.g. `0o17`) |
| `Types.DATE_ISO` | ISO date `YYYY-MM-DD` |
| `Types.DATE_SLASH` | Slash date `DD/MM/YYYY` |
| `Types.TIME_24H` | 24-hour time `HH:MM` |
| `Types.MERGED` | A cell expanded from a merge region |
| `Types.ANY` | Any value including empty (wildcard `.*`) |
| `Types.ANY(n)` | `n` consecutive `Types.ANY` (syntactic sugar) |
| `Types.EMPTY` | Normalised empty cell (`""`) |
| `Types.SPACE` | Empty string or whitespace-only |
| `Types.BLANK` | Same as `SPACE` — any "visually blank" cell |
| `Types.r("regex")` | Custom regex pattern |

**Combining types with `|`:**

```python
Types.STR | Types.INT   # matches either a string or an integer
```

> [!TIP]
> Use `Types.BLANK` when you don't care whether a cell is truly absent
> or just contains whitespace.

---

## Template Building Blocks

### `Block` — The top-level unit

```python
Block(*children, block_id="my_block", orientation="vertical")
```

- `orientation="vertical"` (default) — children are `Row` / `EmptyRow` / `Group`

### `Row` — Pattern node

```python
Row(pattern, repeat=1, node_id=None, normalize=True, min_similarity=None, match_ratio=None)
```

- `normalize=True` — strips and lowercases string cells before matching.
- `min_similarity=0.8` (requires `rapidfuzz`) — matches literal string patterns with a similarity ratio >= 0.8.
- `match_ratio=0.9` — the row matches if >= 90 % of cells pass, even if some fail.

`pattern` is a list where each element is:
- A **plain string** → matched literally (e.g. `"Dept"`)
- A **`Types` constant** → matched by type (e.g. `Types.INT`)
- A **`Types.r(regex)`** → matched by custom regex

### `EmptyRow`

```python
EmptyRow(repeat=1, allow_whitespace=True, node_id=None)
```

Matches rows where every cell is empty. `allow_whitespace=True` (default) also accepts cells containing only whitespace.

### `Group` — Repeat a block of nodes together

```python
Group(children=[Row(...), EmptyRow(...)], repeat="+")
```

Groups multiple nodes and repeats them as a unit. Useful for tables with repeating section separators.

### `AltNode` — Alternatives (OR semantics)

```python
Row(pattern=["Header A", Types.INT]) | Row(pattern=["Header B", Types.INT])
```

Created via the `|` operator. Matches whichever alternative fits.

### `repeat` spec

| Value | Meaning |
|-------|---------|
| `1` (default) | Exactly once |
| `"?"` | 0 or 1 times |
| `"+"` | 1 or more (greedy) |
| `"*"` | 0 or more (greedy) |
| `(2, 5)` | Between 2 and 5 times |
| `(3, None)` | 3 or more |

---

## Working with Results

### Return type

`match_template()` returns `list[list[list[BlockMatch]]]`:

| Dimension | Meaning |
|-----------|---------|
| `[i]` | i-th scanned sheet |
| `[i][j]` | j-th template's matches on sheet i |
| `[i][j][k]` | k-th block match |

### `BlockMatch`

```python
block_match.start       # (row, col) — 0-based top-left corner
block_match.end         # (row, col) — 0-based bottom-right corner
block_match.rows        # list[RowMatch] — all matched rows
block_match.block_id    # the block_id you gave the Block
```

### `RowMatch`

```python
row.row                 # absolute 0-based row index in the sheet
row.cells               # list[CellMatch]
row.node_id             # the node_id you gave the Row
```

### `CellMatch`

```python
cell.row                # absolute 0-based row index
cell.col                # absolute 0-based col index
cell.value              # normalised string value
cell.is_merged          # True if this cell was expanded from a merge range
```

---

## `MatchOptions` Reference

```python
MatchOptions(
    return_mode = 0,    # 0 = scan all sheets; N > 0 = stop after N sheets with matches
)
```

| `return_mode` | Behaviour |
|---------------|-----------|
| `0` | Scan all sheets |
| `N` (positive int) | Stop scanning after `N` sheets that contain at least one match |

---

## Advanced: Multi-Sheet Scanning

```python
# Scan all sheets (default)
results = match_template("report.xlsx", template, sheet=None)

# Scan specific sheets by name or 0-based index
results = match_template("report.xlsx", template, sheet=["Sheet1", "Sheet3"])
results = match_template("report.xlsx", template, sheet=0)
```

---

## Advanced: Multi-Template Composition

Pass a list of Block templates to scan for multiple patterns in one pass:

```python
header_block = Block(Row(pattern=["Report", "Date"]), block_id="header")
data_block   = Block(
    Row(pattern=["Dept", "Name", "Salary"]),
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+"),
    block_id="data_table",
)

results = match_template("report.xlsx", [header_block, data_block])
# results[sheet_idx][0] → header_block matches
# results[sheet_idx][1] → data_block matches
```

---

## Complete Example

```python
from mypkg.excel_extractor import (
    match_template, Block, Row, EmptyRow, Group, Types, MatchOptions,
)

# Template: grouped payroll table with merged dept cells, groups separated by blank rows
template = Block(
    Row(pattern=["Dept", "Name", "Salary"], node_id="header", min_similarity=0.85),
    Group(children=[
        Row(pattern=[Types.MERGED, Types.STR, Types.INT], repeat="+", node_id="data"),
        EmptyRow(repeat="?"),
    ], repeat="+"),
    block_id="payroll",
)

results = match_template(
    "payroll.xlsx",
    template,
    sheet=None,                          # scan all sheets
)

for sheet_results in results:
    for block_match in sheet_results[0]:   # first (only) template
        print(f"Found at {block_match.start} → {block_match.end}")
        for row in block_match.rows:
            if row.node_id == "data":
                dept, name, salary = [c.value for c in row.cells]
                print(f"  Row {row.row}: {dept} | {name} | {salary}")
```
