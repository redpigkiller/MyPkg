# MyPkg — Data Types for IC Design & Verification

A lightweight Python toolkit for IC design & verification engineers.

---

## Installation

```python
# Add MyPkg to your Python path, then:
from mypkg import MapBV, NumBV
```

Dependencies:
```bash
pip install fxpmath    # NumBV only
```

---

## Data Types

### [MapBV](docs/data_types/mapbv.md) — Register & Bit Mapping

位元映射、暫存器結構、雙向同步、邏輯運算、符號化求值。

```python
reg = MapBV("REG0", 16, tags={"type": "RW", "addr": 0x100})
sram = MapBV("SRAM", 8)
sram.link(reg[3:0], padding, field[1:0])

sram.value = 0xFF       # 寫 SRAM → regs 自動更新
sram.eval({"REG0": 0xA}) # 模擬不改值
```

### [NumBV](docs/data_types/numbv.md) — Fixed-Point Arithmetic

定點數運算、自動飽和、Q-format、auto-limit。

```python
a = NumBV(16, 8, value=0.75)   # Q8.8
b = a * 1.5                    # → NumBV(val=1.125, width=16)
a += 0.5                       # in-place

x = NumBV(8, 0, signed=True, value=120)
y = x + 10                     # → val=127 (auto-saturate!)
```

**New: `NumBVArray` — Vectorized Arithmetic**

高效處理定點數陣列 (wrapped `fxpmath`)。

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

專為腳本型多階段流程設計的 Tracker，支援依階段分類、錯誤累積與總結報告。

```python
from mypkg.utils.stage_tracker import StageTracker

tracker = StageTracker() # 全域唯一 Thread-Safe Tracker
tracker.set_stage("Init") # Flat Mode (依序階段)
tracker.info("Starting up...")

with tracker.stage("Process"): # Context Mode (自動資源管理)
    tracker.add_artifact({"key": "value"}) # 紀錄任意物件
    tracker.error("Missing input file")    # Accumulates error without crashing

tracker.summary()                    # Auto-prints failure report
```

---

## Scheduler

### [Scheduler](docs/scheduler/scheduler.md) — Job Scheduling

跨平台任務排程器，支援 priority / dependency / 即時 stdout streaming。

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
