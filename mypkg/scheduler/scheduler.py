"""
scheduler.py — Thread-pool based job scheduler.

Manages job execution with priority ordering, resource pools,
real-time output matching, and lifecycle event callbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Union

from .job import (
    Job,
    JobSnapshot,
    JobUpdate,
    PENDING, RUNNING, DONE, FAILED, CANCELLED,
)
from .types import Event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_FINISH_EVENTS: frozenset[str] = frozenset({Event.DONE, Event.FAILED, Event.CANCELLED, Event.FINISH, Event.ANY})
_EVENT_TO_STATUS: Dict[Event, str] = {
    Event.DONE:   DONE,
    Event.FAILED:   FAILED,
    Event.CANCELLED: CANCELLED,
}


# ---------------------------------------------------------------------------
# Internal callback records
# ---------------------------------------------------------------------------

class _EventCallback:
    """Registered lifecycle event callback."""
    __slots__ = ("id", "event", "callback", "job_filter", "tag_filter", "once")

    def __init__(self, id: str, event: Event, callback: Callable[[Optional[JobSnapshot]], None],
                 job_filter: Optional[str], tag_filter: Optional[str], once: bool):
        self.id         = id
        self.event      = event
        self.callback   = callback
        self.job_filter = job_filter
        self.tag_filter = tag_filter
        self.once       = once


class _MatcherCallback:
    """Registered realtime output matcher."""
    __slots__ = ("id", "job_name", "fn", "once")

    def __init__(self, id: str, job_name: str,
                 fn: Callable[[str, JobSnapshot], Optional[JobUpdate]], once: bool):
        self.id       = id
        self.job_name = job_name
        self.fn       = fn
        self.once     = once


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    """Thread-pool based job scheduler with resource management.

    All interaction with jobs after submission happens through this class
    using the job's name string.  Users should not hold Job references.

    Example::

        sched = Scheduler(resources={"local": 4}, log_dir="./logs")
        sched.submit("build", cmd="make -j4", tags=["ci"])
        sched.on("fail", lambda snap: print(f"{snap.name} failed: exit={snap.exit_code}"))
        sched.run()

    Args:
        resources:     Capacity for each resource pool.
                       Default: ``{"local": os.cpu_count()}``.
        log_dir:       Directory for per-job stdout/stderr logs.  ``None`` = no files.
        poll_interval: Seconds between scheduler loop ticks.  Default ``0.5``.
    """

    def __init__(
        self,
        resources:     Optional[Dict[str, int]]    = None,
        log_dir:       Optional[Union[str, Path]]  = None,
        poll_interval: float                       = 0.5,
    ) -> None:
        cpu = os.cpu_count() or 1
        self._capacity: Dict[str, int] = dict(resources) if resources else {"local": cpu}
        self._used:     Dict[str, int] = {k: 0 for k in self._capacity}

        if self._capacity.get("local", 0) > cpu:
            logger.warning(
                "Scheduler local=%d exceeds os.cpu_count()=%d",
                self._capacity["local"], cpu,
            )

        self._log_dir       = Path(log_dir) if log_dir else None
        self._poll_interval = poll_interval

        # job registry
        self._jobs:     Dict[str, Job] = {}   # name → Job
        self._job_lock  = threading.Lock()    # guards _jobs, _used, job.status
        self._cond      = threading.Condition(self._job_lock) # For efficient wait()

        # background scheduler thread
        self._bg_thread:  Optional[threading.Thread] = None
        self._stop_event  = threading.Event()
        self._paused      = False

        # callbacks
        self._cb_lock:    threading.Lock         = threading.Lock()
        self._event_cbs:  List[_EventCallback]   = []
        self._matcher_cbs: List[_MatcherCallback] = []
        self._active_workers: int = 0

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Scheduler":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.stop(force=True)
            # Returning None allows the exception to propagate normally.
        else:
            self.wait()

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    def submit(
        self,
        name:        str,
        cmd:         str,
        *,
        cwd:         Optional[str]            = None,
        env:         Optional[Dict[str, str]] = None,
        priority:    int                      = 0,
        depends_on:  Optional[List[str]]      = None,
        tags:        Optional[List[str]]      = None,
        resources:   Optional[Dict[str, int]] = None,
        timeout:     Optional[float]          = None,
        max_retries: int                      = 0,
    ) -> str:
        """Create and enqueue a shell command job.

        Returns the job *name* for use with all other Scheduler methods.
        Raises ``ValueError`` on duplicate names.
        """
        from .cmd_job import CmdJob  # local import to avoid circular dependency

        job = CmdJob(
            name        = name,
            cmd         = cmd,
            cwd         = cwd,
            env         = env,
            priority    = priority,
            depends_on  = depends_on,
            tags        = tags,
            resources   = resources,
            timeout     = timeout,
            max_retries = max_retries,
        )
        self._register(job)
        return name

    def submit_job(self, job: Job) -> str:
        """Enqueue a pre-built Job instance (for custom Job subclasses).

        Returns the job *name*.
        Raises ``ValueError`` on duplicate names.
        """
        self._register(job)
        return job.name

    def _register(self, job: Job) -> None:
        with self._job_lock:
            if job.name in self._jobs:
                raise ValueError(f"Duplicate job name: {job.name!r}")
            self._jobs[job.name] = job
            # attach output listener so matchers receive lines
            job._set_output_listener(lambda line, j=job: self._on_output(j, line))

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Blocking: execute all jobs and return when every job has finished."""
        self._stop_event.clear()
        self._execute_loop()

    def start(self) -> None:
        """Non-blocking: launch the scheduler loop in a background daemon thread."""
        if self._bg_thread and self._bg_thread.is_alive():
            raise RuntimeError("Scheduler is already running.")
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._execute_loop, daemon=True, name="scheduler-loop"
        )
        self._bg_thread.start()

    def pause(self) -> None:
        """Pause dispatching.  Jobs currently running are not affected."""
        with self._job_lock:
            self._paused = True

    def resume(self) -> None:
        """Resume dispatching pending jobs."""
        with self._job_lock:
            self._paused = False

    def stop(self, *, force: bool = False) -> None:
        """Stop the scheduler.

        Args:
            force: If ``True``, kill all running jobs immediately.
                   If ``False`` (default), wait for running jobs to finish
                   naturally but do not dispatch any new ones.
        """
        self._stop_event.set()
        if force:
            with self._job_lock:
                running = [j for j in self._jobs.values() if j.status == RUNNING]
            for job in running:
                try:
                    job.kill()
                except Exception:
                    pass
        if self._bg_thread:
            self._bg_thread.join()

    def wait(
        self,
        name:    Optional[str]   = None,
        *,
        tag:     Optional[str]   = None,
        timeout: Optional[float] = None,
    ) -> bool:
        """Block until target jobs reach a terminal state.

        Args:
            name:    Wait for a single job.  ``None`` = wait for all jobs.
            tag:     Wait for all jobs with this tag.  Ignored when *name* is given.
            timeout: Max seconds to wait.  ``None`` = wait forever.

        Returns:
            ``True`` if all target jobs finished within the timeout.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None

        def _targets_finished() -> bool:
            if name is not None:
                if name not in self._jobs:
                    raise KeyError(f"Job not found: {name!r}")
                return self._jobs[name].is_finished
            if tag is not None:
                return all(j.is_finished for j in self._jobs.values() if tag in j.tags)
            if not self._jobs:
                return True
            return all(j.is_finished for j in self._jobs.values())

        with self._cond:
            while not _targets_finished():
                rem = None
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        return False
                self._cond.wait(timeout=rem)
            return True

    # ------------------------------------------------------------------
    # Interactive control
    # ------------------------------------------------------------------

    def cancel(self, name: str) -> None:
        """Cancel a pending job.  Raises ``RuntimeError`` if not pending."""
        with self._job_lock:
            job = self._get_job(name)
            if job.status != PENDING:
                hint = " Use kill()/interrupt() for running jobs." if job.status == RUNNING else ""
                raise RuntimeError(
                    f"Cannot cancel {name!r}: status is {job.status!r} (must be pending).{hint}"
                )
            job.status = CANCELLED
            self._cond.notify_all()
        self._fire_event(Event.CANCELLED, job)

    def interrupt(self, name: str) -> None:
        """Send SIGINT to a running job."""
        self._get_job(name).interrupt()

    def kill(self, name: str) -> None:
        """Forcefully terminate a running job (SIGKILL / taskkill /F)."""
        self._get_job(name).kill()

    def send_input(self, name: str, text: str) -> None:
        """Write *text* to the stdin of a running job."""
        self._get_job(name).send_input(text)

    def set_priority(
        self,
        name:     str,
        priority: int,
        *,
        by_tag:   bool = False,
    ) -> None:
        """Change the dispatch priority of one or more pending jobs.

        Args:
            name:   Job name, or tag name when *by_tag* is ``True``.
            by_tag: If ``True``, update all pending jobs with the given tag.
        """
        with self._job_lock:
            if by_tag:
                targets = [j for j in self._jobs.values()
                           if name in j.tags and j.status == PENDING]
            else:
                job = self._get_job(name)
                if job.status != PENDING:
                    raise RuntimeError(
                        f"Cannot change priority of {name!r}: "
                        f"status is {job.status!r} (must be pending)."
                    )
                targets = [job]
            for j in targets:
                j.priority = priority

    # ------------------------------------------------------------------
    # Callbacks: lifecycle events
    # ------------------------------------------------------------------

    def on(
        self,
        event:    Event,
        callback: Callable[[Optional[JobSnapshot]], None],
        *,
        job:      Optional[str] = None,
        tag:      Optional[str] = None,
        once:     bool          = False,
    ) -> str:
        """Register a lifecycle event callback.

        Args:
            event:    An ``Event`` Enum member (e.g., ``Event.DONE``, ``Event.UPDATE``).
            callback: Called with a ``JobSnapshot`` when the event fires (may be ``None`` for ALL_FINISHED).
            job:      Only fire for this job name.  ``None`` = all jobs.
            tag:      Only fire for jobs carrying this tag.  ``None`` = all jobs.
            once:     Auto-remove after the first invocation.

        Returns:
            A callback ID that can be passed to ``off()`` to deregister.
        """
        cb_id = uuid.uuid4().hex
        rec   = _EventCallback(cb_id, event, callback, job, tag, once)
        with self._cb_lock:
            self._event_cbs.append(rec)
        return cb_id

    def off(self, callback_id: str) -> None:
        """Remove a callback registered with ``on()`` or ``match()``."""
        with self._cb_lock:
            self._event_cbs   = [c for c in self._event_cbs   if c.id != callback_id]
            self._matcher_cbs = [c for c in self._matcher_cbs if c.id != callback_id]

    # ------------------------------------------------------------------
    # Callbacks: output matchers
    # ------------------------------------------------------------------

    def match(
        self,
        name: str,
        fn:   Callable[[str, JobSnapshot], Optional[JobUpdate]],
        *,
        once: bool = False,
    ) -> str:
        """Register a realtime output matcher for a job.

        *fn* is called for every output line while the job is running.
        It receives the line string and a ``JobSnapshot``, and may return a
        ``JobUpdate`` to update the job's recorded state (status, progress,
        custom_data).  Return ``None`` to make no change.

        Args:
            name: Job name.
            fn:   ``(line: str, snap: JobSnapshot) -> Optional[JobUpdate]``
            once: Auto-remove after the first non-None return value.

        Returns:
            A callback ID that can be passed to ``off()``.

        Example::

            import re

            def parse(line, snap):
                if m := re.search(r"(\\d+)%", line):
                    return JobUpdate(progress=float(m.group(1)))
                if "PASSED" in line:
                    return JobUpdate(status="done")
                if "FAILED" in line:
                    return JobUpdate(status="failed")

            sched.match("sim", parse)
        """
        cb_id = uuid.uuid4().hex
        rec   = _MatcherCallback(cb_id, name, fn, once)
        with self._cb_lock:
            self._matcher_cbs.append(rec)
        return cb_id

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, name: str) -> JobSnapshot:
        """Return a snapshot of the named job.  Raises ``KeyError`` if absent."""
        return self._get_job(name).snapshot()

    @property
    def jobs(self) -> List[JobSnapshot]:
        """Snapshot of all submitted jobs."""
        with self._job_lock:
            return [j.snapshot() for j in self._jobs.values()]

    def stdout(self, name: str, lines: int = 100) -> List[str]:
        """Return the last *lines* of captured output for the job."""
        return self._get_job(name).tail(lines)

    def jobs_by_tag(self, tag: str) -> List[JobSnapshot]:
        """Snapshots of all jobs carrying *tag*."""
        with self._job_lock:
            return [j.snapshot() for j in self._jobs.values() if tag in j.tags]

    @property
    def is_paused(self) -> bool:
        with self._job_lock:
            return self._paused

    @property
    def is_complete(self) -> bool:
        """True when every submitted job has reached a terminal state."""
        with self._job_lock:
            if not self._jobs:
                return False
            return all(j.is_finished for j in self._jobs.values())

    @property
    def is_running(self) -> bool:
        """True while the background scheduler thread is active."""
        return self._bg_thread is not None and self._bg_thread.is_alive()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def status(self) -> str:
        """Return a compact status table of all jobs."""
        lines = []
        state = " (PAUSED)" if self.is_paused else ""
        hdr   = f"{'Name':<20} {'Type':<12} {'Status':<12} {'Pri':<5} {'Progress':<10}{state}"
        lines.append(hdr)
        lines.append("─" * len(hdr))
        with self._job_lock:
            jobs_info = [(j.job_type, j.snapshot()) for j in self._jobs.values()]
        for job_type, j in jobs_info:
            prog = f"{j.progress:.0f}%" if j.progress is not None else "-"
            lines.append(
                f"{j.name:<20} {job_type:<12} {j.status:<12} {j.priority:<5} {prog:<10}"
            )
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a detailed summary table of all jobs."""
        lines = []
        state = " (PAUSED)" if self.is_paused else ""
        hdr   = (
            f"{'Name':<20} {'Type':<12} {'Status':<10} {'Exit':<6}"
            f" {'Duration':<12} {'Retries':<8} {'Progress':<10}{state}"
        )
        lines.append(hdr)
        lines.append("-" * len(hdr))
        with self._job_lock:
            jobs_info = [(j.job_type, j.retry_count, j.snapshot()) for j in self._jobs.values()]
        for job_type, retry_count, j in jobs_info:
            dur     = f"{j.duration:.1f}s" if j.duration is not None else "-"
            ec      = str(j.exit_code) if j.exit_code is not None else "-"
            prog    = f"{j.progress:.0f}%" if j.progress is not None else "-"
            retries = str(retry_count) if retry_count > 0 else "-"
            lines.append(
                f"{j.name:<20} {job_type:<12} {j.status:<10} {ec:<6}"
                f" {dur:<12} {retries:<8} {prog:<10}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: scheduling loop
    # ------------------------------------------------------------------

    def _execute_loop(self) -> None:
        """Core scheduler loop: dispatch ready jobs to the thread pool."""
        max_workers = min(100, max(1, sum(self._capacity.values())))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while not self._stop_event.is_set():
                to_cancel = []
                all_finished = False
                with self._job_lock:
                    ready, to_cancel = self._get_ready_jobs()
                    for job in ready:
                        if self._can_acquire(job):
                            self._acquire_resources(job)
                            job.status      = RUNNING
                            job._start_time = time.monotonic()
                            self._active_workers += 1
                            pool.submit(self._run_job, job)
                    
                    for job in to_cancel:
                        job.status = CANCELLED
                        
                    if to_cancel:
                        self._cond.notify_all()
                
                # Fire events outside the lock
                for job in to_cancel:
                    self._fire_event(Event.CANCELLED, job)

                with self._job_lock:
                    all_finished = (
                        bool(self._jobs)
                        and all(j.is_finished for j in self._jobs.values())
                        and self._active_workers == 0
                    )

                if all_finished:
                    # Fire outside _job_lock to avoid callback re-entrancy deadlocks.
                    self._fire_event(Event.ALL_FINISHED, None)
                    with self._job_lock:
                        if (
                            self._jobs
                            and all(j.is_finished for j in self._jobs.values())
                            and self._active_workers == 0
                        ):
                            break

                time.sleep(self._poll_interval)

    def _get_ready_jobs(self) -> tuple[List[Job], List[Job]]:
        """Return (ready_jobs, unrunnable_jobs). 
        
        Ready jobs are pending and have all dependencies DONE.
        Unrunnable jobs are pending but have at least one dependency FAILED,
        CANCELLED, missing, or part of an unresolved pending cycle.
        Caller must hold ``_job_lock``.
        """
        if self._paused or self._stop_event.is_set():
            return [], []
        ready = []
        to_cancel = []
        blocked_pending = []
        for j in self._jobs.values():
            if j.status == PENDING:
                deps_ok = True
                dep_failed = False
                for dep_name in j.depends_on:
                    dep = self._jobs.get(dep_name)
                    if not dep or dep.status != DONE:
                        deps_ok = False
                        if not dep or dep.status in (FAILED, CANCELLED):
                            dep_failed = True
                if deps_ok:
                    ready.append(j)
                elif dep_failed:
                    to_cancel.append(j)
                else:
                    blocked_pending.append(j)

        # Deadlock breaker: pending-only dependency cycles can never progress.
        if not ready and blocked_pending:
            cycle_names = self._find_pending_cycle_names(blocked_pending)
            if cycle_names:
                for j in blocked_pending:
                    if j.name in cycle_names and j not in to_cancel:
                        to_cancel.append(j)
                     
        ready.sort(key=lambda j: j.priority, reverse=True)
        return ready, to_cancel

    def _find_pending_cycle_names(self, pending_jobs: List[Job]) -> set[str]:
        """Return names of pending jobs that participate in dependency cycles."""
        pending_map = {j.name: j for j in pending_jobs}
        graph: Dict[str, List[str]] = {
            name: [d for d in job.depends_on if d in pending_map]
            for name, job in pending_map.items()
        }
        color: Dict[str, int] = {name: 0 for name in graph}  # 0=unseen, 1=visiting, 2=done
        path: List[str] = []
        in_path: set[str] = set()
        in_cycle: set[str] = set()

        for start in graph:
            if color[start] != 0:
                continue

            # Iterative DFS frame: (node, next-neighbor-index)
            frames: List[tuple[str, int]] = [(start, 0)]
            color[start] = 1
            path.append(start)
            in_path.add(start)

            while frames:
                node, idx = frames[-1]
                nbrs = graph.get(node, [])
                if idx >= len(nbrs):
                    frames.pop()
                    if path and path[-1] == node:
                        path.pop()
                    in_path.discard(node)
                    color[node] = 2
                    continue

                nxt = nbrs[idx]
                frames[-1] = (node, idx + 1)
                c = color.get(nxt, 0)
                if c == 0:
                    color[nxt] = 1
                    frames.append((nxt, 0))
                    path.append(nxt)
                    in_path.add(nxt)
                elif c == 1 and nxt in in_path:
                    rev_idx = len(path) - 1 - path[::-1].index(nxt)
                    in_cycle.update(path[rev_idx:])
        return in_cycle

    def _can_acquire(self, job: Job) -> bool:
        """Return True if all resources required by *job* are available."""
        for res, amount in job.resources.items():
            if self._used.get(res, 0) + amount > self._capacity.get(res, 0):
                return False
        return True

    def _acquire_resources(self, job: Job) -> None:
        """Reserve resources for *job*.  Caller must hold ``_job_lock``."""
        for res, amount in job.resources.items():
            self._used[res] = self._used.get(res, 0) + amount

    def _release_resources(self, job: Job) -> None:
        """Release resources held by *job*."""
        with self._job_lock:
            for res, amount in job.resources.items():
                self._used[res] = max(0, self._used.get(res, 0) - amount)

    # ------------------------------------------------------------------
    # Internal: job execution (runs in worker thread)
    # ------------------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        """Execute a single job in a worker thread, with retry support."""
        if job._start_time is None:
            job._start_time = time.monotonic()

        log_file = None
        attempt  = 0

        self._fire_event(Event.START, job)

        while True:
            timer = None
            try:
                # Open log file once; retries append to the same file.
                if self._log_dir and log_file is None:
                    self._log_dir.mkdir(parents=True, exist_ok=True)
                    job.log_path = self._log_dir / f"{job.name}.log"
                    log_file     = open(job.log_path, "w", encoding="utf-8")

                # Timeout watchdog
                if job.timeout and job.timeout > 0:
                    def _on_timeout(j=job):
                        if j.status == RUNNING:
                            j._emit_line(f"[scheduler] timeout ({j.timeout}s) — killing job")
                            try:
                                j.kill()
                            except Exception:
                                pass
                    timer = threading.Timer(job.timeout, _on_timeout)
                    timer.daemon = True
                    timer.start()

                if attempt > 0:
                    job._emit_line(f"[scheduler] retry {attempt}/{job.max_retries}")

                job._execute(log_file)

            except Exception as exc:
                logger.error("Job %r crashed: %s", job.name, exc)
                with self._job_lock:
                    job.status    = FAILED
                    job.exit_code = -1
                job._emit_line(f"[scheduler] internal error: {exc}")

            finally:
                if timer is not None:
                    timer.cancel()

            # Retry decision
            should_retry = (
                job.status == FAILED
                and attempt < job.max_retries
            )
            if not should_retry:
                break

            attempt         += 1
            job.retry_count  = attempt
            job._reset_for_retry()
            time.sleep(0.1)

        # Finalise
        job._end_time = time.monotonic()
        if log_file:
            log_file.close()
        job._finished_event.set()
        self._release_resources(job)

        # Fire lifecycle event
        event = {DONE: Event.DONE, FAILED: Event.FAILED, CANCELLED: Event.CANCELLED}.get(job.status)
        if event:
            self._fire_event(event, job)

        with self._job_lock:
            self._active_workers = max(0, self._active_workers - 1)
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # Internal: output matcher dispatch
    # ------------------------------------------------------------------

    def _on_output(self, job: Job, line: str) -> None:
        """Called for every output line of *job*.  Dispatches to matchers."""
        with self._cb_lock:
            matchers = [m for m in self._matcher_cbs if m.job_name == job.name]

        to_remove = []
        for m in matchers:
            try:
                update = m.fn(line, job.snapshot())
            except Exception as exc:
                logger.debug("Matcher %r raised: %s", m.id, exc)
                continue

            if update is None:
                continue

            normalized_status: Optional[str]
            if update.status == "done":
                normalized_status = "done"
            elif update.status == "failed":
                normalized_status = "failed"
            else:
                normalized_status = None

            should_kill = False
            with self._job_lock:
                if normalized_status in ("done", "failed") and job.status == RUNNING:
                    should_kill = True

            if should_kill:
                try:
                    # Terminate process while it is still in RUNNING state.
                    job.kill()
                except Exception:
                    pass

            # Apply the update
            changed = False
            with self._job_lock:
                if normalized_status == "done":
                    if job.status != DONE:
                        job.status = DONE
                        changed = True
                    if job.exit_code is None:
                        job.exit_code = 0
                elif normalized_status == "failed":
                    if job.status != FAILED:
                        job.status = FAILED
                        changed = True
                    if job.exit_code is None:
                        job.exit_code = 1
                if update.progress is not None:
                    job.progress = update.progress
                    changed = True
                if update.custom_data is not None:
                    job.custom_data = {**job.custom_data, **update.custom_data}
                    changed = True

                if normalized_status in ("done", "failed"):
                    self._cond.notify_all()

            if changed:
                self._fire_event(Event.UPDATE, job)

            if m.once:
                to_remove.append(m.id)

        if to_remove:
            with self._cb_lock:
                self._matcher_cbs = [m for m in self._matcher_cbs if m.id not in to_remove]

    # ------------------------------------------------------------------
    # Internal: event dispatch
    # ------------------------------------------------------------------

    def _fire_event(self, event: Event, job: Optional[Job] = None) -> None:
        """Invoke all registered callbacks matching *event* and *job*."""
        snap = job.snapshot() if job else None

        with self._cb_lock:
            candidates = list(self._event_cbs)

        to_remove = []
        for cb in candidates:
            # Filter by event
            if cb.event not in (Event.ANY, Event.FINISH, event):
                continue
            if cb.event == Event.FINISH and event not in (Event.DONE, Event.FAILED, Event.CANCELLED):
                continue
            
            # Filter by job name or tag (skip if event is completely global like ALL_FINISHED)
            if job is not None:
                if cb.job_filter and cb.job_filter != job.name:
                    continue
                if cb.tag_filter and cb.tag_filter not in job.tags:
                    continue
            else:
                if cb.job_filter or cb.tag_filter:
                    continue

            try:
                cb.callback(snap)
            except Exception as exc:
                logger.debug("Event callback %r raised: %s", cb.id, exc)

            if cb.once:
                to_remove.append(cb.id)

        if to_remove:
            with self._cb_lock:
                self._event_cbs = [c for c in self._event_cbs if c.id not in to_remove]

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _get_job(self, name: str) -> Job:
        """Return the internal Job object.  Raises ``KeyError`` if absent."""
        try:
            return self._jobs[name]
        except KeyError:
            raise KeyError(f"No job named {name!r}")
