"""
manager.py — Thread-pool based job manager.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union, Any

from .job import Job, JobStatus, PENDING, RUNNING, DONE, FAILED, CANCELLED

logger = logging.getLogger(__name__)

class JobManager:
    """Manages concurrent job execution using a thread pool.
    
    Args:
        max_workers:   Number of worker threads (default 4).
        resources:     Capacity limits. Can be static ints or dynamic Callables returning ints.
        log_dir:       Directory to store standard job logs. None to disable files.
        max_history:   How many terminal jobs to keep in memory before GC.
        poll_interval: Seconds between scheduler loops if no jobs ready (default 0.5s).
    """

    def __init__(
        self,
        max_workers: int = 4,
        resources: Optional[Dict[str, Union[int, Callable[[], int]]]] = None,
        log_dir: Optional[Union[str, Path]] = None,
        max_history: int = 1000,
        poll_interval: float = 0.5,
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._resources: Dict[str, Union[int, Callable[[], int]]] = resources or {}
        
        self._log_dir = Path(log_dir) if log_dir else None
        self._max_history = max_history
        self._poll_interval = poll_interval

        # Internal State tracking
        self._jobs: List[Job] = []
        self._used_resources: Dict[str, int] = {}
        
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        
        # SRE Robustness: Thread pool for events and main loops
        self._active_workers: int = 0
        self._stop_event = threading.Event()
        self._paused = False
        self._bg_thread: Optional[threading.Thread] = None

        self._event_bus = ThreadPoolExecutor(max_workers=2, thread_name_prefix="JobEventBus")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def __enter__(self) -> JobManager:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.stop()
        else:
            self.wait()
            self.stop()

    def start(self) -> None:
        """Start the background scheduling loop."""
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._execute_loop, daemon=True, name="JobManagerLoop"
        )
        self._bg_thread.start()

    def stop(self) -> None:
        """Stop the scheduler from dispatching new jobs."""
        self._stop_event.set()
        with self._cond:
            self._cond.notify_all()
        if self._bg_thread:
            self._bg_thread.join()
        self._event_bus.shutdown(wait=False)

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._cond.notify_all()

    def wait(self, target_id: Optional[uuid.UUID] = None, timeout: Optional[float] = None) -> bool:
        """Wait until all jobs (or a target job) finish."""
        deadline = time.monotonic() + timeout if timeout is not None else None

        def _is_done() -> bool:
            if target_id is not None:
                j = self.get(target_id)
                if not j:
                    return True
                return j.status in (DONE, FAILED, CANCELLED)
            return all(j.status in (DONE, FAILED, CANCELLED) for j in self._jobs)

        with self._cond:
            while not _is_done():
                if self._stop_event.is_set():
                    break
                t_rem = None
                if deadline is not None:
                    t_rem = deadline - time.monotonic()
                    if t_rem <= 0:
                        return False
                self._cond.wait(timeout=t_rem)
            return True

    # ------------------------------------------------------------------
    # Job Management
    # ------------------------------------------------------------------
    def add(self, job: Job) -> None:
        """Enqueue a job for execution."""
        # Static Resource Validation (Fail Fast if impossible)
        for res_name, req_val in job.resources.items():
            limit_val = self._resources.get(res_name)
            if limit_val is not None:
                # If static int, we can check capability
                if isinstance(limit_val, int) and isinstance(req_val, int) and req_val > limit_val:
                    raise ValueError(
                        f"Impossible Resource Request: Job requires {req_val} '{res_name}' "
                        f"but JobManager only supports up to {limit_val}."
                    )
                # If dynamic Callable, we trust it or let it fail at runtime

        with self._lock:
            if any(j.id == job.id for j in self._jobs):
                raise ValueError(f"Job with ID {job.id} is already in the manager.")
            
            # SRE wake-up hook injection
            def _wake_up():
                with self._cond:
                    self._cond.notify_all()
            job._on_state_change_cb = _wake_up

            self._jobs.append(job)
            self._cleanup_history()
            self._cond.notify_all()

    def get(self, target_id: uuid.UUID) -> Optional[Job]:
        with self._lock:
            for j in self._jobs:
                if j.id == target_id:
                    return j
        return None

    def cancel(self, target_id: uuid.UUID) -> None:
        j = self.get(target_id)
        if j:
            j.cancel()

    def cancel_all(self) -> None:
        with self._lock:
            for j in self._jobs:
                if j.status in (PENDING, RUNNING):
                    j.cancel()

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------
    def jobs(self) -> List[Job]:
        with self._lock:
            return list(self._jobs)

    def running(self) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status == RUNNING]

    def pending(self) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status == PENDING]

    def finished(self) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs if j.status in (DONE, FAILED, CANCELLED)]

    # ------------------------------------------------------------------
    # Inner Execution Loop
    # ------------------------------------------------------------------
    def _execute_loop(self) -> None:
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            while not self._stop_event.is_set():
                with self._cond:
                    ready_job = self._get_ready_job()
                    if ready_job is None:
                        self._cond.wait(timeout=self._poll_interval)
                        continue

                    # Dispatch job
                    self._acquire_resources(ready_job)
                    self._active_workers += 1
                    
                    with ready_job._lock:
                        ready_job._status = RUNNING
                        ready_job._start_time = time.monotonic()
                    
                    pool.submit(self._run_job_wrapper, ready_job)
            
            # Flush pool on stop
            pool.shutdown(wait=False, cancel_futures=True)

    def _get_ready_job(self) -> Optional[Job]:
        if self._paused:
            return None
            
        if self._active_workers >= self._max_workers:
            return None

        # Gather dynamic capacities
        capacities = {}
        for res_name, limit_val in self._resources.items():
            try:
                if callable(limit_val):
                    capacities[res_name] = limit_val()
                else:
                    capacities[res_name] = limit_val
            except Exception as e:
                logger.error("Error computing dynamic resource %r: %s", res_name, e)
                capacities[res_name] = 0

        # Find highest priority pending job that fits
        pending_obs = sorted(
            [j for j in self._jobs if j.status == PENDING], 
            key=lambda j: j.priority, 
            reverse=True
        )

        for j in pending_obs:
            can_fit = True
            for res_name, req_val in j.resources.items():
                v = req_val() if callable(req_val) else req_val
                limit = capacities.get(res_name, 0)
                used = self._used_resources.get(res_name, 0)
                if used + v > limit:
                    can_fit = False
                    break
            if can_fit:
                return j

        return None

    def _acquire_resources(self, job: Job) -> None:
        for res_name, req_val in job.resources.items():
            v = req_val() if callable(req_val) else req_val
            self._used_resources[res_name] = self._used_resources.get(res_name, 0) + v

    def _release_resources(self, job: Job) -> None:
        with self._lock:
            for res_name, req_val in job.resources.items():
                v = req_val() if callable(req_val) else req_val
                self._used_resources[res_name] = max(0, self._used_resources.get(res_name, 0) - v)
            self._cond.notify_all()

    def _cleanup_history(self) -> None:
        if len(self._jobs) <= self._max_history:
            return
        
        # Pop oldest finished
        finished_list = [j for j in self._jobs if j.status in (DONE, FAILED, CANCELLED)]
        diff = len(self._jobs) - self._max_history
        if diff > 0 and finished_list:
            to_remove = finished_list[:diff]
            for j in to_remove:
                # Remove callback hook to avoid leaks
                j._on_state_change_cb = None
                self._jobs.remove(j)

    def _run_job_wrapper(self, job: Job) -> None:
        """Worker thread entrypoint for running constraints, retry logic, and cleanup."""
        attempt = job._retry_count

        while True:
            log_file = None
            if self._log_dir:
                self._log_dir.mkdir(parents=True, exist_ok=True)
                path = self._log_dir / f"{job.name}_{job.id.hex[:8]}.log"
                log_file = open(path, "a", encoding="utf-8")

            # Execute
            try:
                job._execute(log_file)
            except Exception as e:
                with job._lock:
                    if job._status == RUNNING:
                        job._status = FAILED
                        job._error = str(e)
            finally:
                if log_file:
                    log_file.close()

            with job._lock:
                if job._status == RUNNING:
                    from .cmd_job import CmdJob
                    if job._error is not None or (isinstance(job, CmdJob) and getattr(job, '_result', 0) != 0):
                        job._status = FAILED
                    else:
                        job._status = DONE
            
            # Retry evaluation
            if job.status == FAILED and attempt < job.max_retries and not job.is_cancelled:
                attempt += 1
                with job._lock:
                    job._status = RUNNING
                    job._retry_count = attempt
                    job._result = None
                    job._error = None
                    job._output_buffer.clear()
                time.sleep(0.1) # backoff
                continue
                
            break 
            
        with job._lock:
            job._end_time = time.monotonic()
            
        self._release_resources(job)

        with self._lock:
            self._active_workers -= 1
            self._cond.notify_all()
            
        # Dispatch final callbacks
        self._dispatch_callbacks(job)

    def _dispatch_callbacks(self, job: Job) -> None:
        if job.status == DONE:
            cbs = list(job._on_done_cbs)
            for cb in cbs:
                self._event_bus.submit(cb, job)
        elif job.status == FAILED:
            cbs = list(job._on_fail_cbs)
            err = job.error or "Unknown failure"
            for cb in cbs:
                self._event_bus.submit(cb, job, err)
