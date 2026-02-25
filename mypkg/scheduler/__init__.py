"""scheduler sub-package — cross-platform job scheduling."""

from mypkg.scheduler.job import (
    Job,
    JobStatus,
    PENDING, RUNNING, DONE, FAILED, CANCELLED,
)
from mypkg.scheduler.cmd_job import CmdJob
from mypkg.scheduler.scheduler import Scheduler

__all__ = [
    "Scheduler", "Job", "CmdJob",
    "JobStatus", "PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED",
]
