"""
job.py — Base job abstraction.

Classes:
    JobStatus    — Type alias for job state literals.
    JobSnapshot  — Immutable snapshot of a job's state (safe to pass across threads).
    JobUpdate    — Mutable update object returned by matcher callbacks.
    Job          — Base class for schedulable units of work.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

JobStatus = Literal["pending", "running", "done", "failed", "cancelled"]

PENDING:   JobStatus = "pending"
RUNNING:   JobStatus = "running"
DONE:      JobStatus = "done"
FAILED:    JobStatus = "failed"
CANCELLED: JobStatus = "cancelled"

TERMINAL_STATUSES = (DONE, FAILED, CANCELLED)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobSnapshot:
    """Immutable point-in-time snapshot of a job's state.

    Produced by ``Job.snapshot()`` and passed to all ``on()`` and ``match()``
    callbacks.  Safe to read from any thread without holding any lock.
    """
    name:        str
    status:      JobStatus
    exit_code:   Optional[int]
    progress:    Optional[float]        # 0–100, or None if not tracked
    duration:    Optional[float]        # wall-clock seconds, or None if not started
    tags:        List[str]
    priority:    int
    depends_on:  List[str]
    log_path:    Optional[Path]
    custom_data: Dict[str, Any]


@dataclass
class JobUpdate:
    """Returned by a matcher callback to update a job's recorded state.

    All fields are optional.  Only non-None fields are applied.
    ``custom_data`` is shallow-merged (new keys overwrite existing ones).
    """
    status:      Optional[Literal["done", "failed"]] = None
    progress:    Optional[float] = None             # 0–100
    custom_data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Job base class
# ---------------------------------------------------------------------------

class Job:
    """A schedulable unit of work.

    Users should not hold references to this object after submission.
    All interaction happens through the Scheduler using the job's name.

    Subclasses must implement ``_execute()``.

    Attributes:
        name:        Unique identifier used by the Scheduler.
        cmd:         Shell command string (or descriptive label for custom jobs).
        cwd:         Working directory; ``None`` inherits from the environment.
        env:         Extra environment variables merged with ``os.environ``.
        priority:    Higher value = dispatched first.  Default ``0``.
        depends_on:  List of job names that must be DONE before this job can start.
        tags:        Free-form labels for grouping/filtering.
        resources:   Resource requirements, e.g. ``{"local": 2}``.
        timeout:     Max wall-clock seconds before the job is killed.  ``None`` = unlimited.
        max_retries: Number of automatic retries on failure.  Default ``0``.
    """

    # Subclasses declare their default resource footprint here.
    default_resources: Dict[str, int] = {"local": 1}

    def __init__(
        self,
        name: Optional[str] = None,
        cmd: str = "",
        *,
        cwd:         Optional[str]            = None,
        env:         Optional[Dict[str, str]] = None,
        priority:    int                      = 0,
        depends_on:  Optional[List[str]]      = None,
        tags:        Optional[List[str]]      = None,
        resources:   Optional[Dict[str, int]] = None,
        timeout:     Optional[float]          = None,
        max_retries: int                      = 0,
        cache_output_len: int                 = 10_000,
    ) -> None:
        if not name:
            name = f"{type(self).__name__}_{uuid.uuid4().hex[:8]}"
        if not cmd:
            cmd = f"<{type(self).__name__}>"

        # --- declaration fields ---
        self.name        = name
        self.cmd         = cmd
        self.cwd         = cwd
        self.env         = env
        self.priority    = priority
        self.depends_on: List[str]      = list(depends_on) if depends_on else []
        self.tags:  List[str]       = list(tags) if tags else []
        self.resources:  Dict[str, int] = (
            dict(resources) if resources is not None else dict(self.default_resources)
        )
        self.timeout     = timeout
        self.max_retries = max_retries

        # --- runtime state (managed exclusively by Scheduler) ---
        self.status:      JobStatus         = PENDING
        self.exit_code:   Optional[int]     = None
        self.progress:    Optional[float]   = None
        self.log_path:    Optional[Path]    = None
        self.retry_count: int               = 0
        self.custom_data: Dict[str, Any]    = {}

        # --- timing ---
        self._start_time: Optional[float] = None
        self._end_time:   Optional[float] = None

        # --- synchronisation primitives ---
        self._lock            = threading.Lock()
        self._finished_event  = threading.Event()
        self._proc_ready      = threading.Event()

        # --- process handle (set inside _execute by subclasses) ---
        self._proc: Optional[subprocess.Popen] = None

        # --- output buffer ---
        self._output_buffer: deque[str] = deque(maxlen=cache_output_len)
        self._output_listener: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # Execution lifecycle — subclasses override
    # ------------------------------------------------------------------

    def _execute(self, log_file=None) -> None:
        """Run the job.  Subclasses **must** override this method.

        Responsibilities:
        - Assign ``self._proc`` and call ``self._proc_ready.set()`` if a
          subprocess is spawned.
        - Stream output via ``self._emit_line(line)``.
        - Write each line to *log_file* if it is not ``None``.
        - Set ``self.exit_code`` and ``self.status`` (DONE / FAILED) before
          returning.

        The Scheduler handles timing, resource release, retries, and errors.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _execute().")

    def _reset_for_retry(self) -> None:
        """Reset runtime state so the job can be re-executed.  Called by Scheduler."""
        self._proc_ready.clear()
        self._finished_event.clear()
        self._proc    = None
        self.exit_code = None
        self.progress  = None
        self._end_time = None
        self.status    = RUNNING   # stays RUNNING across retries
        with self._lock:
            self._output_buffer.clear()

    # ------------------------------------------------------------------
    # Output streaming
    # ------------------------------------------------------------------

    def _emit_line(self, line: str) -> None:
        """Buffer a line of output.  Called by ``_execute()`` implementations.

        The Scheduler registers a listener via ``_set_output_listener`` so it
        can forward lines to registered matchers without the Job needing to
        know about the Scheduler.
        """
        with self._lock:
            self._output_buffer.append(line)
            listener = self._output_listener

        if listener is not None:
            try:
                listener(line)
            except Exception:
                pass

    def _set_output_listener(self, fn) -> None:
        """Register a single output listener (called by Scheduler)."""
        with self._lock:
            self._output_listener = fn

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    @property
    def duration(self) -> Optional[float]:
        """Elapsed wall-clock seconds; live while running, frozen on finish."""
        if self._start_time is None:
            return None
        end = self._end_time
        return (end if end is not None else time.monotonic()) - self._start_time

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> JobSnapshot:
        """Return an immutable snapshot of the current state."""
        with self._lock:
            return JobSnapshot(
                name        = self.name,
                status      = self.status,
                exit_code   = self.exit_code,
                progress    = self.progress,
                duration    = self.duration,
                tags        = list(self.tags),
                priority    = self.priority,
                depends_on  = list(self.depends_on),
                log_path    = self.log_path,
                custom_data = dict(self.custom_data),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def is_finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def job_type(self) -> str:
        return type(self).__name__

    def tail(self, n: int = 20) -> List[str]:
        """Return the last *n* lines of captured output."""
        with self._lock:
            return list(self._output_buffer)[-n:]

    def __repr__(self) -> str:
        dur  = f", duration={self.duration:.1f}s" if self.duration is not None else ""
        prog = f", progress={self.progress:.0f}%" if self.progress is not None else ""
        tags = f", tags={self.tags}" if self.tags else ""
        return (
            f"{self.job_type}({self.name!r}, status={self.status!r}, "
            f"exit_code={self.exit_code}, priority={self.priority}{dur}{prog}{tags})"
        )
