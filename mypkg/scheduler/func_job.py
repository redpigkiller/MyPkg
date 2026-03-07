"""
func_job.py — Concrete Job subclass for local Python function execution.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Dict, Optional, Tuple, Union

from .job import Job, RUNNING

class FuncJob(Job):
    """A job that runs a Python function in a background thread.

    Warning: 
        Because it runs in a thread, `FuncJob` is subject to the Python GIL.
        It is ideal for I/O bound tasks, but CPU bound tasks may block the 
        scheduler or fail to parallelize.

    Example:
        job = FuncJob("parse", my_function, args=(1, 2), kwargs={"foo": "bar"})
    """

    def __init__(
        self,
        name: str,
        func: Callable[..., Any],
        args: Optional[Tuple[Any, ...]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 0,
        max_retries: int = 0,
        resources: Optional[Dict[str, int]] = None,
        max_log_lines: int = 10_000,
    ) -> None:
        super().__init__(name, priority=priority, max_retries=max_retries, resources=resources, max_log_lines=max_log_lines)
        self.func = func
        self.args = args or ()
        self.kwargs = kwargs or {}

    def _execute(self, log_file=None) -> None:
        """Run the user function."""
        with self._lock:
            if self.is_cancelled:
                return

        try:
            res = self.func(*self.args, **self.kwargs)
            with self._lock:
                if not self.is_cancelled:
                    self._result = res
        except Exception as e:
            err_str = traceback.format_exc()
            self._emit_line(err_str)
            if log_file:
                log_file.write(err_str + "\n")
                log_file.flush()
            with self._lock:
                self._error = str(e)

    def kill(self) -> None:
        """
        FuncJob cannot be forcefully killed because Python threads cannot be 
        terminated from the outside. The state is marked CANCELLED and the 
        scheduler will ignore the result when the function eventually returns.
        """
        pass
