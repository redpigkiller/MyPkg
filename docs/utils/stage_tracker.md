# StageTracker

[![English](https://img.shields.io/badge/Language-English-blue.svg)](stage_tracker.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](stage_tracker_zh.md)

A workflow-oriented logger for sequential, multi-stage processes. Designed to handle execution tracking, accumulated error handling, checkpointing, and auto-generated summaries.

## Key Features

- **Stage Lifecycle Management**: Neatly separates execution into stages (e.g. "Load" -> "Process" -> "Upload").
- **Thread-Safe**: Uses `threading.local()` so each thread maintains its own stage history and issue tracking, while sharing log handlers.
- **Accumulated Error Handling**: Unlike standard logging where errors are scattered, `StageTracker` collects them and allows you to fail early (`checkpoint`) or wait until the summary.
- **Rich Summary Generation**: Auto-generates a clean summary console block displaying total errors and warnings organized by stage.
- **Lazy Evaluation**: `tracker.info(..., data=my_dict)` delays expensive JSON serialization until truly needed by the handler.

## Basic Usage

### Flat Mode
Best for top-down, sequential scripts.

```python
from mypkg.utils.stage_tracker import StageTracker

# Shared instance pattern
tracker = StageTracker("MainTracker")

# Starts "Initialization" stage
tracker.set_stage("Initialization")
tracker.info("Starting workflow", track=True)
tracker.warning("Debug mode enabled")

# Implicitly finalizes "Initialization" and starts "Processing"
tracker.set_stage("Data Processing")
tracker.error("File 'corrupt.txt' is corrupt") # Accumulates issue
tracker.error("File 'missing.txt' not found")

# Will raise StageFailedError because of the two errors above
try:
    tracker.checkpoint()
except Exception as e:
    print(e)
    
tracker.summary()
```

### Context Manager Mode
Best for isolated blocks, loops, or complex nested logic.

```python
from mypkg.utils.stage_tracker import StageTracker

tracker = StageTracker("ContextTracker")

with tracker.stage("Download"):
    tracker.info("Downloading files...")
    # Health checked automatically upon exit. 
    # If any `tracker.error()` was called, StageFailedError is raised here.

with tracker.stage("Parsing"):
    tracker.fatal("Out of memory!") # Raises StageFailedError immediately
```

*Note: You cannot mix Flat Mode and Context Manager Mode loops within the same `StageTracker` execution context.*

## API Reference

### Configuration
* `add_console_handler(level="INFO", fmt="...")`: Adds a console output. (Added automatically on init). Uses `rich` if installed.
* `add_file_handler(path, level="DEBUG", fmt="...", max_bytes=0, backup_count=0)`: Adds a log file, with optional log rotation.
* `reset(keep_handlers=False)`: Clears all accumulated issues, stage history, and the current stage. Useful before restarting a workflow in the same thread.

### Logging
* `debug(msg, track=False, **kwargs)`: Standard debug tracker.
* `info(msg, track=False, **kwargs)`: Standard info tracker. Set `track=True` to include in the ending summary block.
* `warning(msg, track=True, **kwargs)`: Warning log, tracked in summary by default.
* `error(msg, **kwargs)`: Error tracker. Adds an error issue. Does not raise immediately.
* `fatal(msg, **kwargs)`: Logs a critical error and raises `StageFailedError` immediately.

### Methods
* `set_stage(name)`: Starts a new flat-mode stage. Checks the health of the previous stage.
* `checkpoint()`: Raises `StageFailedError` if the current stage has accumulated any errors.
* `summary(title="EXECUTION SUMMARY") -> bool`: Prints the summary. Returns `True` if no errors were found, `False` otherwise.
* `get_issues(stage=None, level=None)`: Returns a list of `Issue` dataclasses matching the criteria. Level can be a single `ErrorLevel` or a list of levels.
