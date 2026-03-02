# MyPkg — IC 設計與驗證資料型態工具包

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](README_zh.md)

專為 IC 設計與驗證工程師打造的輕量級 Python 工具包。

---

## 安裝

### 快速安裝與測試
```bash
python -m venv venv
# Windows: venv\Scripts\activate | Mac/Linux: source venv/bin/activate
pip install -e .          # 僅核心功能
pip install -e .[all]     # 完整功能 (包含所有選購相依性)
pip install -e .[math]    # 僅定點數功能 (包含 NumBV 與 NumBVArray)
pip install -e .[excel]   # 僅 Excel Extractor（加入 openpyxl）
pytest -q                 # 執行測試
```

> [!TIP]
> **IDE 與開發建議**：
> 如果您的 IDE（如 VS Code/Pylance）無法正確顯示 Type Hint 或出現 `__editable__` 虛擬模組：
> 1. 確保已安裝 `pip install -e .`
> 2. 如果問題持續，請嘗試相容模式：`pip install -e . --config-settings editable_mode=compat`
```

---

## 資料型態

### [MapBV](docs/data_types/mapbv_zh.md) — 暫存器與位元映射

位元映射、暫存器結構、雙向同步、邏輯運算、符號化求值。

```python
import mypkg.data_types.mapbv as mbv
from mypkg import MapBV

reg = mbv.var("REG0", 16, tags={"type": "RW", "addr": 0x100})
sram = mbv.var("SRAM", 8)
padding = mbv.const(0, 2)
field = mbv.var("FIELD", 4)

sram.link(reg[3:0], padding, field[1:0])

sram.value = 0xFF       # 寫 SRAM → regs 自動更新
print(f"REG0 lower 4 bits: {reg.value:X}") # -> F

# 模擬不改原本的值
sim_val = sram.eval({"REG0": 0xA, "FIELD": 0x1}) 
print(f"Simulated SRAM: {sim_val:X}") # -> A1
```

### [NumBV](docs/data_types/numbv_zh.md) — 定點數運算

定點數運算、自動飽和、Q-format、auto-limit。

```python
a = NumBV(16, 8, value=0.75)   # Q8.8
b = a * 1.5                    # → NumBV(val=1.125, width=16)
a += 0.5                       # in-place

x = NumBV(8, 0, signed=True, value=120)
y = x + 10                     # → val=127 (自動飽和!)
```

**新增: `NumBVArray` — 向量化運算**

高效處理定點數陣列 (基於 `fxpmath` 封裝)。

```python
# 建立 Q8.8 陣列
arr = NumBVArray(16, 8, values=[1.0, 2.0, 3.0])

# 向量化與自動飽和
result = arr * 2               # → [2.0, 4.0, 6.0]

# 轉換為 NumBV (純量)
val = arr[0]                   # → NumBV(val=2.0)
lst = arr.to_numbv_list()      # → list[NumBV]
```

---

## 實用工具

### [StageTracker](docs/utils/stage_tracker_zh.md) — 多階段流程紀錄

專為腳本型多階段流程設計的 Tracker，支援依階段分類、錯誤累積與總結報告。

```python
from mypkg.utils.stage_tracker import StageTracker

tracker = StageTracker() # 全域唯一 Thread-Safe Tracker
tracker.set_stage("Init") # Flat Mode (依序階段)
tracker.info("Starting up...")

with tracker.stage("Process"): # Context Mode (自動資源管理)
    tracker.add_artifact({"key": "value"}) # 紀錄任意物件
    tracker.error("Missing input file")    # 累積錯誤而不中斷程式

tracker.summary()                    # 自動印出失敗報告
```

---

## Excel 自動化

### [Excel Extractor](docs/excel_extractor/excel_extractor_zh.md) — 以樣板為基礎的資料擷取

描述資料的「形狀」，引擎自動找出它在工作表上的位置。支援 `.xlsx`/`.xlsm` (openpyxl) 與 `.xls` (xlrd)、合併儲存格、重複列、模糊標題比對及多樣板組合。

```python
from mypkg.excel_extractor import match_template, Block, Row, Types

template = Block(
    Row(pattern=["部門", "姓名", "月薪"], min_similarity=0.85),
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
    block_id="salary_table",
)

# 回傳 list[list[list[BlockMatch]]] — [每張工作表][每個樣板][每個比對結果]
results = match_template("report.xlsx", template)

for block_match in results[0][0]:            # 第一張工作表、第一個樣板
    for row in block_match.rows:
        if row.node_id == "data":
            print(row.row, [c.value for c in row.cells])
```


## 任務排程器

### [Scheduler](docs/scheduler/scheduler_zh.md) — 任務排程

跨平台任務排程器，支援任務優先級 (priority)、相依性 (dependency) 及即時 stdout 串流 (streaming)。

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

## 授權條款

MIT
