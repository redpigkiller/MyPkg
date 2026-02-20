# Scheduler — 跨平台任務排程器

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

輕量級任務排程器，基於 threading，適用於 IO-bound 工作負載（VCS 模擬、batch 指令等）。

---

## 快速開始

```python
from mypkg import Scheduler, CmdJob, GridJob

sched = Scheduler(resources={"local": 4, "grid": 8}, log_dir="./logs")

compile_job = CmdJob("compile", cmd="vlogan -sverilog top.sv", cwd="/proj/rtl")
sim_job = CmdJob("sim_01", cmd="vcs -R +tc=01", cwd="/proj/sim",
                 depends_on=[compile_job], priority=10, timeout=600)

sched.submit(compile_job, sim_job)
sched.run()          # blocking — 跑完才 return
sched.summary()      # 印出狀態表
```

---

## Scheduler

```python
Scheduler(
    resources={"local": 4, "grid": 8},
    log_dir="./logs",
    poll_interval=0.5,
)
```

### 基本控制

| Method | Description |
|--------|-------------|
| `submit(*jobs)` | 加入 job 到佇列 |
| `run()` | **Blocking** — 全部跑完才回來 |
| `start()` | **Non-blocking** — 背景 thread 執行 |
| `wait()` | 等待 `start()` 完成 |
| `stop()` | 停止排程 (完成進行中的 job) |

### 互動控制

| Method | Description |
|--------|-------------|
| `get(name)` | 取得 Job 物件 |
| `follow(name, n=20)` | 附加到 job 的 output stream |
| `cancel(name)` | 取消 pending job |
| `kill(name)` | 終止 running job |
| `set_priority(name, n)` | 改變 pending job 的 priority |
| `actions(name)` | 列出 job 特有操作 |
| `action(name, act)` | 執行 job 的特定操作 |

### 報告 & 篩選

| Method / Property | Description |
|-------------------|-------------|
| `status()` | 輕量狀態表 |
| `summary()` | 完整狀態表 (含 exit code, duration) |
| `jobs` | 所有 jobs |
| `pending` | 未開始的 jobs |
| `running` | 執行中的 jobs |
| `done` | 完成的 jobs |
| `failed` | 失敗 + 取消的 jobs |

---

## CmdJob — 本機指令

```python
CmdJob(
    name="sim_01",
    cmd="python run.py --tc 01",
    cwd="/proj/sim",                        # 工作目錄 (可選)
    env={"SEED": "42"},                     # 額外環境變數 (可選)
    priority=10,                            # 數字越大越優先 (預設 0)
    depends_on=[compile_job],               # 依賴 (可選)
    resources={"local": 1},                 # 預設 local=1
    timeout=600,                            # 超時自動 kill (秒, 可選)
)
```

### Output Streaming

```python
job.on_output(print)          # 即時 callback
job.remove_output(cb)         # 移除 callback
job.tail(20)                  # 最近 20 行
job.output_lines              # 完整歷史
```

### CmdJob Actions

執行完畢後自動提供：
- `open_log` — 跨平台開啟 log 檔案
- `open_cwd` — 跨平台開啟工作目錄

```python
sched.actions("sim_01")              # 列出
sched.action("sim_01", "open_log")   # 執行
```

---

## GridJob — SGE Grid (qsub/qstat/qdel)

```python
GridJob(
    name="grid_sim",
    cmd="vcs -R +tc=01",
    cwd="/proj/sim",
    submit_opts="-q regression -pe smp 2",  # qsub 選項
    priority=5,
    depends_on=[compile_job],
    resources={"grid": 1},                  # 預設 grid=1
    poll_interval=10.0,                     # qstat 查詢間隔 (秒)
)
```

### 工作流程

1. 自動產生 shell script → `qsub` 提交
2. 週期性 `qstat -j <id>` 查狀態
3. 串流 log 檔案內容作為 output
4. 完成後清理 temp script

### GridJob 覆寫

| 方法 | 用途 |
|------|------|
| `kill()` | `qdel <grid_id>` |
| `actions()` | `grid_status` — 查詢 qstat |
| `_parse_grid_id(output)` | 解析 qsub 回傳的 job ID |
| `_check_grid_status()` | 解析 qstat 狀態 |

### 自訂 Grid 系統

繼承 `GridJob` 並覆寫：

```python
class SlurmJob(GridJob):
    default_resources = {"slurm": 1}

    def __init__(self, name, cmd, **kwargs):
        super().__init__(name, cmd,
                         submit_cmd="sbatch",
                         kill_cmd="scancel",
                         status_cmd="squeue",
                         **kwargs)

    def _parse_grid_id(self, output):
        # Slurm format: "Submitted batch job 12345"
        m = re.search(r"Submitted batch job (\d+)", output)
        return m.group(1) if m else None
```

---

## 互動範例

```python
sched = Scheduler(resources={"local": 4, "grid": 8}, log_dir="./logs")
# ... submit jobs ...
sched.start()

sched.status()                     # 看目前狀態
sched.follow("sim_01")             # 即時看 output
sched.cancel("sim_03")             # 取消 pending
sched.kill("sim_02")               # kill running
sched.set_priority("sim_04", 100)  # 提高優先度
sched.actions("sim_01")            # 查看可用操作

sched.wait()
sched.summary()

# 篩選
for j in sched.failed:
    print(f"FAIL: {j.name}  exit={j.exit_code}")
```

---

## GUI Integration

```python
import queue

gui_queue = queue.Queue()
job.on_output(lambda line: gui_queue.put(("output", job.name, line)))

sched.start()   # non-blocking

# In GUI main loop:
while not gui_queue.empty():
    msg_type, name, data = gui_queue.get()
    text_widget.insert(END, data + "\n")
```
