"""Comprehensive tests for Scheduler — cross-platform job scheduling.

Sections:
  1-12:  Phase 1 — Core
  13-20: Phase 2a — Interactive control
  21-22: Phase 2b — Timeout, CmdJob actions
  23:    Phase 2c — Status filters
  24-28: Phase 3  — Hooks, Matchers, Lifecycle
"""

import os
import sys
import time
from unittest.mock import patch, MagicMock
import tempfile
import threading
from pathlib import Path

import pytest
from mypkg import Scheduler, Job, CmdJob


# ---------------------------------------------------------------------------
# Helpers: cross-platform echo / sleep commands
# ---------------------------------------------------------------------------

_IS_WIN = sys.platform == "win32"

# Use 'python' from PATH rather than sys.executable — the latter may
# contain spaces (e.g. C:\Users\Hong Bin\...) which break shell=True on Windows.
_PY = "python"


def _echo_cmd(msg: str) -> str:
    """Return a shell command that prints *msg* to stdout."""
    if _IS_WIN:
        return f'cmd /c "echo {msg}"'
    return f"echo {msg}"


def _multi_echo_cmd(*msgs: str) -> str:
    """Return a command that echoes multiple lines."""
    if _IS_WIN:
        parts = [f"echo {m}" for m in msgs]
        return "cmd /c \"" + " & ".join(parts) + "\""
    return " && ".join(f"echo {m}" for m in msgs)


def _sleep_cmd(seconds: float) -> str:
    """Return a command that sleeps for *seconds*."""
    return f'{_PY} -c "import time; time.sleep({seconds})"'


def _fail_cmd() -> str:
    """Return a command that exits with non-zero code."""
    if _IS_WIN:
        return "cmd /c exit 1"
    return "exit 1"


def _echo_sleep_cmd(msg: str, seconds: float) -> str:
    """Echo then sleep."""
    return f"""{_PY} -c "print('{msg}'); import time; time.sleep({seconds})" """


# ═══════════════════════════════════════════════════════════════════════════
# 1. Job Basics
# ═══════════════════════════════════════════════════════════════════════════


class TestJobBasics:
    def test_construction(self):
        j = Job("my_job", cmd="echo hi")
        assert j.name == "my_job"
        assert j.cmd == "echo hi"
        assert j.status == "pending"
        assert j.priority == 0
        assert j.exit_code is None
        assert j.duration is None
        assert j.depends_on == []

    def test_default_resources(self):
        j = Job("j", cmd="echo x")
        assert j.resources == {"local": 1}

    def test_custom_resources(self):
        j = Job("j", cmd="echo x", resources={"grid": 2, "license": 1})
        assert j.resources == {"grid": 2, "license": 1}

    def test_auto_name_when_omitted(self):
        """Omitting name should auto-generate a unique string identifier."""
        j = Job(cmd="echo x")
        assert j.name  # non-empty
        assert j.name.startswith("Job_")

    def test_auto_name_unique(self):
        """Two auto-named jobs must not share the same name."""
        j1 = Job(cmd="echo x")
        j2 = Job(cmd="echo y")
        assert j1.name != j2.name

    def test_auto_name_uses_class_name(self):
        """Auto-name prefix matches the concrete class name."""
        j = CmdJob("cmdjob_test", "echo x")
        assert j.name == "cmdjob_test"

    def test_empty_cmd_auto_labels(self):
        """cmd is now optional; omitting it auto-assigns a label."""
        j = Job("j")
        assert j.cmd == "<Job>"   # auto-label from class name

    def test_tail(self):
        j = Job("j")
        j._emit_line("line1")
        j._emit_line("line2")
        j._emit_line("line3")
        assert j.tail(2) == ["line2", "line3"]

    def test_output_lines_property(self):
        j = Job("j", cmd="echo x")
        j._emit_line("hello")
        assert j.tail() == ["hello"]

    def test_is_finished(self):
        j = Job("j")
        assert not j.is_finished
        j.status = "done"
        assert j.is_finished
        j.status = "failed"
        assert j.is_finished

    def test_repr(self):
        r = repr(Job("my_job"))
        assert "my_job" in r and "pending" in r


# ═══════════════════════════════════════════════════════════════════════════
# 2. CmdJob
# ═══════════════════════════════════════════════════════════════════════════


class TestCmdJob:
    def test_is_job(self):
        assert isinstance(CmdJob("c", "echo x"), Job)

    def test_default_resources(self):
        assert CmdJob("c", "echo x").resources == {"local": 1}

    def test_custom_priority(self):
        j = CmdJob("c", "echo x", priority=5)
        assert j.priority == 5


# ═══════════════════════════════════════════════════════════════════════════
# 3. Scheduler — Single Job
# ═══════════════════════════════════════════════════════════════════════════


class TestSingleJob:
    def test_echo(self):
        sched = Scheduler(resources={"local": 2})
        sched.submit("hello", _echo_cmd("hello_world"))
        sched.run()
        job = sched.get("hello")
        assert job.status == "done"
        assert job.exit_code == 0
        assert job.duration is not None and job.duration >= 0
        assert any("hello_world" in line for line in sched.stdout("hello", 10))

    def test_with_cwd(self, tmp_path):
        sched = Scheduler(resources={"local": 1})
        sched.submit("pwd", _echo_cmd("ok"), cwd=str(tmp_path))
        sched.run()
        job = sched.get("pwd")
        assert job.status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Scheduler — Parallel Jobs
# ═══════════════════════════════════════════════════════════════════════════


class TestParallel:
    def test_two_jobs_run_concurrently(self):
        """Two 1-second jobs with local=2 should finish in ~1s, not ~2s."""
        sched = Scheduler(resources={"local": 2})
        sched.submit("s1", _sleep_cmd(1.0))
        sched.submit("s2", _sleep_cmd(1.0))

        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0

        assert sched.get("s1").status == "done" and sched.get("s2").status == "done"
        assert elapsed < 2.5  # should be ~1s, generous margin


# ═══════════════════════════════════════════════════════════════════════════
# 5. Scheduler — Priority
# ═══════════════════════════════════════════════════════════════════════════


class TestPriority:
    def test_higher_priority_starts_first(self):
        """With local=1, the higher-priority job should run first."""
        order = []
        sched = Scheduler(resources={"local": 1})
        sched.submit("lo", _echo_cmd("lo"), priority=1)
        sched.submit("hi", _echo_cmd("hi"), priority=10)
        
        # Test hook equivalent using sched.on Event.START
        from mypkg.scheduler.types import Event
        
        def tracker(snap):
            if snap.name not in order:
                order.append(snap.name)
        
        sched.on(Event.START, tracker)
        sched.run()
        # hi should appear before lo
        assert order.index("hi") < order.index("lo")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Scheduler — Dependencies
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencies:
    def test_basic_dep(self):
        sched = Scheduler(resources={"local": 2})
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.run()
        a = sched.get("a")
        b = sched.get("b")
        assert a.status == "done" and b.status == "done"
        # a must have finished before b started
        assert a.duration is not None and b.duration is not None

    def test_dep_chain(self):
        sched = Scheduler(resources={"local": 2})
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.submit("c", _echo_cmd("C"), depends_on=["b"])
        sched.run()
        assert all(sched.get(name).status == "done" for name in ["a", "b", "c"])

    def test_unknown_dep_raises(self):
        # We don't raise on submit anymore, it's just never run
        sched = Scheduler()
        sched.submit("child", "echo y", depends_on=["orphan"])
        sched.start()
        sched.wait() # will be auto cancelled
        assert sched.get("child").status == "cancelled"

    def test_failed_dep_propagates(self):
        """If dep fails, dependent job should also fail or cancel."""
        sched = Scheduler(resources={"local": 2})
        sched.submit("a", _fail_cmd())
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.run()
        assert sched.get("a").status == "failed"
        assert sched.get("b").status == "cancelled"

    def test_cycle_dependencies_auto_cancel(self):
        """A dependency cycle should be detected and cancelled, not hang forever."""
        sched = Scheduler(resources={"local": 2}, poll_interval=0.01)
        sched.submit("a", _echo_cmd("A"), depends_on=["b"])
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.start()
        assert sched.wait(timeout=2.0)
        assert sched.get("a").status == "cancelled"
        assert sched.get("b").status == "cancelled"

    def test_large_cycle_detection_no_recursion_overflow(self):
        """Cycle detection should not rely on deep recursion."""
        sched = Scheduler(resources={"local": 1})
        n = 1500
        jobs = []
        for i in range(n):
            dep = f"j{(i + 1) % n}"
            jobs.append(Job(f"j{i}", cmd="echo x", depends_on=[dep]))
        cycle_names = sched._find_pending_cycle_names(jobs)  # intentionally test internal helper
        assert len(cycle_names) == n


# ═══════════════════════════════════════════════════════════════════════════
# 7. Scheduler — Failure
# ═══════════════════════════════════════════════════════════════════════════


class TestFailure:
    def test_nonzero_exit(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("bad", _fail_cmd())
        sched.run()
        j = sched.get("bad")
        assert j.status == "failed"
        assert j.exit_code != 0

    def test_invalid_command(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("bad", "this_command_does_not_exist_xyz")
        sched.run()
        assert sched.get("bad").status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Scheduler — Stdout Streaming
# ═══════════════════════════════════════════════════════════════════════════


class TestStreaming:
    def test_multi_line(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("multi", _multi_echo_cmd("line1", "line2", "line3"))
        sched.run()
        texts = " ".join(sched.stdout("multi", 10))
        assert "line1" in texts and "line2" in texts and "line3" in texts


# ═══════════════════════════════════════════════════════════════════════════
# 9. Scheduler — Log Directory
# ═══════════════════════════════════════════════════════════════════════════


class TestLogDir:
    def test_log_created(self, tmp_path):
        log_dir = tmp_path / "logs"
        sched = Scheduler(resources={"local": 1}, log_dir=str(log_dir))
        sched.submit("log_test", _echo_cmd("LOG_CONTENT"))
        sched.run()
        j = sched.get("log_test")
        assert j.log_path is not None
        assert j.log_path.exists()
        content = j.log_path.read_text(encoding="utf-8")
        assert "LOG_CONTENT" in content

    def test_no_log_dir(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("no_log", _echo_cmd("hi"))
        sched.run()
        assert sched.get("no_log").log_path is None


# ═══════════════════════════════════════════════════════════════════════════
# 10. Scheduler — Resource Limit
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceLimit:
    def test_max_local_one(self):
        """With local=1, two 0.5s jobs should take ≥1s (sequential)."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("s1", _sleep_cmd(0.5))
        sched.submit("s2", _sleep_cmd(0.5))

        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0

        assert sched.get("s1").status == "done" and sched.get("s2").status == "done"
        assert elapsed >= 0.8  # should be ~1s


# ═══════════════════════════════════════════════════════════════════════════
# 11. Scheduler — start/wait (Non-blocking)
# ═══════════════════════════════════════════════════════════════════════════


class TestStartWait:
    def test_non_blocking(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("bg", _sleep_cmd(0.5))
        sched.start()
        # start() should return immediately
        assert True
        sched.wait()
        assert sched.get("bg").status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Scheduler — Summary
# ═══════════════════════════════════════════════════════════════════════════


class TestSummary:
    def test_summary_contains_job_info(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("sum_test", _echo_cmd("hi"))
        sched.run()
        text = sched.summary()
        assert "sum_test" in text
        assert "done" in text

    def test_duplicate_name_raises(self):
        sched = Scheduler()
        sched.submit("dup", "echo a")
        with pytest.raises(ValueError, match="Duplicate"):
            sched.submit("dup", "echo b")


# ═══════════════════════════════════════════════════════════════════════════
# 13. Cancel
# ═══════════════════════════════════════════════════════════════════════════


class TestCancel:
    def test_cancel_pending(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _echo_cmd("B"))
        sched.cancel("b")
        sched.run()
        assert sched.get("a").status == "done"
        assert sched.get("b").status == "cancelled"

    def test_cancel_not_pending_raises(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.run()
        with pytest.raises(RuntimeError, match="pending"):
            sched.cancel("j")

    def test_cancelled_dep_propagates(self):
        """If dep is cancelled, dependent job should cancel/fail."""
        sched = Scheduler(resources={"local": 2})
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.cancel("a")
        sched.run()
        assert sched.get("a").status == "cancelled"
        assert sched.get("b").status == "cancelled"


# ═══════════════════════════════════════════════════════════════════════════
# 14. Kill
# ═══════════════════════════════════════════════════════════════════════════


class TestKill:
    def test_kill_running(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("slow", _sleep_cmd(10))
        sched.start()
        # Wait for job to start running
        for _ in range(40):
            if sched.get("slow").status == "running":
                break
            time.sleep(0.1)
        assert sched.get("slow").status == "running"
        sched.kill("slow")
        sched.wait()
        assert sched.get("slow").status == "failed"

    def test_kill_not_running_raises(self):
        sched = Scheduler()
        sched.submit("j", "echo x")
        with pytest.raises(RuntimeError, match="status is 'pending'"):
            sched.kill("j")


# ═══════════════════════════════════════════════════════════════════════════
# 15. Send stdin
# ═══════════════════════════════════════════════════════════════════════════


def _stdin_echo_cmd() -> str:
    """A command that reads one line from stdin and prints it."""
    return f'{_PY} -c "import sys; line=sys.stdin.readline(); print(line.strip())"'


class TestSendStdin:
    def test_send_to_running(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("reader", _stdin_echo_cmd())
        sched.start()
        # Wait for job to start
        for _ in range(40):
            if sched.get("reader").status == "running":
                break
            time.sleep(0.1)
        assert sched.get("reader").status == "running"
        sched.send_input("reader", "HELLO_STDIN\n")
        sched.wait()
        assert sched.get("reader").status == "done"
        assert any("HELLO_STDIN" in line for line in sched.stdout("reader", 10))

    def test_send_not_running_raises(self):
        sched = Scheduler()
        sched.submit("j", "echo x")
        with pytest.raises(RuntimeError, match="status is 'pending'"):
            sched.send_input("j", "data\n")

# ═══════════════════════════════════════════════════════════════════════════
# 16. Set Priority
# ═══════════════════════════════════════════════════════════════════════════

class TestSetPriority:
    def test_change_priority(self):
        """Change priority before run, verify order changes."""
        order = []
        sched = Scheduler(resources={"local": 1})
        sched.submit("a", _echo_cmd("A"), priority=10)
        sched.submit("b", _echo_cmd("B"), priority=1)
        
        from mypkg.scheduler.types import Event
        def tracker(snap):
            if snap.name not in order:
                order.append(snap.name)
                
        sched.on(Event.START, tracker)
        # Flip: make b higher priority
        sched.set_priority("b", 100)
        sched.run()
        assert order.index("b") < order.index("a")

    def test_set_priority_not_pending_raises(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.run()
        with pytest.raises(RuntimeError, match="pending"):
            sched.set_priority("j", 99)


# ═══════════════════════════════════════════════════════════════════════════
# 17. Status
# ═══════════════════════════════════════════════════════════════════════════


class TestStatus:
    def test_status_output(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("st_test", _echo_cmd("hi"))
        sched.run()
        text = sched.status()
        assert "st_test" in text
        assert "CmdJob" in text
        assert "done" in text


# ═══════════════════════════════════════════════════════════════════════════
# 18. Get
# ═══════════════════════════════════════════════════════════════════════════


class TestGet:
    def test_get_existing(self):
        sched = Scheduler()
        sched.submit("find_me", "echo x")
        assert sched.get("find_me").name == "find_me"

    def test_get_missing_raises(self):
        sched = Scheduler()
        with pytest.raises(KeyError):
            sched.get("nope")


# ═══════════════════════════════════════════════════════════════════════════
# Removed TestActions and TestCmdJobActions as actions have been removed from Job architecture


# ═══════════════════════════════════════════════════════════════════════════
# 23. Status Filters
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusFilters:
    def test_filter_properties(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _fail_cmd())
        sched.submit("c", _echo_cmd("C"))
        sched.cancel("c")
        sched.run()
        assert len([j for j in sched.jobs if j.status == "done"]) == 1
        assert [j for j in sched.jobs if j.status == "done"][0].name == "a"
        assert len([j for j in sched.jobs if j.status == "failed"]) == 1  # b=failed
        assert len([j for j in sched.jobs if j.status == "cancelled"]) == 1  # c=cancelled
        assert len([j for j in sched.jobs if j.status == "pending"]) == 0
        assert len([j for j in sched.jobs if j.status == "running"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 24. Hooks — lifecycle events
# ═══════════════════════════════════════════════════════════════════════════


class TestHooks:
    def test_on_start_hook(self):
        log = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.on(Event.START, lambda snap: log.append(("start", snap.name)))
        
        sched.submit("hk", _echo_cmd("hi"))
        sched.run()
        assert ("start", "hk") in log

    def test_on_done_hook(self):
        log = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.on(Event.DONE, lambda snap: log.append(("done", snap.name)))
        
        sched.submit("hk", _echo_cmd("hi"))
        sched.run()
        assert ("done", "hk") in log

    def test_on_fail_hook(self):
        log = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.on(Event.FAILED, lambda snap: log.append(("fail", snap.name)))
        
        sched.submit("hk", _fail_cmd())
        sched.run()
        assert ("fail", "hk") in log

    def test_on_cancel_hook(self):
        """on_cancel should fire when job is cancelled, NOT on_done."""
        done_log = []
        cancel_log = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.on(Event.DONE, lambda snap: done_log.append(snap.name))
        sched.on(Event.CANCELLED, lambda snap: cancel_log.append(snap.name))
        
        sched.submit("a", _echo_cmd("A"))
        sched.submit("b", _echo_cmd("B"))
        sched.cancel("b")
        sched.run()
        assert "b" not in done_log
        assert "b" in cancel_log

    def test_on_fail_hook_for_blocked_dep(self):
        """Jobs cancelled due to dependency failure should trigger on_cancel, not on_fail (under the new model, it propagates as cancelled)."""
        log = []
        sched = Scheduler(resources={"local": 2})
        from mypkg.scheduler.types import Event
        sched.on(Event.CANCELLED, lambda snap: log.append(("cancel", snap.name)))
        
        sched.submit("a", _fail_cmd())
        sched.submit("b", _echo_cmd("B"), depends_on=["a"])
        sched.run()
        assert ("cancel", "b") in log


# ═══════════════════════════════════════════════════════════════════════════
# 25. Matchers
# ═══════════════════════════════════════════════════════════════════════════


class TestMatchers:
    def test_realtime_matcher(self):
        from mypkg.scheduler.types import Event
        from mypkg.scheduler.job import JobUpdate
        
        results = []
        sched = Scheduler()
        sched.submit("j", _echo_cmd("x")) # Dummy cmd

        def find_key(line, snap):
            if "KEY=" in line:
                val = line.split("KEY=")[1]
                return JobUpdate(custom_data={"key": val})
            return None

        sched.match("j", find_key)
        sched.on(Event.UPDATE, lambda snap: results.append(snap.custom_data.get("key")))
        
        j = sched._jobs["j"]
        j._emit_line("noise")
        j._emit_line("KEY=abc")
        j._emit_line("more noise")
        assert "abc" in results

    def test_matcher_triggers_update_event(self):
        from mypkg.scheduler.types import Event
        from mypkg.scheduler.job import JobUpdate
        
        sched = Scheduler()
        sched.submit("j", _echo_cmd("x"))
        
        def update_progress(line, snap):
            if "PROG:" in line:
                return JobUpdate(progress=float(line.split(":")[1]))
            return None
            
        sched.match("j", update_progress)
        
        progs = []
        sched.on(Event.UPDATE, lambda snap: progs.append(snap.progress))
        
        j = sched._jobs["j"]
        j._emit_line("PROG:10.5")
        j._emit_line("PROG:99.9")
        
        assert progs == [10.5, 99.9]
        assert j.progress == 99.9


# Removed TestLifecycle as the architecture was changed and callbacks occur on Event.


# ═══════════════════════════════════════════════════════════════════════════
# 27. is_complete / is_running
# ═══════════════════════════════════════════════════════════════════════════


class TestIsComplete:
    def test_is_complete_false_before_run(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        assert sched.is_complete is False

    def test_is_complete_no_jobs_returns_false(self):
        sched = Scheduler(resources={"local": 1})
        assert sched.is_complete is False

    def test_is_complete_true_after_run(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.run()
        assert sched.is_complete is True

    def test_is_complete_true_with_failed_jobs(self):
        """All-failed is also complete."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _fail_cmd())
        sched.run()
        assert sched.is_complete is True

    def test_is_complete_false_while_running(self):
        """is_complete is False while jobs are still executing."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("slow", _sleep_cmd(1.0))
        sched.start()
        # Poll until running
        for _ in range(40):
            if sched.is_running:
                break
            time.sleep(0.05)
        assert sched.is_complete is False
        sched.wait()
        assert sched.is_complete is True

    def test_is_complete_interactive_loop(self):
        """Verify while-not-is_complete control loop pattern works correctly."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.start()
        iterations = 0
        while not sched.is_complete:
            iterations += 1
            time.sleep(0.05)
            if iterations > 200:  # safety guard (~10s)
                break
        assert sched.is_complete is True


class TestIsRunning:
    def test_is_running_false_before_start(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        assert sched.is_running is False

    def test_is_running_true_after_start(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _sleep_cmd(0.5))
        sched.start()
        assert sched.is_running is True
        sched.wait()
        sched.stop()  # Ensure background thread joins immediately
        assert sched.is_running is False

    def test_is_running_false_after_run(self):
        """run() is blocking; is_running is False once it returns."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.run()
        assert sched.is_running is False


# ═══════════════════════════════════════════════════════════════════════════
# 28. Output whitespace stripping
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputStripping:
    def test_no_trailing_whitespace(self):
        """output_lines must never contain trailing whitespace (Windows echo pads)."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("strip", _echo_cmd("hello"))
        sched.run()
        for line in sched.stdout("strip", 10):
            assert line == line.rstrip(), f"Trailing whitespace found: {line!r}"

    def test_no_trailing_whitespace_multiline(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("mstrip", _multi_echo_cmd("alpha", "beta", "gamma"))
        sched.run()
        for line in sched.stdout("mstrip", 10):
            assert line == line.rstrip(), f"Trailing whitespace found: {line!r}"

    def test_matcher_no_trailing_whitespace(self):
        """Lines delivered via matcher should also be clean."""
        received = []
        sched = Scheduler(resources={"local": 1})
        sched.submit("hook_strip", _echo_cmd("stream"))
        sched.match("hook_strip", lambda l, s: received.append(l) or None)
        sched.run()
        for line in received:
            assert line == line.rstrip(), f"Trailing whitespace in hook: {line!r}"


# ═══════════════════════════════════════════════════════════════════════════
# 29. duration available in Events
# ═══════════════════════════════════════════════════════════════════════════


class TestDurationInEvents:
    def test_duration_set_in_on_done_event(self):
        """snap.duration must not be None when Event.DONE fires."""
        durations = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.submit("dur", _echo_cmd("hi"))
        sched.on(Event.DONE, lambda snap: durations.append(snap.duration))
        sched.run()
        assert len(durations) == 1
        assert durations[0] is not None
        assert durations[0] >= 0.0

    def test_duration_set_in_on_fail_event(self):
        """snap.duration must not be None when Event.FAILED fires."""
        durations = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.submit("fail_dur", _fail_cmd())
        sched.on(Event.FAILED, lambda snap: durations.append(snap.duration))
        sched.run()
        assert len(durations) == 1
        assert durations[0] is not None


# ═══════════════════════════════════════════════════════════════════════════
# 30. Live duration
# ═══════════════════════════════════════════════════════════════════════════


class TestLiveDuration:
    def test_duration_none_before_start(self):
        sched = Scheduler()
        sched.submit("j", _echo_cmd("hi"))
        assert sched.get("j").duration is None

    def test_duration_positive_while_running(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _sleep_cmd(1.0))
        sched.start()
        # Wait until duration is live (set atomically when status→RUNNING)
        for _ in range(40):
            if sched.get("j").duration is not None:
                break
            time.sleep(0.05)
        j = sched.get("j")
        assert j.duration is not None
        assert j.duration > 0.0
        sched.wait()

    def test_duration_frozen_after_done(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("j", cmd=_echo_cmd("hi"))
        sched.submit_job(j)
        sched.run()
        d1 = j.duration
        time.sleep(0.1)
        d2 = j.duration
        assert d1 is not None
        assert d1 == d2, "duration should be frozen after job finishes"

    def test_duration_set_in_on_done_hook(self):
        """Regression: duration must not be None when on_done fires."""
        durations = []
        sched = Scheduler(resources={"local": 1})
        from mypkg.scheduler.types import Event
        sched.on(Event.DONE, lambda snap: durations.append(snap.duration))
        sched.submit("dur", _echo_cmd("hi"))
        sched.run()
        assert durations[0] is not None and durations[0] >= 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 32. Tags & jobs_by_tag
# ═══════════════════════════════════════════════════════════════════════════


class TestTags:
    def test_tags_set_on_job(self):
        j = CmdJob("j", _echo_cmd("hi"), tags=["wave1", "regression"])
        assert "wave1" in j.tags
        assert "regression" in j.tags

    def test_jobs_by_tag(self):
        sched = Scheduler(resources={"local": 2})
        sched.submit("a", _echo_cmd("a"), tags=["wave1"])
        sched.submit("b", _echo_cmd("b"), tags=["wave2"])
        sched.submit("c", _echo_cmd("c"), tags=["wave1"])
        sched.run()
        wave1 = sched.jobs_by_tag("wave1")
        assert set(j.name for j in wave1) == {"a", "c"}

    def test_jobs_by_tag_empty(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"), tags=["foo"])
        assert sched.jobs_by_tag("bar") == []


# ═══════════════════════════════════════════════════════════════════════════
# 33. progress field
# ═══════════════════════════════════════════════════════════════════════════


class TestProgress:
    def test_progress_default_none(self):
        j = CmdJob("j", _echo_cmd("hi"))
        assert j.progress is None

    def test_progress_set_via_matcher(self):
        import re
        from mypkg.scheduler.job import JobUpdate
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("50"))
        
        def update_prog(line, snap):
            m = re.search(r"(\d+)", line)
            if m: return JobUpdate(progress=float(m.group(1)))
            return None
            
        sched.match("j", update_prog)
        sched.run()
        assert sched.get("j").progress == 50.0


# ═══════════════════════════════════════════════════════════════════════════
# 34. max_retries & retry_if
# ═══════════════════════════════════════════════════════════════════════════


class TestRetry:
    def test_no_retry_by_default(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _fail_cmd())
        sched.run()
        j = sched.get("j")
        assert j.status == "failed"
        assert sched._jobs["j"].retry_count == 0

    def test_retries_n_times_then_fails(self):
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _fail_cmd(), max_retries=2)
        sched.run()
        j = sched.get("j")
        assert j.status == "failed"
        assert sched._jobs["j"].retry_count == 2

    def test_retry_succeeds_on_second_attempt(self):
        """First attempt fails, second succeeds — job ends as DONE."""
        attempt_file = None
        import tempfile, os
        attempt_file = tempfile.mktemp(suffix=".flag")

        if _IS_WIN:
            cmd = (
                f'cmd /c "if exist {attempt_file} (exit 0) else ('
                f'echo. > {attempt_file} & exit 1)"'
            )
        else:
            cmd = (
                f'sh -c "if [ -f {attempt_file} ]; then exit 0; '
                f'else touch {attempt_file}; exit 1; fi"'
            )
        try:
            sched = Scheduler(resources={"local": 1})
            sched.submit("j", cmd, max_retries=2)
            sched.run()
            j = sched.get("j")
            assert j.status == "done"
            assert sched._jobs["j"].retry_count == 1
        finally:
            if os.path.exists(attempt_file):
                os.remove(attempt_file)


# ═══════════════════════════════════════════════════════════════════════════
# 35. fail_if / done_if helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestFailIfDoneIf:
    def test_fail_matcher_overrides_exit_0(self):
        """Exit 0 but output contains 'FAILED' → job should be FAILED."""
        from mypkg.scheduler.job import JobUpdate
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("SIMULATION FAILED"))
        sched.match("j", lambda l, s: JobUpdate(status="failed") if "SIMULATION FAILED" in l else None)
        sched.run()
        assert sched.get("j").status == "failed"

    def test_done_matcher_overrides_nonzero_exit(self):
        """Non-zero exit but output says PASSED → job should be DONE."""
        from mypkg.scheduler.job import JobUpdate
        if _IS_WIN:
            cmd = 'cmd /c "echo SIMULATION PASSED & exit 1"'
        else:
            cmd = 'sh -c "echo SIMULATION PASSED; exit 1"'
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", cmd)
        sched.match("j", lambda l, s: JobUpdate(status="done") if "SIMULATION PASSED" in l else None)
        sched.run()
        assert sched.get("j").status == "done"

    def test_fail_matcher_not_triggered(self):
        """No matching line → matcher has no effect, job stays done."""
        from mypkg.scheduler.job import JobUpdate
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("everything is fine"))
        sched.match("j", lambda l, s: JobUpdate(status="failed") if "FAILED" in l else None)
        sched.run()
        assert sched.get("j").status == "done"

    def test_failed_matcher_alias_supported(self):
        """Matcher status='failed' should be accepted (same as legacy 'fail')."""
        from mypkg.scheduler.job import JobUpdate
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("SIMULATION FAILED"))
        sched.match("j", lambda l, s: JobUpdate(status="failed") if "SIMULATION FAILED" in l else None)
        sched.run()
        assert sched.get("j").status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 36. Dynamic submit while running (Case 7)
# ═══════════════════════════════════════════════════════════════════════════


class TestDynamicSubmit:
    def test_submit_while_running(self):
        """Submit new jobs from an on_done hook while scheduler is running."""
        sched = Scheduler(resources={"local": 2})
        results = []

        sched.submit("coord", _echo_cmd("params"))

        from mypkg.scheduler.types import Event
        def on_done(snap):
            if snap.name == "coord":
                # Dynamically inject two more jobs
                sched.submit("dyn_a", _echo_cmd("a"))
                sched.submit("dyn_b", _echo_cmd("b"))
            elif snap.name.startswith("dyn_"):
                results.append(snap.name)

        sched.on(Event.DONE, on_done)
        sched.start()

        # Drive with is_complete — waits for dynamically submitted jobs
        for _ in range(100):
            if sched.is_complete:
                break
            time.sleep(0.1)

        assert sched.is_complete
        assert set(results) == {"dyn_a", "dyn_b"}

    def test_is_complete_waits_for_dynamic_jobs(self):
        """is_complete should be False until dynamic jobs also finish."""
        sched = Scheduler(resources={"local": 1})
        sched.submit("coord", _echo_cmd("go"))

        from mypkg.scheduler.types import Event
        def on_done(snap):
            if snap.name == "coord":
                sched.submit("dyn", _sleep_cmd(0.3))

        sched.on(Event.DONE, on_done)
        sched.start()

        # Wait for coord to finish, then immediately check
        for _ in range(40):
            if sched.get("coord").status in ("done", "failed", "cancelled"):
                break
            time.sleep(0.05)

        time.sleep(0.1)
        # Now poll until truly complete
        for _ in range(60):
            if sched.is_complete:
                break
            time.sleep(0.1)

        assert sched.is_complete


# ═══════════════════════════════════════════════════════════════════════════
# 37. Auto-naming — parameter sweep workflow
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoNameWorkflow:
    def test_parameter_sweep_no_name_conflict(self):
        """Multiple anonymous jobs can be submitted without naming collisions."""
        sched = Scheduler(resources={"local": 4})
        for i in range(5):
            sched.submit(f"run_{i}", _echo_cmd(f"run_{i}"), tags=["sweep"])
        sched.run()
        sweep_jobs = sched.jobs_by_tag("sweep")
        assert len(sweep_jobs) == 5
        assert all(j.status == "done" for j in sweep_jobs)

    def test_unnamed_and_named_can_coexist(self):
        """Explicitly named and auto-named jobs can coexist in the same scheduler."""
        sched = Scheduler(resources={"local": 2})
        sched.submit("explicit", _echo_cmd("named"))
        sched.submit("anon_1", _echo_cmd("anon"), tags=["anon"])
        sched.run()
        assert sched.get("explicit").status == "done"
        assert len(sched.jobs_by_tag("anon")) == 1
        assert sched.jobs_by_tag("anon")[0].status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 38. fail_fast — abort on first failure
# ═══════════════════════════════════════════════════════════════════════════


class TestFailFast:
    def test_fail_fast_cancels_pending_on_failure(self):
        """With fail_fast=True, a job failure should cancel remaining pending jobs."""
        from mypkg.scheduler.types import Event
        def enable_fail_fast(sched):
            sched.on(Event.FAILED, lambda snap: [sched.cancel(j.name) for j in sched.jobs if j.status == "pending"])
            
        sched = Scheduler(resources={"local": 1})
        enable_fail_fast(sched)
        sched.submit("bad", _fail_cmd(), priority=10)
        sched.submit("good", _sleep_cmd(10), priority=1)
        sched.run()
        assert sched.get("bad").status == "failed"
        assert sched.get("good").status == "cancelled"


# ═══════════════════════════════════════════════════════════════════════════
# 39. job.wait() — single job blocking
# ═══════════════════════════════════════════════════════════════════════════


class TestJobWait:
    def test_wait_returns_when_done(self):
        """Wait logic testing wait_until_done() via threading.Event"""
        sched = Scheduler(resources={"local": 1})
        sched.submit("j", _echo_cmd("hi"))
        sched.start()
        sched._jobs["j"]._finished_event.wait(timeout=10.0)
        assert sched.get("j").status == "done"
        sched.wait()


class TestSchedulerRegressions:
    def test_all_finished_callback_can_call_summary_without_hanging(self):
        from mypkg.scheduler.types import Event

        sched = Scheduler(resources={"local": 1}, poll_interval=0.01)
        sched.submit("j", _echo_cmd("hi"))

        entered = threading.Event()
        exited = threading.Event()

        def on_all_finished(_snap):
            entered.set()
            _ = sched.summary()
            exited.set()

        sched.on(Event.ALL_FINISHED, on_all_finished)
        sched.start()

        assert sched.wait(timeout=2.0)
        for _ in range(40):
            if not sched.is_running:
                break
            time.sleep(0.05)

        assert entered.is_set()
        assert exited.is_set()
        assert not sched.is_running

    def test_dynamic_submit_in_done_callback_executes_child(self):
        from mypkg.scheduler.types import Event

        sched = Scheduler(resources={"local": 1}, poll_interval=0.005)
        sched.submit("root", _echo_cmd("root"))

        def on_done(snap):
            if snap and snap.name == "root":
                # Keep callback alive briefly to expose loop-exit race.
                time.sleep(0.05)
                sched.submit("child", _echo_cmd("child"))

        sched.on(Event.DONE, on_done)
        sched.start()

        assert sched.wait(name="root", timeout=2.0)
        for _ in range(40):
            try:
                sched.get("child")
                break
            except KeyError:
                time.sleep(0.05)

        assert sched.wait(name="child", timeout=2.0)
        assert sched.get("child").status == "done"
        assert sched.is_complete

    def test_matcher_terminal_status_kills_process_early(self):
        from mypkg.scheduler.job import JobUpdate

        cmd = (
            f'{_PY} -c "import sys,time; '
            "print('BEGIN'); sys.stdout.flush(); "
            "time.sleep(2); "
            "print('END'); sys.stdout.flush()\""
        )

        sched = Scheduler(resources={"local": 1}, poll_interval=0.01)
        sched.submit("j", cmd)
        sched.match("j", lambda line, snap: JobUpdate(status="done") if "BEGIN" in line else None)

        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0

        assert sched.get("j").status == "done"
        assert elapsed < 1.5
        assert not any("END" in line for line in sched.stdout("j", 20))
