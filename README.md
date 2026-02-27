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
from mypkg import MapBV
reg = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})
sram = MapBV("SRAM", 8)
padding = MapBV(0, 2)
field = MapBV("FIELD", 4)

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
tracker.set_stage("Init") # Flat Mode (Sequential stages)
tracker.info("Starting up...")

with tracker.stage("Process"): # Context Mode (Auto resource management)
    tracker.add_artifact({"key": "value"}) # Record any object
    tracker.error("Missing input file")    # Accumulates error without crashing

tracker.summary()                    # Auto-prints failure report
```

---

## Excel Automation

### [Excel Extractor](docs/excel_extractor/excel_extractor.md) — Template-Based Data Extraction

Describe the *shape* of your data; the engine finds it wherever it lives on the sheet. Supports merged cells, repeating rows, multi-template composition, and near-miss debugging.

```python
from mypkg.excel_extractor import match_template, Block, Row, Types

template = Block(
    Row(["部門", "姓名", "月薪"]),
    Row([Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
    block_id="salary_table",
)

output = match_template("report.xlsx", template)
result = output.results[0]

# Exact sheet coordinates for every matched row
for node in result.data_nodes():
    print(node.grid_row, node.cells)  # → absolute row + [dept, name, salary]

# Find a specific row by id and write back later
third = result.find_node("data", repeat_index=2)
print(third.grid_row, third.grid_col)  # → exact (row, col) in the sheet
```

### Dynamic Column Extraction (`RecordBlock`)

For sparse matching of many columns (e.g. searching headers by name in any order):

```python
from pydantic import BaseModel
from mypkg.excel_extractor import match_template, RecordBlock, Field, Types

class Employee(BaseModel):
    name: str
    salary: int

template = RecordBlock(
    Field(header="姓名", pattern=Types.STR, name="name"),
    Field(header="月薪", pattern=Types.INT, name="salary"),
)

output = match_template("report.xls", template)
employees = output.results[0].to_models(Employee)
print(employees[0].name, employees[0].salary)
```

---

## Scheduler

### [Scheduler](docs/scheduler/scheduler.md) — Job Scheduling

Cross-platform task scheduler supporting priority, dependencies, and real-time stdout streaming.

```python
sched = Scheduler(resources={"local": 4}, log_dir="./logs")

compile_job = CmdJob("compile", cmd="vlogan -sverilog top.sv")
sim_job = CmdJob("sim_01", cmd="vcs -R +tc=01",
                 depends_on=[compile_job], priority=10)

sched.submit(compile_job, sim_job)
sched.run()
sched.summary()
```

---

## License

MIT
