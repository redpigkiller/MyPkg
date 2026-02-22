"""
Job — Base job abstraction for the Scheduler.

Classes:
    Job      — Base class representing a schedulable unit of work.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JobStatus constants
# ---------------------------------------------------------------------------

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]

PENDING: JobStatus = "pending"
RUNNING: JobStatus = "running"
DONE: JobStatus = "done"
FAILED: JobStatus = "failed"
CANCELLED: JobStatus = "cancelled"


# ---------------------------------------------------------------------------
# OutputMatcher
# ---------------------------------------------------------------------------

MatchTiming = Literal["realtime", "post"]
HookEvent = Literal["on_start", "on_done", "on_fail", "on_cancel", "on_output"]

@dataclass
class OutputMatcher:
    name: str
    match_fn: Callable[[str], Any]
    callback: Callable[[Any, "Job"], None]
    once: bool
    timing: MatchTiming


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
        self.status: JobStatus = PENDING
        self.exit_code: Optional[int] = None
        self.duration: Optional[float] = None
        self.log_path: Optional[Path] = None

        # --- process handle (set during _execute) ---
        self._proc: Optional[subprocess.Popen] = None
        self._proc_ready = threading.Event()  # set once _proc is assigned

        # --- stdout streaming ---
        self._output_buffer: List[str] = []
        self._lock = threading.Lock()

        # --- hooks & matchers & actions ---
        self._matchers: List[OutputMatcher] = []
        self._hooks: Dict[str, List[Callable]] = {
            "on_start": [], "on_done": [], "on_fail": [],
            "on_cancel": [], "on_output": [],
        }
        self._actions: Dict[str, Tuple[str, Callable[[], None]]] = {}

    # ----- execution lifecycle (subclasses override) -----

    def _pre_execute(self) -> None:
        """Called before ``_execute()``.  Subclasses override for setup logic.

        This runs inside the worker thread, after ``on_start`` hooks.
        """
        pass

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

    def _post_execute(self) -> None:
        """Called automatically after job finishes to run post logic and hooks."""
        self._proc_ready.clear()
        self._proc = None

        # run post matchers
        with self._lock:
            lines = list(self._output_buffer)
            matchers = [m for m in self._matchers if m.timing == "post"]

        for line in lines:
            to_remove = []
            for m in matchers:
                try:
                    res = m.match_fn(line)
                    if res:
                        m.callback(res, self)
                        if m.once:
                            to_remove.append(m)
                except Exception as exc:
                    logger.debug(
                        "Matcher %r raised during post-match: %s", m.name, exc
                    )
            for m in to_remove:
                matchers.remove(m)

        if self.status == DONE:
            self._trigger_hook("on_done")
        elif self.status == CANCELLED:
            self._trigger_hook("on_cancel")
        elif self.status == FAILED:
            self._trigger_hook("on_fail")

    # ----- output streaming API -----

    def _emit_line(self, line: str) -> None:
        """Internal: buffer a line and notify all hooks / matchers."""
        with self._lock:
            self._output_buffer.append(line)
            # snapshot matchers under lock
            matchers = [m for m in self._matchers if m.timing == "realtime"]

        to_remove = []
        for m in matchers:
            try:
                res = m.match_fn(line)
                if res:
                    m.callback(res, self)
                    if m.once:
                        to_remove.append(m.name)
            except Exception as exc:
                logger.debug(
                    "Matcher %r raised during realtime match: %s", m.name, exc
                )
        for m_name in to_remove:
            self.remove_matcher(m_name)

        for hook_cb in self._hooks["on_output"]:
            try:
                hook_cb(line, self)
            except Exception as exc:
                logger.debug("on_output hook raised: %s", exc)

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

    def send_input(self, text: str) -> None:
        """Write *text* to the running process's stdin."""
        if self.status != RUNNING:
            raise RuntimeError(f"Job {self.name!r} is not running (status={self.status}).")
        # Wait for process handle (status may change to RUNNING before Popen).
        self._proc_ready.wait(timeout=10)
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"Job {self.name!r}: stdin not available.")
        self._proc.stdin.write(text)
        self._proc.stdin.flush()

    def interrupt(self) -> None:
        """Send SIGINT to the running process."""
        if self.status != RUNNING:
            raise RuntimeError(f"Job {self.name!r} is not running (status={self.status}).")
        self._proc_ready.wait(timeout=10)
        if self._proc is None:
            raise RuntimeError(f"Job {self.name!r}: no process handle.")

        if sys.platform == "win32":
            try:
                self._proc.send_signal(signal.CTRL_C_EVENT)
            except Exception:
                self._proc.terminate()
        else:
            self._proc.send_signal(signal.SIGINT)

    def kill(self, force: bool = False) -> None:
        """Terminate the running process.

        If force=False, calls interrupt() instead of forceful kill.
        Subclasses can override for custom kill behaviour.
        Raises ``RuntimeError`` if job is not running.
        """
        if not force:
            self.interrupt()
            return

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

    # ----- actions -----

    def actions(self) -> Dict[str, Tuple[str, Callable]]:
        """Return job-type-specific actions as ``{name: (description, callable)}``.

        Subclasses override to provide extra features.
        """
        return dict(self._actions)

    def register_action(self, name: str, description: str, fn: Callable[[], None]) -> None:
        """Register a new action for this job."""
        self._actions[name] = (description, fn)

    # ----- matchers -----

    def add_matcher(
        self,
        match_fn: Callable[[str], Any],
        callback: Callable[[Any, "Job"], None],
        *,
        name: Optional[str] = None,
        once: bool = False,
        timing: MatchTiming = "realtime"
    ) -> None:
        """Add an OutputMatcher for log analysis."""
        if name is None:
            name = f"matcher_{len(self._matchers)}_{id(match_fn)}"
        with self._lock:
            self._matchers.append(OutputMatcher(name, match_fn, callback, once, timing))

    def remove_matcher(self, name: str) -> None:
        """Remove an OutputMatcher by name."""
        with self._lock:
            self._matchers = [m for m in self._matchers if m.name != name]

    # ----- hooks -----

    def add_hook(self, event: HookEvent, callback: Callable) -> None:
        """Add a lifecycle hook.

        Events: ``"on_start"``, ``"on_done"``, ``"on_fail"``,
        ``"on_cancel"``, ``"on_output"``.

        Callback signatures:
        - ``on_output``: ``callback(line: str, job: Job) -> None``
        - all others:    ``callback(job: Job) -> None``
        """
        if event not in self._hooks:
            raise ValueError(f"Invalid hook event: {event}. Allowed: {list(self._hooks.keys())}")
        self._hooks[event].append(callback)

    def remove_hook(self, event: HookEvent, callback: Callable) -> None:
        """Remove a previously registered hook callback.

        Silently does nothing if the callback was not found.
        """
        if event not in self._hooks:
            raise ValueError(f"Invalid hook event: {event}. Allowed: {list(self._hooks.keys())}")
        try:
            self._hooks[event].remove(callback)
        except ValueError:
            pass

    def _trigger_hook(self, event: HookEvent) -> None:
        """Invoke all callbacks for a lifecycle event."""
        for cb in self._hooks.get(event, []):
            try:
                # on_output has different signature and is called via _emit_line
                if event != "on_output":
                    cb(self)
            except Exception as exc:
                logger.debug("Hook %r callback raised: %s", event, exc)

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
