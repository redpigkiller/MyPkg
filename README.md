# MyPkg — Data Types for IC Design & Verification

[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)
[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)

A lightweight Python toolkit for IC design & verification engineers.

---

## Installation

### Quick Install & Test
```bash
python -m venv venv
# Windows: venv\Scripts\activate | Mac/Linux: source venv/bin/activate
pip install -e .          # Core features only
pip install -e .[all]     # Full features (all optional dependencies)
pip install -e .[math]    # Math features (NumBV & NumBVArray)
pip install -e .[excel]   # Excel Extractor (adds openpyxl)
pytest -q                 # Run tests
```

---

## Data Types

### [MapBV](docs/data_types/mapbv.md) — Register & Bit Mapping

Bit mapping, register structures, bidirectional synchronization, logic operations, and symbolic evaluation.

```python
import mypkg.data_types.mapbv as mbv
from mypkg import MapBV

reg = mbv.var("REG0", 16)
sram = mbv.var("SRAM", 8)
padding = mbv.const(0, 2)
field = mbv.var("FIELD", 4)

sram.link(reg[3:0], padding, field[1:0])

sram.value = 0xFF       # Write to SRAM → regs automatically update
print(f"REG0 lower 4 bits: {reg.value:X}") # -> F

# Simulation without modifying the original value
sim_val = sram.eval({"REG0": 0xA, "FIELD": 0x1}) 
print(f"Simulated SRAM: {sim_val:X}") # -> A1
```

### [NumBV](docs/data_types/numbv.md) — Fixed-Point Arithmetic

Fixed-point arithmetic, auto-saturation, Q-format, and auto-limit.

```python
a = NumBV(16, 8, value=0.75)   # Q8.8
b = a * 1.5                    # → NumBV(val=1.125, width=16)
a += 0.5                       # in-place

x = NumBV(8, 0, signed=True, value=120)
y = x + 10                     # → val=127 (auto-saturate!)
```

**New: `NumBVArray` — Vectorized Arithmetic**

Efficient processing of fixed-point number arrays (wraps `fxpmath`).

```python
# Create Q8.8 array
arr = NumBVArray(16, 8, values=[1.0, 2.0, 3.0])

# Vectorized & Auto-Saturated
result = arr * 2               # → [2.0, 4.0, 6.0]

# Bridge to NumBV (scalar)
val = arr[0]                   # → NumBV(val=2.0)
lst = arr.to_numbv_list()      # → list[NumBV]
```

---

## Utilities

### [StageTracker](docs/utils/stage_tracker.md) — Multi-Stage Workflow Logging

A tracker designed for script-based multi-stage workflows, supporting stage classification, error accumulation, and summary reporting.

```python
from mypkg.utils.stage_tracker import StageTracker

tracker = StageTracker() # Globally unique thread-safe tracker
tracker.begin_stage("Init") # Flat Mode (Sequential stages)
tracker.info("Starting up...")

with tracker.stage("Process"): # Context Mode (Auto resource management)
    tracker.error("Missing input file")    # Accumulates error without crashing

tracker.summary()                    # Auto-prints failure report
```

### [Text-Diff](docs/utils/text_diff.md) — Plain Text Difference Reporter

A terminal-friendly text comparison utility supporting side-by-side or unified diffs with character-level precision and smart line wrapping.

```python
from mypkg.utils.text_diff import diff_lines

report = diff_lines(["old line"], ["new line"], diff_style="side_by_side")
print(report)
```

---

## Excel Automation

### [Excel Extractor](docs/excel_extractor/excel_extractor.md) — Template-Based Data Extraction

Describe the *shape* of your data; the engine finds it wherever it lives on the sheet. Supports `.xlsx`/`.xlsm` (openpyxl) and `.xls` (xlrd), merged cells, repeating rows, fuzzy header matching, and multi-template composition.

```python
from mypkg.excel_extractor import match_template, Block, Row, Types

template = Block(
    Row(pattern=["部門", "姓名", "月薪"], min_similarity=0.85),
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
    block_id="salary_table",
)

# Returns list[list[list[BlockMatch]]] — [per-sheet][per-template][per-match]
results = match_template("report.xlsx", template)

for block_match in results[0][0]:            # first sheet, first template
    for row in block_match.rows:
        if row.node_id == "data":
            print(row.row, [c.value for c in row.cells])
```


## Scheduler

### [Scheduler](docs/scheduler/scheduler.md) — Job Scheduling

A lightweight, cross-platform job scheduler supporting priority, resource constraints, and real-time output streaming.

```python
from mypkg.scheduler import JobManager, CmdJob

manager = JobManager(max_workers=4, resources={"local": 4})
manager.start()

compile_job = CmdJob("compile", cmd="vlogan -sverilog top.sv")
sim_job = CmdJob("sim_01", cmd="vcs -R +tc=01",
                 resources={"local": 1}, priority=10)

manager.add(compile_job)
manager.add(sim_job)

manager.wait()
```

---

## License

MIT
