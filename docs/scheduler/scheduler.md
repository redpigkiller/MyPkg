# Scheduler — Cross-Platform Job Scheduler

[![English](https://img.shields.io/badge/Language-English-blue.svg)](scheduler.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](scheduler_zh.md)

## What is the Scheduler?

When you are developing and need to run several time-consuming tasks simultaneously (for example: running dozens of simulation tests, or compiling multiple code projects), if you manually click to run them one by one, or write a simple `for` loop to launch them all at once, your computer might **crash due to instantly exhausting all CPU and memory**.

The **Scheduler** was created exactly to solve this problem!
Think of it like a smart restaurant manager. It helps you:
1. **Control the number of customers served simultaneously (Resource Management)**: Ensures your computer doesn't get overloaded.
2. **Arrange the serving order (Dependencies)**: Ensures the "chopping" task finishes before the "cooking" task begins.
3. **Broadcast live updates (Status Monitoring)**: Lets you know which tasks are currently running, which are waiting in line, and which have failed.

---

## 5 Core Concepts

Before using the Scheduler, take a minute to understand these five core mechanisms, and you will have complete mastery over it!

1. **Resource Management (Resources)**
   - Your computer's CPU cores, available memory, or nodes in a cluster are limited "resources".
   - When creating the Scheduler, you tell it: "I have a total of 4 `local` resources."
   - When you execute a `Job`, you can set how many `local` resources it consumes. The Scheduler will ensure that **at most 4 Jobs are running at the same time**. If a 5th Job arrives, it must wait in line until someone finishes and "returns" their resource before it can start.

2. **Dependencies (`depends_on`)**
   - By configuring `depends_on=[job_A, job_B]`, you can tell the Scheduler: "This task must wait until both 'job_A' and 'job_B' have finished successfully (`DONE`) before it can start."
   - If a prerequisite task unfortunately fails (`FAILED`), this dependent task will automatically be marked as failed to avoid wasted effort.

3. **Priority**
   - Jobs with higher numbers have higher priority. When resources become available, the Scheduler will dispatch a `priority=100` task before a `priority=0` task.

4. **Timeout**
   - Some tasks might hang forever due to a bug. You can set `timeout=600` (seconds) for a Job. If it hasn't finished when the time is up, the Scheduler will ruthlessly terminate it (`KILL`) to free up resources.

5. **Interactive Operations (Actions)**
   - Different tasks might have their own "special skills".
   - For example, a finished terminal command task (`CmdJob`) might offer an `open_log` action, allowing you to open its log file in your default editor with a single command. Every task can do different things, and you can use `.actions("job_name")` to explore what tricks it knows!

---

## Quick Start Demo

In just 3 steps, let the Scheduler manage your tasks for you!

```python
from mypkg import Scheduler, CmdJob

# Step 1: Create a Scheduler
# Tell it: You can only use a maximum of 4 resources named "local" at a time
sched = Scheduler(resources={"local": 4}, log_dir="./logs")

# Step 2: Create Jobs
# compile_job is a terminal command task
compile_job = CmdJob("compile", cmd="gcc main.c -o main")

# sim_job is also a command task, but it says: "I must wait for compile_job to finish first!"
sim_job = CmdJob(
    name="run_sim", 
    cmd="./main", 
    depends_on=[compile_job],  # Set dependency
    timeout=60                 # Kill it if it takes longer than 60 seconds
)

# Step 3: Hand the jobs over to the Scheduler, and tell it to start working!
sched.submit(compile_job, sim_job)
sched.run()          # This command blocks, waiting until ALL tasks are finished

print(sched.summary())  # After it finishes, print a beautifully formatted summary report!
```

---

## How to Control the Scheduler (API Details)

Here is how you can operate the `Scheduler` object. Every command gives you full control.

### 📅 Adding and Starting
* `submit(*jobs)`: Tosses several Jobs into the queue.
* `run()`: **Blocks your program**. It starts dispatching tasks and waits until every single one is finished (or failed) before moving to the next line of your code. Ideal for scripts.
* `start()`: **Does not block your program**. It opens an invisible worker (Thread) in the background to dispatch tasks, allowing your code to continue executing immediately. Ideal for GUI applications.
* `wait()`: If you used `start()`, but at some point you decide you want to wait for everything to finish, call `wait()`.

### ⏸️ Pausing, Resuming and Stopping
* `pause()`: Pause the scheduler. **Currently running Jobs will finish**, but no new tasks will be dispatched.
* `resume()`: Resume the scheduler. Tasks queued during the pause will start being dispatched again.
* `stop()`: Tells the Scheduler: "Stop dispatching new tasks!". However, it is very polite and will wait for any **currently running** Jobs to finish properly before it actually shuts down.
* `is_complete() -> bool`: Returns `True` when every submitted job has reached a terminal state (done/failed/cancelled). Returns `False` if no jobs were submitted. Ideal for driving interactive loops.
* `is_running -> bool` (property): Returns `True` if the background scheduler thread is currently active (i.e., after `start()` is called and before it finishes).

### 🕹️ Single Job Interaction
When you want to do something to a specific task (`"job_name"`) in the queue:
* `get("name")`: Retrieves that specific `Job` object for you to manipulate.
* `follow("name", n=20)`: Like a live stream! Prints the last 20 lines of output from this task directly to your Terminal in real-time.
* `cancel("name")`: Pulls a task that is **still queuing** out of the line so it won't run.
* `kill("name")`: Forcefully terminates a task that is **currently running**.
* `set_priority("name", n)`: This task is urgent, let it cut the line! Increases its priority.
* `actions("name")`: Asks the task: "What special moves do you know?". It returns a list of special operation names it can perform.
* `action("name", "action_name")`: Directly commands this task to use its special move (e.g., `"open_log"`).

### 📊 Status and Filtering
Want to know where everyone is at right now?
* `status() -> str`: **Returns** a concise status table string. Use `print(sched.status())` to display it.
* `summary() -> str`: **Returns** a detailed summary table string with execution times. Use `print(sched.summary())` to display it.
* You can also directly inspect the rosters (returns a list of Jobs):
  * `sched.pending`: The list of jobs still waiting in line.
  * `sched.running`: The list of jobs currently executing.
  * `sched.done`: The list of jobs that finished successfully.
  * `sched.failed`: The list of jobs that failed.
  * `sched.cancelled`: The list of jobs that were cancelled.

---

## Built-in Job Types

### CmdJob (Local Terminal Command)
Designed specifically to execute command-line instructions on your computer.

```python
CmdJob(
    name="sim_01",
    cmd="python run.py --tc 01",            # The command you would type in the terminal
    cwd="/proj/sim",                        # [Optional] Which folder should it run in?
    env={"SEED": "42"},                     # [Optional] Any extra environment variables?
    priority=10,                            # Higher numbers run first
    resources={"local": 1},                 # [Optional] It consumes 1 local resource (Default is 1)
)
```

---

## Hooks & Matchers — Lifecycle Events and Log Analysis

### Hooks — Lifecycle Event Callbacks

You can attach callbacks to various lifecycle stages of a Job, so it automatically notifies you when specific events occur.

| Hook Event | When it fires | Callback Signature |
|-----------|---------|---------------|
| `on_start` | Before the job starts executing | `callback(job)` |
| `on_done` | After the job completes successfully | `callback(job)` |
| `on_fail` | After the job fails | `callback(job)` |
| `on_cancel` | When the job is cancelled | `callback(job)` |
| `on_output` | Each time a line of stdout is produced | `callback(line, job)` |

```python
job = CmdJob("sim", cmd="python run.py")

# Notify on completion
job.add_hook("on_done", lambda j: print(f"✅ {j.name} finished!"))

# Alert on failure
job.add_hook("on_fail", lambda j: print(f"❌ {j.name} failed! exit_code={j.exit_code}"))

# Monitor output in real-time (fires for every line)
job.add_hook("on_output", lambda line, j: print(f"[{j.name}] {line}"))

# Done listening? Remove the hook
job.remove_hook("on_output", my_callback)
```

**Reading Output History**
```python
job.tail(20)          # Grab the last 20 lines of output
job.output_lines      # Get the full output history snapshot (list[str])
```

> [!NOTE]
> `on_output` hooks fire **in real-time** as each line is produced by the process.
> `output_lines` and `tail()` are **snapshot reads** — they return whatever has been
> captured so far and are safe to call from any thread.

### Matchers — Smart Log Analysis

Matchers let you define a "match function" that automatically triggers a callback when output matching your criteria appears.

```python
import re

# Detect ERROR in output
def find_error(line):
    m = re.search(r"ERROR: (.+)", line)
    return m.group(1) if m else None  # Return truthy → triggers callback

def on_error(matched_text, job):
    print(f"⚠️ Job {job.name} encountered an error: {matched_text}")

job.add_matcher(find_error, on_error, name="error_finder", timing="realtime")
```

| Parameter | Description |
|------|------|
| `timing="realtime"` | Match **immediately** as each output line is produced |
| `timing="post"` | Scan all output **once** after the job finishes |
| `once=True` | Automatically remove the matcher after the first match |

---

## Advanced: Building Your Custom Job

Sometimes you might not just want to run terminal commands. Perhaps you want the Scheduler to queue up a **Python function** you wrote.

Simply inherit from the `Job` base template and rewrite the execution logic to build your very own, specialized worker!

**Job Lifecycle**:
```
on_start hooks → _pre_execute() → _execute() → _post_execute() → on_done / on_fail / on_cancel hooks
```

### Full Example: Turning a Python Function into a Job

```python
import time
from mypkg.scheduler.job import Job, DONE, FAILED

class PythonJob(Job):
    """Turns any Python function into a Job that the Scheduler can queue"""

    def __init__(self, name, func, *args, **kwargs):
        # 1. Remember to call the parent (super) to set up infrastructure
        # 'cmd' can be any descriptive string
        super().__init__(name, cmd="[Python Callable]")
        
        # Save the arguments for later
        self.func = func
        self.args = args
        self.kwargs = kwargs

        # ⭐️ Key concept: Equip your Custom Job with a "Special Move (Action)"
        self.register_action("say_hi", "A special move that says Hi", lambda: print(f"Hi! I am the {name} job"))

    def _pre_execute(self):
        """[Optional] Called before _execute(), good for initialization"""
        self._emit_line("Running pre-execution setup...")

    def _execute(self, log_file=None):
        """2. This is the core logic. The Scheduler will call this when it's your turn"""
        try:
            self._emit_line(f"Preparing to execute function: {self.func.__name__}")
            
            # Actually execute your Python code
            result = self.func(*self.args, **self.kwargs)
            
            self._emit_line(f"Execution successful! The result is {result}")
            
            # 3. Very important: You must honestly report your final status
            self.exit_code = 0          # 0 means success
            self.status = DONE          # Mark it as successfully finished
            
        except Exception as e:
            # Handling errors
            self._emit_line(f"Oops, the program crashed: {e}")
            self.exit_code = 1          # Non-zero means failure
            self.status = FAILED        # Mark it as failed


# ================================
# 🎉 Let's test it out!
# ================================
def compute_heavy_math(x, y):
    time.sleep(2) # Pretend this calculation takes a long time
    return x ** y

# Put it into the PythonJob you just built
my_job = PythonJob("math_task", compute_heavy_math, 2, 10)

# Attach a completion notification
my_job.add_hook("on_done", lambda j: print(f"🎉 {j.name} computation complete!"))

# Throw it into the Scheduler
sched = Scheduler(resources={"local": 2})
sched.submit(my_job)
sched.run()

# Unleash this Job's special move! (It will print Hi)
sched.action("math_task", "say_hi")
```

Congratulations! You now fully understand all the secrets of the Scheduler, from its lowest-level concepts to advanced extensions!

---

## Integration Patterns: CLI / TUI / GUI

### Pattern A — Short-lived jobs (blocking)

The simplest pattern: submit everything, call `run()`, and inspect results afterwards.

```python
from mypkg import Scheduler, CmdJob

sched = Scheduler(resources={"local": 4})
sched.submit(CmdJob("build", cmd="make -j4"))
sched.submit(CmdJob("test",  cmd="pytest", depends_on=[sched.get("build")]))

sched.run()               # blocks until all jobs finish
print(sched.summary())    # summary() returns a str — print it yourself
```

### Pattern B — Long-running monitoring loop (CLI / TUI)

Use `start()` + `is_complete()` to drive an interactive command loop.

```python
from mypkg import Scheduler, CmdJob

sched = Scheduler(resources={"local": 4})
sched.submit(CmdJob("sim", cmd="python run_sim.py", timeout=3600))
sched.start()                          # non-blocking: returns immediately

while not sched.is_complete():
    cmd = input("command> ").strip()
    if cmd == "status":
        print(sched.status())          # status() returns a str
    elif cmd == "summary":
        print(sched.summary())         # summary() returns a str
    elif cmd == "pause":
        sched.pause()
    elif cmd == "resume":
        sched.resume()
    elif cmd.startswith("kill "):
        sched.kill(cmd.split()[1])
    elif cmd.startswith("follow "):
        sched.follow(cmd.split()[1])   # streams output until job finishes
    elif cmd.startswith("cancel "):
        sched.cancel(cmd.split()[1])

sched.wait()                          # ensure background thread exits cleanly
print(sched.summary())
```

### Pattern C — GUI / async callback-driven

For GUI frameworks that run their own event loop, use hooks instead of polling.

```python
from mypkg import Scheduler, CmdJob

sched = Scheduler(resources={"local": 4})
job = CmdJob("long_task", cmd="python heavy.py")

# GUI callback: update a progress widget on every output line
job.add_hook("on_output", lambda line, j: gui.append_log(line))
job.add_hook("on_done",   lambda j: gui.show_success(j.name))
job.add_hook("on_fail",   lambda j: gui.show_error(j.name, j.exit_code))

sched.submit(job)
sched.start()   # scheduler runs in background; GUI event loop continues
```
