"""
Job — Base job abstraction for the Scheduler.

Classes:
    Job      — Base class representing a schedulable unit of work.
    CmdJob   — Concrete subclass for shell commands (local execution).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# JobStatus constants
# ---------------------------------------------------------------------------

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Job — base class
# ---------------------------------------------------------------------------

class Job:
    """A schedulable unit of work.

    Attributes:
        name:        Unique identifier for this job.
        cmd:         Shell command string to execute.
        cwd:         Working directory (None = inherit from Scheduler).
        env:         Extra environment variables merged with ``os.environ``.
        priority:    Higher value = run first.  Default ``0``.
        depends_on:  Jobs that must finish before this one starts.
        resources:   Resource requirements, e.g. ``{"local": 1}``.
        timeout:     Max wall-clock seconds. ``None`` = no limit.
        status:      Current state: pending / running / done / failed / cancelled.
        exit_code:   Process return code (``None`` until finished).
        duration:    Wall-clock seconds (``None`` until finished).
        log_path:    Auto-assigned log file path (``None`` if no log_dir).
    """

    # Subclasses can override to declare default resource requirements.
    default_resources: Dict[str, int] = {"local": 1}

    def __init__(
        self,
        name: str,
        cmd: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        priority: int = 0,
        depends_on: Optional[List["Job"]] = None,
        resources: Optional[Dict[str, int]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        if not name:
            raise ValueError("Job name must not be empty.")
        if not cmd:
            raise ValueError("Job cmd must not be empty.")

        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.priority = priority
        self.depends_on: List[Job] = list(depends_on) if depends_on else []
        self.resources: Dict[str, int] = (
            dict(resources) if resources is not None else dict(self.default_resources)
        )
        self.timeout = timeout

        # --- runtime state (managed by Scheduler) ---
        self.status: str = PENDING
        self.exit_code: Optional[int] = None
        self.duration: Optional[float] = None
        self.log_path: Optional[Path] = None

        # --- process handle (set during _execute) ---
        self._proc: Optional[subprocess.Popen] = None
        self._proc_ready = threading.Event()  # set once _proc is assigned

        # --- stdout streaming ---
        self._output_buffer: List[str] = []
        self._callbacks: List[Callable[[str], None]] = []
        self._lock = threading.Lock()

    # ----- execution (subclasses override) -----

    def _execute(self, log_file=None) -> None:
        """Run the job.  Subclasses **must** override this.

        Responsibilities of the implementation:
        - Set ``self._proc`` and call ``self._proc_ready.set()`` (if applicable).
        - Stream output via ``self._emit_line(line)`` and write to *log_file*.
        - Set ``self.exit_code`` and ``self.status`` (DONE / FAILED).

        The Scheduler handles timing, resource release, and error wrapping.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _execute()."
        )

    # ----- output streaming API -----

    def on_output(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked for each stdout line.

        The callback receives a single string (one line, newline stripped).
        Thread-safe: callbacks are invoked from the worker thread.
        """
        self._callbacks.append(callback)

    def remove_output(self, callback: Callable[[str], None]) -> None:
        """Unregister a previously registered output callback."""
        try:
            self._callbacks.remove(callback)
        except ValueError:
            pass

    def _emit_line(self, line: str) -> None:
        """Internal: buffer a line and notify all callbacks."""
        with self._lock:
            self._output_buffer.append(line)
        for cb in self._callbacks:
            try:
                cb(line)
            except Exception:
                pass  # never let a bad callback crash the worker

    def tail(self, n: int = 20) -> List[str]:
        """Return the last *n* lines of captured output."""
        with self._lock:
            return list(self._output_buffer[-n:])

    @property
    def output_lines(self) -> List[str]:
        """Full output history (snapshot copy)."""
        with self._lock:
            return list(self._output_buffer)

    # ----- interactive control -----

    def send(self, text: str) -> None:
        """Write *text* to the running process's stdin.

        Raises ``RuntimeError`` if the job is not running or stdin is
        unavailable.
        """
        if self.status != RUNNING:
            raise RuntimeError(f"Job {self.name!r} is not running (status={self.status}).")
        # Wait for process handle (status may change to RUNNING before Popen).
        self._proc_ready.wait(timeout=10)
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"Job {self.name!r}: stdin not available.")
        self._proc.stdin.write(text)
        self._proc.stdin.flush()

    def kill(self) -> None:
        """Terminate the running process.

        Subclasses can override for non-local jobs (e.g. ``GridJob`` → ``qdel``).
        Raises ``RuntimeError`` if job is not running.
        """
        if self.status != RUNNING:
            raise RuntimeError(f"Job {self.name!r} is not running (status={self.status}).")
        # Wait for process handle (status may change to RUNNING before Popen).
        self._proc_ready.wait(timeout=10)
        if self._proc is None:
            raise RuntimeError(f"Job {self.name!r}: no process handle.")
        if sys.platform == "win32":
            # On Windows, shell=True spawns cmd.exe; terminate() only kills the
            # shell, not child processes.  Use taskkill /T to kill the tree.
            subprocess.run(
                f"taskkill /F /T /PID {self._proc.pid}",
                shell=True, capture_output=True,
            )
        else:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def actions(self) -> Dict[str, Tuple[str, Callable]]:
        """Return job-type-specific actions as ``{name: (description, callable)}``.

        Subclasses override to provide extra features.
        """
        return {}

    # ----- helpers -----

    @property
    def is_finished(self) -> bool:
        return self.status in (DONE, FAILED, CANCELLED)

    @property
    def job_type(self) -> str:
        """Human-readable job type name (class name)."""
        return type(self).__name__

    def __repr__(self) -> str:
        return (
            f"{self.job_type}({self.name!r}, status={self.status!r}, "
            f"exit_code={self.exit_code}, priority={self.priority})"
        )


# ---------------------------------------------------------------------------
# CmdJob — local command execution
# ---------------------------------------------------------------------------

def _open_file_cross_platform(path: Union[str, Path]) -> None:
    """Open a file or directory with the system default handler."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class CmdJob(Job):
    """A job that runs a shell command on the local machine.

    Default resources: ``{"local": 1}``

    Example::

        job = CmdJob("compile", cmd="make -j4", cwd="/proj/rtl", timeout=600)
    """

    default_resources: Dict[str, int] = {"local": 1}

    def _execute(self, log_file=None) -> None:
        """Run a shell command via subprocess."""
        env = None
        if self.env:
            env = {**os.environ, **self.env}

        proc = subprocess.Popen(
            self.cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
            text=True,
            bufsize=1,  # line-buffered
        )
        self._proc = proc
        self._proc_ready.set()

        # Stream stdout line-by-line
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n").rstrip("\r")
            self._emit_line(line)
            if log_file:
                log_file.write(raw_line)
                log_file.flush()

        proc.wait()
        self.exit_code = proc.returncode
        self.status = DONE if proc.returncode == 0 else FAILED

    def actions(self) -> Dict[str, Tuple[str, Callable]]:
        acts: Dict[str, Tuple[str, Callable]] = {}
        if self.log_path and self.log_path.exists():
            acts["open_log"] = ("開啟 log 檔案", lambda: _open_file_cross_platform(self.log_path))
        if self.cwd:
            acts["open_cwd"] = ("開啟工作目錄", lambda: _open_file_cross_platform(self.cwd))
        return acts
