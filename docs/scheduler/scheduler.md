# Scheduler — Minimalist Job Management

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

`Scheduler` is a lightweight, cross-platform job scheduling module designed for executing time-consuming background tasks (compilations, simulations, or Python workloads) concurrently without freezing your application or exhausting system resources.

It features **static resource tracking**, **auto-retry capabilities**, **event callbacks**, and **robust thread-safe supervision** over standard processes and Python functions.

---

## Quick Start (User Guide)

To safely run concurrent jobs, you construct individual `Job` objects (`CmdJob` or `FuncJob`) and hand them over to a `JobManager` to coordinate their execution based on available resources.

### 1. The JobManager

The Manager serves as the bouncer for your system. You tell it your resource limits upfront.

```python
from mypkg.scheduler import JobManager

# Limit to 4 parallel workers, and cap the "gpu" resource at 1 at any given time.
manager = JobManager(max_workers=4, resources={"gpu": 1})

# Start the background scheduling loop
manager.start()
```

### 2. Creating Jobs

Jobs are the units of work. You can run terminal commands using `CmdJob`, or wrapped Python functions using `FuncJob`.

```python
from mypkg.scheduler import CmdJob, FuncJob

# A simple terminal command
compile_job = CmdJob(
    name="build_app", 
    cmd="make clean && make -j4",
    cwd="/path/to/project",
)

# A terminal command requiring special resources and retry logic
sim_job = CmdJob(
    name="run_sim",
    cmd="./simulator --intensive",
    resources={"gpu": 1},  # Will wait nicely if the gpu is busy
    max_retries=2,         # Automatically retry twice on failure
    priority=10,           # Higher priority runs sooner 
)

# A Python callback function
def calc_pi(precision):
    # compute...
    return 3.14

math_job = FuncJob(
    name="calculate_pi",
    func=calc_pi,
    kwargs={"precision": 100}
)
```

### 3. Execution & Waiting

Once your manager is running, throw jobs at it to add them to the queue. You can block the main thread to wait for all jobs (or a specific one) to finish.

```python
# Add jobs to the queue
manager.add(compile_job)
manager.add(sim_job)
manager.add(math_job)

# Wait for them all to finish (Blocks until completion)
manager.wait()

print(f"Simulation completed with status: {sim_job.status}")
print(f"Math calculation result: {math_job.result}")
```

### 4. Interactive Callbacks & Log Watching

You can attach callbacks to monitor progress, or listen for specific words in the console output.

```python
test_job = CmdJob("test", "pytest -v")

# Listen to structural state changes
test_job.on_done(lambda job: print(f"✅ {job.name} Passed!"))
test_job.on_fail(lambda job, err: print(f"❌ {job.name} Failed: {err}"))

# React instantly whenever the output emits a specific regex pattern
test_job.watch(r"FAILED", lambda job, match: print("Uh oh, a test failed!"))

# Read the last 5 lines of captured output
print(test_job.tail(5))
```

### 5. Pausing and Cancelling

If you realize a pending job is redundant, or you need to pause the whole show:

```python
# Pull a specific job from the queue or kill it if it's already running
manager.cancel(sim_job.id)

# Temporarily halt the dispatching of new jobs
manager.pause()

# Resume worker operations
manager.resume()

# Politely stop the manager thread (waits on active threads)
manager.stop()
```

---

## API Reference (Detailed Control)

### `JobManager` API

| Method | Description |
| --- | --- |
| `JobManager(max_workers=4, resources=None, log_dir=None)`| Initializes the manager. `resources` controls capacity caps (`Dict[str, int]`). Waitlists jobs exceeding these limits. |
| `.start()` / `.stop()`| Starts/stops the internal background loop. Stopping waits for currently RUNNING tasks. |
| `.add(job)`| Adds a `Job` instance to the queue. Resolves statically impossible capacities via ValueError fast-fail. |
| `.cancel(target_id)`| Forces a specific job (by string UUID or UUID object) to `CANCELLED` and kills its process. |
| `.wait(target_id=None, timeout=None)`| Blocking call that pauses execution until the specific job (or all submitted jobs if None) hit terminal states (`DONE`, `FAILED`, `CANCELLED`). |
| `.pause()` / `.resume()`| Suspends or revives the core loop from pulling the next `PENDING` job into active execution. |
| `.on_queue_drained(cb)`| Registers a callback that fires whenever the manager has no more pending or running jobs. |
| `.jobs()`, `.running()`, `.pending()`| Returns snapshots containing lists of `Job` instances matching those states. |

### `Job` Core Status

Every abstract `Job` carries the following standard properties:

- `.status` (`JobStatus`): Resolves to `"pending"`, `"running"`, `"done"`, `"failed"`, or `"cancelled"`.
- `.result` (`Any`): The returned payload upon success (For `CmdJob`, it's the exit code `0`. For `FuncJob`, it's the Python return value). **Note**: Because a successful CmdJob returns `0` (which is falsy in Python), avoid checking `if job.result:` to test for success. Always check `if job.status == "done":` instead.
- `.error` (`str \| None`): Error string or traceback recorded upon failure.
- `.is_cancelled` (`bool`): Direct indicator flag determining cancellation triggers.

### `CmdJob` Parameters
* `name`: Display string for logging.
* `cmd`: Standard shell string to execute.
* `cwd`: Starting directory absolute path.
* `env`: Complete environment variable override dictionary.
* `priority`: Integer sorting queue precedence. Higher goes first.
* `max_retries`: Execution attempts before formally labeling `FAILED`.
* `resources`: `Dict[str, int]` of resource tags required for this task.
* `max_log_lines`: Retained memory deque limit for trailing outputs (Default: `10000`).

### `FuncJob` Parameters
Similar to `CmdJob`, but receives:
* `func`: `Callable` object to execute on the worker thread.
* `args`: Tuple of arguments applied to the func. 
* `kwargs`: Dictionary of keyword parameters provided to the func.
> **Note**: `FuncJob` execution loops directly inside the ThreadPool worker limits, subject to GIL restrictions. Suitable mostly for Python-bound IO/Web requests. Use `CmdJob` for heavy parallel CPU computations.
