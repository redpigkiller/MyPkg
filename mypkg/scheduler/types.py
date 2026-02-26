"""
types.py — Type definitions and Enums for the Scheduler.
"""

from enum import Enum

class Event(str, Enum):
    """Lifecycle events for job execution."""
    START        = "start"
    DONE         = "done"
    FAILED       = "failed"
    CANCELLED    = "cancelled"
    FINISH       = "finish"       # any terminal state (done, fail, cancel)
    UPDATE       = "update"       # triggered when progress or custom_data changes via Matcher
    ALL_FINISHED = "all_finished" # triggered once all jobs in the scheduler reach a terminal state
    ANY          = "any"          # catches all events

class Resource(str, Enum):
    """Standard resource pool identifiers."""
    LOCAL_CPU = "local"
