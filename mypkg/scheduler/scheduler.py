"""
Scheduler — Thread-pool based job scheduler with resource management.

Manages job execution with priority ordering, dependency resolution,
resource pools, and real-time stdout streaming.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Union

from mypkg.scheduler.job import (
    Job, PENDING, RUNNING, DONE, FAILED, CANCELLED,
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
        cpu = os.cpu_count() or 1
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
        self._paused = False

    # ----- public: job management -----

    def submit(self, *jobs: Job) -> None:
        """Add one or more jobs to the scheduler queue.

        Raises ``ValueError`` on duplicate names or if a dependency is not
        already in the queue (or being submitted in the same call).
        """
        with self._lock:
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
        """All jobs with status ``"failed"``."""
        return [j for j in self._jobs if j.status == FAILED]

    @property
    def cancelled(self) -> List[Job]:
        """All jobs with status ``"cancelled"``."""
        return [j for j in self._jobs if j.status == CANCELLED]

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

    def pause(self) -> None:
        """Pause the scheduler. Currently running jobs will finish, but no new jobs will start."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Resume the scheduler. Pending jobs will be dispatched again."""
        with self._lock:
            self._paused = False

    @property
    def is_paused(self) -> bool:
        """Return True if the scheduler is paused."""
        with self._lock:
            return self._paused

    # ----- public: interactive control -----

    def cancel(self, name: str) -> None:
        """Cancel a pending job.

        Raises ``RuntimeError`` if the job is not pending.
        """
        with self._lock:
            job = self.get(name)
            if job.status != PENDING:
                raise RuntimeError(
                    f"Cannot cancel job {name!r}: status is {job.status!r} (must be pending)."
                )
            job.status = CANCELLED

    def interrupt(self, name: str) -> None:
        """Interrupt a running job by sending SIGINT."""
        job = self.get(name)
        job.interrupt()

    def send_input(self, name: str, text: str) -> None:
        """Write text to the stdin of a running job."""
        job = self.get(name)
        job.send_input(text)

    def kill(self, name: str, force: bool = False) -> None:
        """Kill a running job.

        Delegates to ``job.kill(force)`` which subclasses can override.
        Raises ``RuntimeError`` if the job is not running.
        """
        job = self.get(name)
        job.kill(force=force)  # job.kill() checks status internally

    def set_priority(self, name: str, priority: int) -> None:
        """Change the priority of a pending job.

        Takes effect on the next scheduler poll cycle.
        Raises ``RuntimeError`` if the job is not pending.
        """
        with self._lock:
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

        with self._lock:
            is_finished = job.is_finished
            status = job.status

        if is_finished:
            print(f"--- {name} finished (status={status}) ---")
            return

        # Stream new output via hook
        def _printer(line: str, _job: Job) -> None:
            print(f"[{name}] {line}")

        job.add_hook("on_output", _printer)
        try:
            while True:
                with self._lock:
                    if job.is_finished:
                        break
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            job.remove_hook("on_output", _printer)
            print(f"\n--- detached from {name} ---")

    def actions(self, name: str) -> Dict:
        """Return available actions for a job.

        Returns the actions dict ``{name: (description, callable)}``.
        """
        job = self.get(name)
        return job.actions()

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
        """Return a compact status table.

        Lighter than ``summary()`` — shows type, name, status, priority.
        """
        lines = []
        state_str = " (PAUSED)" if self.is_paused else ""
        hdr = f"{'Name':<20} {'Type':<10} {'Status':<12} {'Pri':<5}{state_str}"
        lines.append(hdr)
        lines.append("─" * len(hdr))
        for j in self._jobs:
            lines.append(
                f"{j.name:<20} {j.job_type:<10} {j.status:<12} {j.priority:<5}"
            )
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a formatted table of all job statuses."""
        lines = []
        state_str = " (PAUSED)" if self.is_paused else ""
        hdr = f"{'Name':<20} {'Type':<10} {'Status':<10} {'Exit':<6} {'Duration':<12}{state_str}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for j in self._jobs:
            dur = f"{j.duration:.1f}s" if j.duration is not None else "-"
            ec = str(j.exit_code) if j.exit_code is not None else "-"
            lines.append(
                f"{j.name:<20} {j.job_type:<10} {j.status:<10} {ec:<6} {dur:<12}"
            )
        return "\n".join(lines)

    # ----- internal: scheduling loop -----

    def _execute_loop(self) -> None:
        """Core scheduler loop: dispatch ready jobs to thread pool."""
        max_workers = min(100, max(1, sum(self._capacity.values())))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while True:
                if self._stop_event.is_set():
                    break

                with self._lock:
                    self._fail_blocked_jobs()
                    ready = self._get_ready_jobs()
                    for job in ready:
                        if self._can_acquire(job):
                            self._acquire_resources(job)
                            job.status = RUNNING
                            pool.submit(self._run_job, job)

                # Check if all done
                with self._lock:
                    if not self._jobs:
                        break
                    all_finished = all(j.is_finished for j in self._jobs)
                if all_finished:
                    break

                time.sleep(self._poll_interval)

    def _fail_blocked_jobs(self) -> None:
        """Mark jobs as FAILED if any of their dependencies have failed or cancelled."""
        for job in self._jobs:
            if job.status == PENDING:
                if any(dep.status in (FAILED, CANCELLED) for dep in job.depends_on):
                    job.status = FAILED
                    job.exit_code = -1
                    job._trigger_hook("on_fail")

    def _get_ready_jobs(self) -> List[Job]:
        """Return pending jobs whose dependencies are met and resources
        are available, sorted by priority (descending)."""
        if self._paused:
            return []

        ready = []
        for job in self._jobs:
            if job.status != PENDING:
                continue
            # Check dependencies
            if not all(dep.status == DONE for dep in job.depends_on):
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

        log_file = None
        timer = None

        try:
            # Set up log file
            if self._log_dir:
                self._log_dir.mkdir(parents=True, exist_ok=True)
                job.log_path = self._log_dir / f"{job.name}.log"
                log_file = open(job.log_path, "w", encoding="utf-8")

            # Timeout watchdog
            if job.timeout is not None and job.timeout > 0:
                def _timeout_handler():
                    with self._lock:
                        status = job.status
                    if status == RUNNING:
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

            job._trigger_hook("on_start")
            job._pre_execute()
            job._execute(log_file)

        except Exception as exc:
            logger.error("Job %r crashed: %s", job.name, exc)
            with self._lock:
                job.status = FAILED
                job.exit_code = -1
            job._emit_line(f"[scheduler] internal error: {exc}")

        finally:
            job._post_execute()
            if timer is not None:
                timer.cancel()
            with self._lock:
                job.duration = time.monotonic() - start_time
            if log_file:
                log_file.close()
            self._release_resources(job)
