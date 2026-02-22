"""
CmdJob — Concrete subclass for shell commands (local execution).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Tuple, Union

from mypkg.scheduler.job import Job, DONE, FAILED


def _open_file_cross_platform(path: Union[str, Path]) -> None:
    """Open a file or directory with the system default handler."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class CmdJob(Job):
    """A job that runs a shell command on the local machine.

    Default resources: ``{"local": 1}``

    Example::

        job = CmdJob("compile", cmd="make -j4", cwd="/proj/rtl", timeout=600)
    """

    default_resources: Dict[str, int] = {"local": 1}

    def _execute(self, log_file=None) -> None:
        """Run a shell command via subprocess."""
        env = None
        if self.env:
            env = {**os.environ, **self.env}

        proc = subprocess.Popen(
            self.cmd,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
            text=True,
            bufsize=1,  # line-buffered
        )
        self._proc = proc
        self._proc_ready.set()

        # Stream stdout line-by-line
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n").rstrip("\r")
            self._emit_line(line)
            if log_file:
                log_file.write(raw_line)
                log_file.flush()

        proc.wait()
        self.exit_code = proc.returncode
        self.status = DONE if proc.returncode == 0 else FAILED

    def actions(self) -> Dict[str, Tuple[str, Callable]]:
        acts = super().actions()
        if self.log_path and self.log_path.exists():
            acts["open_log"] = ("Open log file", lambda: _open_file_cross_platform(self.log_path))
        if self.cwd:
            acts["open_cwd"] = ("Open working directory", lambda: _open_file_cross_platform(self.cwd))
        return acts
