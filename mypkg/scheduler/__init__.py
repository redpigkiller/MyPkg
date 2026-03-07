"""scheduler sub-package — minimal, robust job scheduling."""

from mypkg.scheduler.job import (
    Job,
    JobStatus,
    PENDING, RUNNING, DONE, FAILED, CANCELLED,
)
from mypkg.scheduler.cmd_job import CmdJob
from mypkg.scheduler.func_job import FuncJob
from mypkg.scheduler.manager import JobManager

__all__ = [
    "JobManager", "Job", "CmdJob", "FuncJob",
    "JobStatus", "PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED",
]
