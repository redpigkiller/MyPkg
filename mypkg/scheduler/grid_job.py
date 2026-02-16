"""
GridJob — Job subclass for SGE (Sun Grid Engine) grid submission.

Submits commands via ``qsub``, polls status with ``qstat``,
and kills with ``qdel``.  Configurable for other grid systems
by overriding ``_parse_grid_id`` and ``_check_grid_status``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from mypkg.scheduler.job import Job, DONE, FAILED, RUNNING

logger = logging.getLogger(__name__)


class GridJob(Job):
    """A job that runs on an SGE grid cluster.

    The command is submitted via ``qsub`` and monitored via ``qstat``.
    This Job type is designed for Linux workstations only.

    Parameters:
        submit_cmd:     Grid submit command (default ``"qsub"``).
        submit_opts:    Extra submit options, e.g. ``"-q normal -pe smp 4"``.
        kill_cmd:       Grid kill command (default ``"qdel"``).
        status_cmd:     Grid status command (default ``"qstat"``).
        poll_interval:  Seconds between status polls (default ``10.0``).

    Default resources: ``{"grid": 1}``

    Example::

        job = GridJob(
            "sim_01",
            cmd="vcs -R +tc=01",
            cwd="/proj/sim",
            submit_opts="-q regression -pe smp 2",
        )
    """

    default_resources: Dict[str, int] = {"grid": 1}

    def __init__(
        self,
        name: str,
        cmd: str,
        *,
        submit_cmd: str = "qsub",
        submit_opts: str = "",
        kill_cmd: str = "qdel",
        status_cmd: str = "qstat",
        poll_interval: float = 10.0,
        **kwargs,
    ) -> None:
        super().__init__(name, cmd, **kwargs)
        self.submit_cmd = submit_cmd
        self.submit_opts = submit_opts
        self.kill_cmd = kill_cmd
        self.status_cmd = status_cmd
        self.grid_poll_interval = poll_interval
        self.grid_id: Optional[str] = None

    # ----- execution -----

    def _execute(self, log_file=None) -> None:
        """Submit to grid → poll until finished → capture output from log."""
        # Build submission command.
        # Write a temp shell script so qsub can execute it.
        script_content = f"#!/bin/bash\n{self.cmd}\n"
        script_path = Path(self.cwd or ".") / f".sched_{self.name}.sh"
        script_path.write_text(script_content, encoding="utf-8")
        script_path.chmod(0o755)

        log_opt = ""
        if log_file:
            log_opt = f"-o {log_file.name}"

        submit_full = (
            f"{self.submit_cmd} {self.submit_opts} {log_opt} "
            f"-N {self.name} "
            f"-cwd "
            f"{script_path}"
        ).strip()

        self._emit_line(f"[grid] submitting: {submit_full}")

        try:
            result = subprocess.run(
                submit_full,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.cwd,
            )

            if result.returncode != 0:
                self._emit_line(f"[grid] submit failed: {result.stderr.strip()}")
                self.exit_code = result.returncode
                self.status = FAILED
                return

            # Parse grid job ID from qsub output
            self.grid_id = self._parse_grid_id(result.stdout)
            self._emit_line(f"[grid] submitted: {result.stdout.strip()}")

            if not self.grid_id:
                self._emit_line("[grid] warning: could not parse grid job ID")
                self.exit_code = -1
                self.status = FAILED
                return

            # Poll until the grid job finishes
            last_log_pos = 0
            while True:
                time.sleep(self.grid_poll_interval)

                # Stream log file content if available
                if log_file:
                    last_log_pos = self._tail_log(log_file.name, last_log_pos)

                grid_status = self._check_grid_status()
                if grid_status is None:
                    # Job no longer in qstat → it has finished
                    break

            # Final log flush
            if log_file:
                self._tail_log(log_file.name, last_log_pos)

            # Check exit status (read from the acct file or assume success
            # if the job disappeared from qstat without error)
            self.exit_code = 0
            self.status = DONE

        except Exception as exc:
            self._emit_line(f"[grid] error: {exc}")
            self.exit_code = -1
            self.status = FAILED

        finally:
            # Clean up temp script
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ----- grid-specific methods (override for non-SGE) -----

    def _parse_grid_id(self, output: str) -> Optional[str]:
        """Parse job ID from ``qsub`` output.

        Default parses SGE format:
        ``Your job 12345 ("name") has been submitted``

        Override for other grid systems.
        """
        m = re.search(r"[Yy]our job (\d+)", output)
        return m.group(1) if m else None

    def _check_grid_status(self) -> Optional[str]:
        """Query grid for this job's status.

        Returns the status string (e.g. ``"r"``, ``"qw"``, ``"Eqw"``)
        or ``None`` if the job is no longer in the queue (= finished).

        Override for non-SGE grids.
        """
        if not self.grid_id:
            return None

        try:
            result = subprocess.run(
                f"{self.status_cmd} -j {self.grid_id}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # If qstat -j returns non-zero, job is gone (finished)
            if result.returncode != 0:
                return None

            # Try to parse status from output
            for line in result.stdout.splitlines():
                if line.strip().startswith("job_state"):
                    # Format: "job_state   1:  r"
                    parts = line.split()
                    if parts:
                        return parts[-1]

            # If we got output but couldn't parse state, job exists
            return "unknown"

        except subprocess.TimeoutExpired:
            logger.warning("qstat timed out for job %s", self.grid_id)
            return "unknown"

    def _tail_log(self, log_path: str, start_pos: int) -> int:
        """Read new content from log file and emit as output lines.

        Returns the new file position.
        """
        try:
            p = Path(log_path)
            if not p.exists():
                return start_pos
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start_pos)
                new_content = f.read()
                new_pos = f.tell()
            if new_content:
                for line in new_content.splitlines():
                    self._emit_line(line)
            return new_pos
        except Exception:
            return start_pos

    # ----- interactive control -----

    def kill(self) -> None:
        """Kill the grid job via ``qdel``."""
        if self.status != RUNNING:
            raise RuntimeError(
                f"Job {self.name!r} is not running (status={self.status})."
            )
        if not self.grid_id:
            raise RuntimeError(f"Job {self.name!r}: no grid job ID.")

        result = subprocess.run(
            f"{self.kill_cmd} {self.grid_id}",
            shell=True,
            capture_output=True,
            text=True,
        )
        self._emit_line(f"[grid] qdel: {result.stdout.strip()}")
        if result.returncode != 0:
            self._emit_line(f"[grid] qdel error: {result.stderr.strip()}")

    def show_grid_status(self) -> None:
        """Print full qstat output for this job."""
        if not self.grid_id:
            print(f"{self.name}: no grid job ID")
            return
        result = subprocess.run(
            f"{self.status_cmd} -j {self.grid_id}",
            shell=True,
            capture_output=True,
            text=True,
        )
        print(result.stdout if result.stdout else f"Job {self.grid_id} not found in queue")

    def actions(self) -> Dict[str, Tuple[str, Callable]]:
        acts: Dict[str, Tuple[str, Callable]] = {}
        acts["grid_status"] = ("查詢 grid job 狀態 (qstat)", self.show_grid_status)
        if self.log_path and self.log_path.exists():
            from mypkg.scheduler.job import _open_file_cross_platform
            acts["open_log"] = ("開啟 log 檔案", lambda: _open_file_cross_platform(self.log_path))
        if self.cwd:
            from mypkg.scheduler.job import _open_file_cross_platform
            acts["open_cwd"] = ("開啟工作目錄", lambda: _open_file_cross_platform(self.cwd))
        return acts

    def __repr__(self) -> str:
        grid = f", grid_id={self.grid_id!r}" if self.grid_id else ""
        return (
            f"GridJob({self.name!r}, status={self.status!r}, "
            f"exit_code={self.exit_code}{grid})"
        )
