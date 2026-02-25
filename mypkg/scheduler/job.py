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
import time
import uuid
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
        name:         Unique identifier for this job.
        cmd:          Shell command string to execute (or label for custom jobs).
        cwd:          Working directory (None = inherit from Scheduler).
        env:          Extra environment variables merged with ``os.environ``.
        priority:     Higher value = run first.  Default ``0``.
        depends_on:   Jobs that must finish before this one starts.
        resources:    Resource requirements, e.g. ``{"local": 1}``.
        timeout:      Max wall-clock seconds. ``None`` = no limit.
        tags:         Free-form labels for grouping/filtering, e.g. ``["wave1", "regression"]``.
        max_retries:  Number of times to retry after failure (default 0 = no retry).
        retry_if:     Callable ``(job) -> bool`` — if provided, only retry when it
                      returns True.  ``None`` means always retry.
        status:       Current state: pending / running / done / failed / cancelled.
        exit_code:    Process return code (``None`` until finished).
        duration:     Elapsed wall-clock seconds.  Updated in real-time while
                      running; frozen at the finish time once the job completes.
        progress:     Optional 0–100 float for progress tracking. Set by the user
                      via matchers or hooks; displayed in ``status()``/``summary()``.
        log_path:     Auto-assigned log file path (``None`` if no log_dir).
        retry_count:  How many retries have been executed so far (0-based).
    """

    # Subclasses can override to declare default resource requirements.
    default_resources: Dict[str, int] = {"local": 1}

    def __init__(
        self,
        name: Optional[str] = None,
        cmd: str = "",
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        priority: int = 0,
        depends_on: Optional[List["Job"]] = None,
        resources: Optional[Dict[str, int]] = None,
        timeout: Optional[float] = None,
        tags: Optional[List[str]] = None,
        max_retries: int = 0,
        retry_if: Optional[Callable[["Job"], bool]] = None,
    ) -> None:
        # Auto-generate a unique name if not provided.
        if not name:
            name = f"{type(self).__name__}_{uuid.uuid4().hex[:8]}"
        # cmd is optional for custom subclasses; auto-label if omitted
        if not cmd:
            cmd = f"<{type(self).__name__}>"

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
        self.tags: List[str] = list(tags) if tags else []
        self.max_retries = max_retries
        self.retry_if = retry_if

        # --- runtime state (managed by Scheduler) ---
        self.status: JobStatus = PENDING
        self.exit_code: Optional[int] = None
        self.progress: Optional[float] = None
        self.log_path: Optional[Path] = None
        self.retry_count: int = 0

        # --- timing ---
        self._start_time: Optional[float] = None   # set by Scheduler on first dispatch
        self._end_time: Optional[float] = None     # set by Scheduler on finish

        # --- completion event (set by _post_execute; cleared on retry) ---
        self._finished_event = threading.Event()

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

        # Unblock anyone waiting on job.wait()
        self._finished_event.set()

    def _reset_for_retry(self) -> None:
        """Reset runtime state so the job can be re-executed.  Called by Scheduler."""
        self._proc_ready.clear()
        self._finished_event.clear()
        self._proc = None
        self._output_buffer.clear()
        self.exit_code = None
        self.progress = None
        self._end_time = None
        self.status = RUNNING  # stays RUNNING across retries

    # ----- timing -----

    @property
    def duration(self) -> Optional[float]:
        """Elapsed wall-clock seconds.

        - ``None`` if the job has not started yet.
        - Live running total while the job is executing.
        - Frozen at the finish time once the job completes.
        """
        if self._start_time is None:
            return None
        end = self._end_time
        if end is not None:
            return end - self._start_time
        return time.monotonic() - self._start_time

    # ----- output streaming API -----

    def _emit_line(self, line: str) -> None:
        """Internal: buffer a line and notify all hooks / matchers."""
        with self._lock:
            self._output_buffer.append(line)
            # Snapshot both matchers and hooks under lock to prevent
            # RuntimeError if add_hook/add_matcher is called concurrently.
            matchers = [m for m in self._matchers if m.timing == "realtime"]
            output_hooks = list(self._hooks["on_output"])

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

        for hook_cb in output_hooks:
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

    # ----- status helpers -----

    def fail_if(
        self,
        callback: Callable[[str, "Job"], bool],
        *,
        timing: MatchTiming = "realtime",
    ) -> str:
        """Mark the job FAILED when *callback(line, job)* returns True.

        Adds a matcher that sets ``self.status = FAILED`` and
        ``self.exit_code = 1`` on the first matching line.  Subsequent
        output continues streaming (you can still read it), but the
        final status will be FAILED regardless of the process exit code.

        Returns the matcher name so you can remove it with
        ``remove_matcher(name)`` if needed.

        Example::

            # Fail even if the process exits with code 0
            job.fail_if(lambda line, j: "SIMULATION FAILED" in line)
        """
        def _match(line: str) -> bool:
            try:
                return bool(callback(line, self))
            except Exception:
                return False

        def _apply(result: bool, job: "Job") -> None:
            job.status = FAILED
            if job.exit_code is None:
                job.exit_code = 1

        return self.add_matcher(_match, _apply, once=True, timing=timing)

    def done_if(
        self,
        callback: Callable[[str, "Job"], bool],
        *,
        timing: MatchTiming = "realtime",
    ) -> str:
        """Mark the job DONE when *callback(line, job)* returns True.

        Useful when a tool prints a "PASSED" / "COMPLETE" line before
        exiting, and you want to trust the output rather than the
        exit code.

        Returns the matcher name so you can remove it with
        ``remove_matcher(name)`` if needed.

        Example::

            job.done_if(lambda line, j: "Simulation PASSED" in line)
        """
        def _match(line: str) -> bool:
            try:
                return bool(callback(line, self))
            except Exception:
                return False

        def _apply(result: bool, job: "Job") -> None:
            job.status = DONE
            if job.exit_code is None:
                job.exit_code = 0

        return self.add_matcher(_match, _apply, once=True, timing=timing)

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

    def kill(self) -> None:
        """Forcefully terminate the running process.

        On Windows: uses ``taskkill /F /T`` to kill the entire process tree.
        On POSIX:   sends SIGTERM, escalates to SIGKILL after 5 s.

        For a graceful stop, use ``interrupt()`` (SIGINT) or
        ``send_input("exit\\n")`` if the process reads from stdin.

        Raises ``RuntimeError`` if the job is not running.
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
    ) -> str:
        """Add an OutputMatcher for log analysis.

        Returns the matcher *name* so it can be passed to ``remove_matcher()``.
        """
        if name is None:
            name = f"matcher_{len(self._matchers)}_{id(match_fn)}"
        with self._lock:
            self._matchers.append(OutputMatcher(name, match_fn, callback, once, timing))
        return name

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

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the job reaches a terminal state (done/failed/cancelled).

        Uses a ``threading.Event`` internally — no busy-polling, no CPU waste.

        Args:
            timeout: Maximum seconds to wait.  ``None`` = wait forever.

        Returns:
            ``True`` if the job finished within the timeout, ``False`` if the
            timeout expired before the job completed.

        Example::

            sched.start()
            if not setup_job.wait(timeout=30):
                print("Setup timed out!")
        """
        return self._finished_event.wait(timeout)

    @property
    def is_finished(self) -> bool:
        return self.status in (DONE, FAILED, CANCELLED)

    @property
    def job_type(self) -> str:
        """Human-readable job type name (class name)."""
        return type(self).__name__

    def __repr__(self) -> str:
        dur = f", duration={self.duration:.1f}s" if self.duration is not None else ""
        prog = f", progress={self.progress:.0f}%" if self.progress is not None else ""
        tags = f", tags={self.tags}" if self.tags else ""
        return (
            f"{self.job_type}({self.name!r}, status={self.status!r}, "
            f"exit_code={self.exit_code}, priority={self.priority}"
            f"{dur}{prog}{tags})"
        )
