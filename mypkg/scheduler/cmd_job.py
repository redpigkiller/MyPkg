"""
cmd_job.py — Concrete Job subclass for local shell command execution.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Callable, Dict, Optional, Union

from .job import Job, RUNNING, CANCELLED

class CmdJob(Job):
    """A job that runs a shell command on the local machine.

    Example:
        job = CmdJob("compile", "make -j4", cwd="/proj/rtl")
    """

    def __init__(
        self,
        name: str,
        cmd: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        priority: int = 0,
        max_retries: int = 0,
        resources: Optional[Dict[str, int]] = None,
        max_log_lines: int = 10_000,
    ) -> None:
        super().__init__(name, priority=priority, max_retries=max_retries, resources=resources, max_log_lines=max_log_lines)
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._proc: Optional[subprocess.Popen] = None

    def _execute(self, log_file=None) -> None:
        """Run `self.cmd` via subprocess and stream output."""
        env = {**os.environ, **self.env} if self.env else None
        
        # SRE Robustness: Process Group to avoid zombies
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        with self._lock:
            if self.is_cancelled:
                return
            proc = subprocess.Popen(
                self.cmd,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.cwd,
                env=env,
                text=True,
                bufsize=1,   # line-buffered
                **kwargs
            )
            self._proc = proc

        buffer = []
        # Non-blocking OS check loop avoiding Busy Wait
        while True:
            char = proc.stdout.read(1)
            
            if not char:  # EOF — process finished or killed
                if buffer:
                    line = "".join(buffer)
                    self._emit_line(line)
                    if log_file:
                        log_file.write(line)
                        log_file.flush()
                break

            buffer.append(char)
            if char == "\n":
                line = "".join(buffer)
                self._emit_line(line.rstrip())
                if log_file:
                    log_file.write(line)
                    log_file.flush()
                buffer.clear()
            else:
                partial = "".join(buffer)
                if partial.endswith((": ", "? ", "> ")):
                    self._emit_line(partial)
                    if log_file:
                        log_file.write(partial)
                        log_file.flush()
                    buffer.clear()

        proc.wait()
        
        with self._lock:
            # Result = exit_code
            self._result = proc.returncode
            if proc.returncode != 0 and self._status == RUNNING:
                lines = self.tail(5)
                self._error = "\n".join(lines) if lines else f"Exit code {proc.returncode}"

    def kill(self) -> None:
        """Forcefully terminate the running process tree."""
        with self._lock:
            proc = self._proc
            if proc is None:
                return

        if proc.poll() is not None:
            return  # Already terminated

        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                proc.kill() # fallback

    def send_input(self, text: str) -> None:
        """Write *text* to the running process's stdin."""
        with self._lock:
            proc = self._proc
            status = self._status
        
        if status != RUNNING or proc is None or proc.stdin is None:
            raise RuntimeError(f"Job {self.name!r}: stdin not available or not running.")
            
        try:
            proc.stdin.write(text)
            proc.stdin.flush()
        except OSError:
            pass # Pipe likely broken because process exited
