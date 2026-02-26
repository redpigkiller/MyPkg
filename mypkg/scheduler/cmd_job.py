"""
cmd_job.py — Concrete Job subclass for local shell command execution.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, Union

from .job import Job, DONE, FAILED, RUNNING


def _open_path(path: Union[str, Path]) -> None:
    """Open a file or directory with the OS default application."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class CmdJob(Job):
    """A job that runs a shell command on the local machine.

    Default resource footprint: ``{"local": 1}``

    Example::

        sched.submit("compile", cmd="make -j4", cwd="/proj/rtl", timeout=600)
    """

    default_resources: Dict[str, int] = {"local": 1}

    def _execute(self, log_file=None) -> None:
        """Run ``self.cmd`` via subprocess and stream output line-by-line."""
        env = {**os.environ, **self.env} if self.env else None

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
        )
        self._proc = proc
        self._proc_ready.set()

        # Stream output character-by-character so that partial lines
        # (prompts ending in ": " / "? " / "> ") are emitted immediately.
        assert proc.stdout is not None
        buffer = []
        while True:
            char = proc.stdout.read(1)

            if not char:                        # EOF — process finished
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
        self.exit_code = proc.returncode

        # Only derive status from the exit code when a matcher has not already
        # overridden it during streaming.
        if self.status == RUNNING:
            self.status = DONE if proc.returncode == 0 else FAILED

    # ------------------------------------------------------------------
    # Interactive control (called by Scheduler via name-based API)
    # ------------------------------------------------------------------

    def send_input(self, text: str) -> None:
        """Write *text* to the running process's stdin."""
        self._require_running("send_input")
        self._wait_for_proc()
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"Job {self.name!r}: stdin not available.")
        self._proc.stdin.write(text)
        self._proc.stdin.flush()

    def interrupt(self) -> None:
        """Send SIGINT to the running process."""
        self._require_running("interrupt")
        self._wait_for_proc()
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
        """
        self._require_running("kill")
        self._wait_for_proc()
        if self._proc is None:
            raise RuntimeError(f"Job {self.name!r}: no process handle.")
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                capture_output=True,
            )
        else:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_running(self, op: str) -> None:
        if self.status != RUNNING:
            raise RuntimeError(
                f"Cannot {op} job {self.name!r}: status is {self.status!r} (must be running)."
            )

    def _wait_for_proc(self) -> None:
        """Block until ``_proc`` is assigned (Popen may lag behind status change)."""
        while not self._proc_ready.wait(timeout=0.1):
            if self.status != RUNNING:
                raise RuntimeError(
                    f"Job {self.name!r} stopped running before process was ready."
                )
