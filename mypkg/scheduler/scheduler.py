"""
Scheduler — Thread-pool based job scheduler with resource management.

Manages job execution with priority ordering, dependency resolution,
resource pools, and real-time stdout streaming.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from mypkg.scheduler.job import (
    Job, CmdJob, PENDING, RUNNING, DONE, FAILED, CANCELLED,
)

logger = logging.getLogger(__name__)


class Scheduler:
    """IO-bound job scheduler backed by a thread pool.

    Usage::

        sched = Scheduler(resources={"local": 4}, log_dir="./logs")
        sched.submit(CmdJob("a", cmd="echo hello"))
        sched.start()         # non-blocking
        sched.follow("a")     # attach to live output
        sched.summary()

    Parameters:
        resources:  Capacity for each resource pool.
                    Default: ``{"local": os.cpu_count()}``.
                    If ``resources["local"]`` exceeds ``os.cpu_count()``,
                    a warning is emitted.
        log_dir:    Directory for auto-captured stdout/stderr logs.
                    ``None`` = no log files.
        poll_interval: Seconds between scheduler loop ticks (default 0.5).
    """

    def __init__(
        self,
        resources: Optional[Dict[str, int]] = None,
        log_dir: Optional[Union[str, Path]] = None,
        poll_interval: float = 0.5,
    ) -> None:
        cpu = os.cpu_count() or 4
        self._capacity: Dict[str, int] = dict(resources) if resources else {"local": cpu}
        self._used: Dict[str, int] = {k: 0 for k in self._capacity}

        # Warn if local exceeds physical CPUs
        if self._capacity.get("local", 0) > cpu:
            logger.warning(
                "Scheduler local=%d exceeds os.cpu_count()=%d",
                self._capacity["local"],
                cpu,
            )

        self._log_dir: Optional[Path] = Path(log_dir) if log_dir else None
        self._poll_interval = poll_interval

        self._jobs: List[Job] = []
        self._lock = threading.Lock()  # protects _jobs status + _used
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ----- public: job management -----

    def submit(self, *jobs: Job) -> None:
        """Add one or more jobs to the scheduler queue.

        Raises ``ValueError`` on duplicate names or if a dependency is not
        already in the queue (or being submitted in the same call).
        """
        # Collect names of all known + incoming jobs
        known = {j.name for j in self._jobs}
        incoming = {j.name for j in jobs}

        for job in jobs:
            if job.name in known:
                raise ValueError(f"Duplicate job name: {job.name!r}")
            for dep in job.depends_on:
                if dep.name not in known and dep.name not in incoming:
                    raise ValueError(
                        f"Job {job.name!r} depends on {dep.name!r}, "
                        f"which is not submitted."
                    )
            known.add(job.name)

        self._jobs.extend(jobs)

    def get(self, name: str) -> Job:
        """Return the Job with the given *name*.

        Raises ``KeyError`` if not found.
        """
        for j in self._jobs:
            if j.name == name:
                return j
        raise KeyError(f"No job named {name!r}")

    @property
    def jobs(self) -> List[Job]:
        """All submitted jobs (read-only snapshot)."""
        return list(self._jobs)

    # ----- public: status filter properties -----

    @property
    def pending(self) -> List[Job]:
        """All jobs with status ``"pending"``."""
        return [j for j in self._jobs if j.status == PENDING]

    @property
    def running(self) -> List[Job]:
        """All jobs with status ``"running"``."""
        return [j for j in self._jobs if j.status == RUNNING]

    @property
    def done(self) -> List[Job]:
        """All jobs with status ``"done"``."""
        return [j for j in self._jobs if j.status == DONE]

    @property
    def failed(self) -> List[Job]:
        """All jobs with status ``"failed"`` or ``"cancelled"``."""
        return [j for j in self._jobs if j.status in (FAILED, CANCELLED)]

    # ----- public: execution -----

    def run(self) -> None:
        """Blocking: execute all jobs and return when finished."""
        self._stop_event.clear()
        self._execute_loop()

    def start(self) -> None:
        """Non-blocking: launch the scheduler loop in a daemon thread."""
        if self._bg_thread and self._bg_thread.is_alive():
            raise RuntimeError("Scheduler is already running.")
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._execute_loop, daemon=True, name="scheduler-loop"
        )
        self._bg_thread.start()

    def wait(self) -> None:
        """Block until the background scheduler finishes."""
        if self._bg_thread:
            self._bg_thread.join()

    def stop(self) -> None:
        """Signal the scheduler to stop after current jobs finish."""
        self._stop_event.set()
        if self._bg_thread:
            self._bg_thread.join()

    # ----- public: interactive control -----

    def cancel(self, name: str) -> None:
        """Cancel a pending job.

        Raises ``RuntimeError`` if the job is not pending.
        """
        job = self.get(name)
        if job.status != PENDING:
            raise RuntimeError(
                f"Cannot cancel job {name!r}: status is {job.status!r} (must be pending)."
            )
        with self._lock:
            job.status = CANCELLED

    def kill(self, name: str) -> None:
        """Kill a running job by terminating its process.

        Delegates to ``job.kill()`` which subclasses can override.
        Raises ``RuntimeError`` if the job is not running.
        """
        job = self.get(name)
        job.kill()  # job.kill() checks status internally

    def set_priority(self, name: str, priority: int) -> None:
        """Change the priority of a pending job.

        Takes effect on the next scheduler poll cycle.
        Raises ``RuntimeError`` if the job is not pending.
        """
        job = self.get(name)
        if job.status != PENDING:
            raise RuntimeError(
                f"Cannot change priority of job {name!r}: "
                f"status is {job.status!r} (must be pending)."
            )
        job.priority = priority

    def follow(self, name: str, n: int = 20) -> None:
        """Attach to a job's output stream.

        Prints the last *n* lines of history, then streams new output
        in real-time until the job finishes or ``KeyboardInterrupt``.
        """
        job = self.get(name)

        # Replay recent history
        for line in job.tail(n):
            print(f"[{name}] {line}")

        if job.is_finished:
            print(f"--- {name}已結束 (status={job.status}) ---")
            return

        # Stream new output
        stop = threading.Event()

        def _printer(line: str) -> None:
            print(f"[{name}] {line}")

        job.on_output(_printer)
        try:
            while not job.is_finished and not stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            job.remove_output(_printer)
            print(f"\n--- detached from {name} ---")

    def actions(self, name: str) -> Dict:
        """Print and return available actions for a job.

        Returns the actions dict ``{name: (description, callable)}``.
        """
        job = self.get(name)
        acts = job.actions()
        if not acts:
            print(f"{name} ({job.job_type}) — 沒有額外操作")
        else:
            print(f"{name} ({job.job_type}) 可用操作:")
            for act_name, (desc, _) in acts.items():
                print(f"  {act_name:<16} — {desc}")
        return acts

    def action(self, name: str, action_name: str) -> None:
        """Execute a specific action on a job.

        Raises ``KeyError`` if the action is not found.
        """
        job = self.get(name)
        acts = job.actions()
        if action_name not in acts:
            raise KeyError(
                f"Action {action_name!r} not found for job {name!r}. "
                f"Available: {list(acts.keys())}"
            )
        _, fn = acts[action_name]
        fn()

    # ----- public: reporting -----

    def status(self) -> str:
        """Return a compact status table. Also prints to stdout.

        Lighter than ``summary()`` — shows type, name, status, priority.
        """
        lines = []
        hdr = f"{'Name':<20} {'Type':<10} {'Status':<12} {'Pri':<5}"
        lines.append(hdr)
        lines.append("─" * len(hdr))
        for j in self._jobs:
            lines.append(
                f"{j.name:<20} {j.job_type:<10} {j.status:<12} {j.priority:<5}"
            )
        text = "\n".join(lines)
        print(text)
        return text

    def summary(self) -> str:
        """Return a formatted table of all job statuses.

        Also prints to stdout for convenience.
        """
        lines = []
        hdr = f"{'Name':<20} {'Type':<10} {'Status':<10} {'Exit':<6} {'Duration':<12}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for j in self._jobs:
            dur = f"{j.duration:.1f}s" if j.duration is not None else "-"
            ec = str(j.exit_code) if j.exit_code is not None else "-"
            lines.append(
                f"{j.name:<20} {j.job_type:<10} {j.status:<10} {ec:<6} {dur:<12}"
            )
        text = "\n".join(lines)
        print(text)
        return text

    # ----- internal: scheduling loop -----

    def _execute_loop(self) -> None:
        """Core scheduler loop: dispatch ready jobs to thread pool."""
        max_workers = sum(self._capacity.values())
        with ThreadPoolExecutor(max_workers=max(max_workers, 1)) as pool:
            while True:
                if self._stop_event.is_set():
                    break

                with self._lock:
                    ready = self._get_ready_jobs()
                    for job in ready:
                        self._acquire_resources(job)
                        job.status = RUNNING
                        pool.submit(self._run_job, job)

                # Check if all done
                with self._lock:
                    all_finished = all(j.is_finished for j in self._jobs)
                if all_finished:
                    break

                time.sleep(self._poll_interval)

    def _get_ready_jobs(self) -> List[Job]:
        """Return pending jobs whose dependencies are met and resources
        are available, sorted by priority (descending)."""
        ready = []
        for job in self._jobs:
            if job.status != PENDING:
                continue
            # Check dependencies
            deps_finished = all(dep.is_finished for dep in job.depends_on)
            if not deps_finished:
                continue
            # Check failed / cancelled dependencies — fail this job too
            if any(dep.status in (FAILED, CANCELLED) for dep in job.depends_on):
                job.status = FAILED
                job.exit_code = -1
                continue
            # Check resources
            if not self._can_acquire(job):
                continue
            ready.append(job)
        # Higher priority first
        ready.sort(key=lambda j: j.priority, reverse=True)
        return ready

    def _can_acquire(self, job: Job) -> bool:
        for res, amount in job.resources.items():
            cap = self._capacity.get(res, 0)
            used = self._used.get(res, 0)
            if used + amount > cap:
                return False
        return True

    def _acquire_resources(self, job: Job) -> None:
        for res, amount in job.resources.items():
            self._used[res] = self._used.get(res, 0) + amount

    def _release_resources(self, job: Job) -> None:
        with self._lock:
            for res, amount in job.resources.items():
                self._used[res] = max(0, self._used.get(res, 0) - amount)

    # ----- internal: job execution (runs in worker thread) -----

    def _run_job(self, job: Job) -> None:
        """Execute a single job in a worker thread.

        Delegates actual execution to ``job._execute(log_file)``.
        This wrapper handles timing, log files, timeout, resource release,
        and error handling uniformly for all Job types.
        """
        start_time = time.monotonic()

        # Set up log file
        log_file = None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            job.log_path = self._log_dir / f"{job.name}.log"
            log_file = open(job.log_path, "w", encoding="utf-8")

        # Timeout watchdog
        timer = None
        if job.timeout is not None and job.timeout > 0:
            def _timeout_handler():
                if job.status == RUNNING:
                    job._emit_line(
                        f"[scheduler] timeout ({job.timeout}s) — killing job"
                    )
                    try:
                        job.kill()
                    except Exception:
                        pass
            timer = threading.Timer(job.timeout, _timeout_handler)
            timer.daemon = True
            timer.start()

        try:
            job._execute(log_file)

        except Exception as exc:
            logger.error("Job %r crashed: %s", job.name, exc)
            job.status = FAILED
            job.exit_code = -1
            job._emit_line(f"[scheduler] internal error: {exc}")

        finally:
            if timer is not None:
                timer.cancel()
            job._proc_ready.clear()
            job._proc = None
            job.duration = time.monotonic() - start_time
            if log_file:
                log_file.close()
            self._release_resources(job)
