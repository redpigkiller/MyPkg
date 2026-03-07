"""
job.py — Base job abstraction.

Classes:
    JobStatus — Type alias for job state literals.
    Job       — Abstract Base Class for schedulable units of work.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Callable, Literal, Pattern

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]

PENDING: JobStatus = "pending"
RUNNING: JobStatus = "running"
DONE: JobStatus = "done"
FAILED: JobStatus = "failed"
CANCELLED: JobStatus = "cancelled"

class Job(ABC):
    """Abstract Base Class for schedulable units of work.
    
    Subclasses must implement `_execute()` and `kill()`.
    Users interact directly with Job instances, which manage their own state
    in a thread-safe manner.
    """

    def __init__(
        self,
        name: str,
        *,
        priority: int = 0,
        max_retries: int = 0,
        resources: dict[str, int] | None = None,
        max_log_lines: int = 10_000,
    ) -> None:
        self._id: uuid.UUID = uuid.uuid4()
        self._name: str = name
        
        # Settings
        self.priority: int = priority
        self.max_retries: int = max_retries
        self.resources: dict[str, int] = resources or {}
        
        # State
        self._status: JobStatus = PENDING
        self._progress: float | None = None
        self._result: Any = None
        self._error: str | None = None
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._retry_count: int = 0
        
        # Threading & Control
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        
        # Logs
        self._output_buffer: deque[str] = deque(maxlen=max_log_lines)
        
        # Callbacks (internal & external)
        self._on_state_change_cb: Callable[[], None] | None = None
        self._on_log_cbs: list[Callable[['Job', str], None]] = []
        self._on_done_cbs: list[Callable[['Job'], None]] = []
        self._on_fail_cbs: list[Callable[['Job', str], None]] = []
        
        # Watchers: pattern regex -> user callback(job, match)
        self._watchers: list[tuple[Pattern[str], Callable[['Job', re.Match], None]]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def id(self) -> uuid.UUID:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> JobStatus:
        with self._lock:
            return self._status

    @property
    def progress(self) -> float | None:
        with self._lock:
            return self._progress

    @property
    def result(self) -> Any:
        with self._lock:
            return self._result

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def start_time(self) -> float | None:
        with self._lock:
            return self._start_time

    @property
    def end_time(self) -> float | None:
        with self._lock:
            return self._end_time

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ------------------------------------------------------------------
    # Control API (Called by User or Manager)
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Mark job as ready to run. Typically used if it was paused or manually controlled."""
        with self._lock:
            if self._status not in (PENDING, CANCELLED):
                return
            self._status = PENDING
            self._cancel_event.clear()
        self._notify_state_change()

    def cancel(self) -> None:
        """Cancel the job. Subclasses should respond to cancellation (e.g. killing process)."""
        with self._lock:
            if self._status in (DONE, FAILED, CANCELLED):
                return
            self._status = CANCELLED
            self._cancel_event.set()
        
        # If the job is running (e.g., subprocess), force it to terminate.
        self.kill()
        self._notify_state_change()

    def _reset(self) -> None:
        """Reset the job state and retry count to run again."""
        with self._lock:
            self._status = PENDING
            self._progress = None
            self._result = None
            self._error = None
            self._start_time = None
            self._end_time = None
            self._retry_count = 0
            self._cancel_event.clear()
            self._output_buffer.clear()
        self._notify_state_change()

    def set_progress(self, value: float) -> None:
        """Update job progress (0.0 to 100.0)."""
        with self._lock:
            self._progress = max(0.0, min(100.0, value))
        self._notify_state_change()

    @abstractmethod
    def kill(self) -> None:
        """Forcefully terminate the running job. Must be implemented by subclasses."""
        pass

    # ------------------------------------------------------------------
    # Data & Observability
    # ------------------------------------------------------------------
    def logs(self) -> list[str]:
        """Return all captured log lines."""
        with self._lock:
            return list(self._output_buffer)

    def tail(self, n: int = 20) -> list[str]:
        """Return the last *n* lines of captured output."""
        with self._lock:
            return list(self._output_buffer)[-n:]

    def _emit_line(self, line: str) -> None:
        """Called by subclasses to append output lines and trigger matchers/cbs."""
        with self._lock:
            self._output_buffer.append(line)
            cbs = list(self._on_log_cbs)
            watchers = list(self._watchers)
        
        for cb in cbs:
            try:
                cb(self, line)
            except (TypeError, ValueError, AttributeError):
                pass
                
        for pattern, wcb in watchers:
            try:
                m = pattern.search(line)
                if m:
                    wcb(self, m)
            except (TypeError, ValueError, AttributeError):
                pass

    # ------------------------------------------------------------------
    # Event Bindings
    # ------------------------------------------------------------------
    def on_log(self, cb: Callable[['Job', str], None]) -> None:
        with self._lock:
            self._on_log_cbs.append(cb)

    def on_done(self, cb: Callable[['Job'], None]) -> None:
        with self._lock:
            self._on_done_cbs.append(cb)

    def on_fail(self, cb: Callable[['Job', str], None]) -> None:
        with self._lock:
            self._on_fail_cbs.append(cb)

    def watch(self, pattern: str | Pattern[str], cb: Callable[['Job', re.Match], None]) -> None:
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        with self._lock:
            self._watchers.append((pattern, cb))

    # ------------------------------------------------------------------
    # Internal Lifecycle (Used by JobManager / Base Job)
    # ------------------------------------------------------------------
    @abstractmethod
    def _execute(self, log_file=None) -> None:
        """Execute the job's core workload. Subclasses must implement."""
        pass

    def _notify_state_change(self) -> None:
        """Trigger the Manager wake-up callback if registered."""
        cb = None
        with self._lock:
            cb = self._on_state_change_cb
        if cb is not None:
            cb()

    def __repr__(self) -> str:
        dur = ""
        with self._lock:
            if self._start_time:
                end = self._end_time or time.monotonic()
                dur = f", duration={end - self._start_time:.1f}s"
            prog = f", progress={self._progress:.0f}%" if self._progress is not None else ""
            return f"<{type(self).__name__} {self.name!r} status={self._status}{dur}{prog}>"
