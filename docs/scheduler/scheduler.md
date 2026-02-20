# Scheduler — Cross-Platform Job Scheduler

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

A lightweight job scheduler based on threading, optimized for IO-bound workloads (e.g., VCS simulations, batch commands).

---

## Quick Start

```python
from mypkg import Scheduler, CmdJob, GridJob

sched = Scheduler(resources={"local": 4, "grid": 8}, log_dir="./logs")

compile_job = CmdJob("compile", cmd="vlogan -sverilog top.sv", cwd="/proj/rtl")
sim_job = CmdJob("sim_01", cmd="vcs -R +tc=01", cwd="/proj/sim",
                 depends_on=[compile_job], priority=10, timeout=600)

sched.submit(compile_job, sim_job)
sched.run()          # blocking — returns only when finished
sched.summary()      # Prints the status table
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

### Basic Controls

| Method | Description |
|--------|-------------|
| `submit(*jobs)` | Add jobs to the queue |
| `run()` | **Blocking** — Wait until all jobs complete |
| `start()` | **Non-blocking** — Run in background thread |
| `wait()` | Wait for `start()` to complete |
| `stop()` | Stop scheduling (finishes ongoing jobs) |

### Interactive Controls

| Method | Description |
|--------|-------------|
| `get(name)` | Get a Job object |
| `follow(name, n=20)` | Attach to a job's output stream |
| `cancel(name)` | Cancel a pending job |
| `kill(name)` | Terminate a running job |
| `set_priority(name, n)` | Change the priority of a pending job |
| `actions(name)` | List job-specific actions |
| `action(name, act)` | Execute a job's specific action |

### Reporting & Filtering

| Method / Property | Description |
|-------------------|-------------|
| `status()` | Lightweight status table |
| `summary()` | Full status table (includes exit code, duration) |
| `jobs` | All jobs |
| `pending` | Unstarted jobs |
| `running` | Executing jobs |
| `done` | Completed jobs |
| `failed` | Failed + canceled jobs |

---

## CmdJob — Local Commands

```python
CmdJob(
    name="sim_01",
    cmd="python run.py --tc 01",
    cwd="/proj/sim",                        # Working directory (optional)
    env={"SEED": "42"},                     # Extra environment variables (optional)
    priority=10,                            # Higher is more prioritized (default 0)
    depends_on=[compile_job],               # Dependencies (optional)
    resources={"local": 1},                 # Default local=1
    timeout=600,                            # Auto-kill upon timeout in seconds (optional)
)
```

### Output Streaming

```python
job.on_output(print)          # Real-time callback
job.remove_output(cb)         # Remove callback
job.tail(20)                  # Last 20 lines
job.output_lines              # Full history
```

### CmdJob Actions

Automatically provided after completion:
- `open_log` — Cross-platform open log file
- `open_cwd` — Cross-platform open working directory

```python
sched.actions("sim_01")              # List actions
sched.action("sim_01", "open_log")   # Execute
```

---

## GridJob — SGE Grid (qsub/qstat/qdel)

```python
GridJob(
    name="grid_sim",
    cmd="vcs -R +tc=01",
    cwd="/proj/sim",
    submit_opts="-q regression -pe smp 2",  # qsub options
    priority=5,
    depends_on=[compile_job],
    resources={"grid": 1},                  # Default grid=1
    poll_interval=10.0,                     # qstat polling interval (seconds)
)
```

### Workflow

1. Auto-generates shell script → submits via `qsub`
2. Periodically checks status using `qstat -j <id>`
3. Streams log file contents as output
4. Cleans up temp scripts upon completion

### GridJob Overrides

| Method | Purpose |
|------|------|
| `kill()` | `qdel <grid_id>` |
| `actions()` | `grid_status` — queries qstat |
| `_parse_grid_id(output)` | Parses the job ID returned by qsub |
| `_check_grid_status()` | Parses qstat status |

### Custom Grid Systems

Inherit `GridJob` and override:

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

## Interactive Examples

```python
sched = Scheduler(resources={"local": 4, "grid": 8}, log_dir="./logs")
# ... submit jobs ...
sched.start()

sched.status()                     # View current status
sched.follow("sim_01")             # View output in real-time
sched.cancel("sim_03")             # Cancel pending
sched.kill("sim_02")               # Kill running
sched.set_priority("sim_04", 100)  # Increase priority
sched.actions("sim_01")            # View available actions

sched.wait()
sched.summary()

# Filtering
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
