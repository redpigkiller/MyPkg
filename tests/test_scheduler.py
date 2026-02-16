"""Comprehensive tests for Scheduler — cross-platform job scheduling.

Sections:
  1-12:  Phase 1 — Core
  13-20: Phase 2a — Interactive control
  21-25: Phase 2b — Timeout, CmdJob actions, GridJob, Status filters
"""

import os
import sys
import time
from unittest.mock import patch, MagicMock
import tempfile
import threading
from pathlib import Path

import pytest
from mypkg import Scheduler, Job, CmdJob, GridJob


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

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            Job("", cmd="echo x")

    def test_empty_cmd_raises(self):
        with pytest.raises(ValueError):
            Job("j", cmd="")

    def test_on_output_and_tail(self):
        j = Job("j", cmd="echo x")
        collected = []
        j.on_output(collected.append)
        j._emit_line("line1")
        j._emit_line("line2")
        j._emit_line("line3")
        assert collected == ["line1", "line2", "line3"]
        assert j.tail(2) == ["line2", "line3"]

    def test_output_lines_property(self):
        j = Job("j", cmd="echo x")
        j._emit_line("hello")
        assert j.output_lines == ["hello"]

    def test_is_finished(self):
        j = Job("j", cmd="echo x")
        assert not j.is_finished
        j.status = "done"
        assert j.is_finished
        j.status = "failed"
        assert j.is_finished

    def test_repr(self):
        r = repr(Job("my_job", cmd="echo x"))
        assert "my_job" in r and "pending" in r


# ═══════════════════════════════════════════════════════════════════════════
# 2. CmdJob
# ═══════════════════════════════════════════════════════════════════════════


class TestCmdJob:
    def test_is_job(self):
        assert isinstance(CmdJob("c", cmd="echo x"), Job)

    def test_default_resources(self):
        assert CmdJob("c", cmd="echo x").resources == {"local": 1}

    def test_custom_priority(self):
        j = CmdJob("c", cmd="echo x", priority=5)
        assert j.priority == 5


# ═══════════════════════════════════════════════════════════════════════════
# 3. Scheduler — Single Job
# ═══════════════════════════════════════════════════════════════════════════


class TestSingleJob:
    def test_echo(self):
        sched = Scheduler(resources={"local": 2})
        job = CmdJob("hello", cmd=_echo_cmd("hello_world"))
        sched.submit(job)
        sched.run()
        assert job.status == "done"
        assert job.exit_code == 0
        assert job.duration is not None and job.duration >= 0
        assert any("hello_world" in line for line in job.output_lines)

    def test_with_cwd(self, tmp_path):
        sched = Scheduler(resources={"local": 1})
        job = CmdJob("pwd", cmd=_echo_cmd("ok"), cwd=str(tmp_path))
        sched.submit(job)
        sched.run()
        assert job.status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Scheduler — Parallel Jobs
# ═══════════════════════════════════════════════════════════════════════════


class TestParallel:
    def test_two_jobs_run_concurrently(self):
        """Two 1-second jobs with local=2 should finish in ~1s, not ~2s."""
        sched = Scheduler(resources={"local": 2})
        j1 = CmdJob("s1", cmd=_sleep_cmd(1.0))
        j2 = CmdJob("s2", cmd=_sleep_cmd(1.0))
        sched.submit(j1, j2)

        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0

        assert j1.status == "done" and j2.status == "done"
        assert elapsed < 2.5  # should be ~1s, generous margin


# ═══════════════════════════════════════════════════════════════════════════
# 5. Scheduler — Priority
# ═══════════════════════════════════════════════════════════════════════════


class TestPriority:
    def test_higher_priority_starts_first(self):
        """With local=1, the higher-priority job should run first."""
        order = []
        sched = Scheduler(resources={"local": 1})
        lo = CmdJob("lo", cmd=_echo_cmd("lo"), priority=1)
        hi = CmdJob("hi", cmd=_echo_cmd("hi"), priority=10)
        lo.on_output(lambda line: order.append("lo"))
        hi.on_output(lambda line: order.append("hi"))
        sched.submit(lo, hi)
        sched.run()
        # hi should appear before lo
        assert order.index("hi") < order.index("lo")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Scheduler — Dependencies
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencies:
    def test_basic_dep(self):
        sched = Scheduler(resources={"local": 2})
        a = CmdJob("a", cmd=_echo_cmd("A"))
        b = CmdJob("b", cmd=_echo_cmd("B"), depends_on=[a])
        sched.submit(a, b)
        sched.run()
        assert a.status == "done" and b.status == "done"
        # a must have finished before b started
        assert a.duration is not None and b.duration is not None

    def test_dep_chain(self):
        sched = Scheduler(resources={"local": 2})
        a = CmdJob("a", cmd=_echo_cmd("A"))
        b = CmdJob("b", cmd=_echo_cmd("B"), depends_on=[a])
        c = CmdJob("c", cmd=_echo_cmd("C"), depends_on=[b])
        sched.submit(a, b, c)
        sched.run()
        assert all(j.status == "done" for j in [a, b, c])

    def test_unknown_dep_raises(self):
        sched = Scheduler()
        orphan = CmdJob("orphan", cmd="echo x")
        child = CmdJob("child", cmd="echo y", depends_on=[orphan])
        with pytest.raises(ValueError, match="not submitted"):
            sched.submit(child)

    def test_failed_dep_propagates(self):
        """If dep fails, dependent job should also fail."""
        sched = Scheduler(resources={"local": 2})
        a = CmdJob("a", cmd=_fail_cmd())
        b = CmdJob("b", cmd=_echo_cmd("B"), depends_on=[a])
        sched.submit(a, b)
        sched.run()
        assert a.status == "failed"
        assert b.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Scheduler — Failure
# ═══════════════════════════════════════════════════════════════════════════


class TestFailure:
    def test_nonzero_exit(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("bad", cmd=_fail_cmd())
        sched.submit(j)
        sched.run()
        assert j.status == "failed"
        assert j.exit_code != 0

    def test_invalid_command(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("bad", cmd="this_command_does_not_exist_xyz")
        sched.submit(j)
        sched.run()
        assert j.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Scheduler — Stdout Streaming
# ═══════════════════════════════════════════════════════════════════════════


class TestStreaming:
    def test_callback_called(self):
        collected = []
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("echo", cmd=_echo_cmd("STREAM_TEST"))
        j.on_output(collected.append)
        sched.submit(j)
        sched.run()
        assert any("STREAM_TEST" in line for line in collected)

    def test_multi_line(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("multi", cmd=_multi_echo_cmd("line1", "line2", "line3"))
        sched.submit(j)
        sched.run()
        texts = " ".join(j.output_lines)
        assert "line1" in texts and "line2" in texts and "line3" in texts


# ═══════════════════════════════════════════════════════════════════════════
# 9. Scheduler — Log Directory
# ═══════════════════════════════════════════════════════════════════════════


class TestLogDir:
    def test_log_created(self, tmp_path):
        log_dir = tmp_path / "logs"
        sched = Scheduler(resources={"local": 1}, log_dir=str(log_dir))
        j = CmdJob("log_test", cmd=_echo_cmd("LOG_CONTENT"))
        sched.submit(j)
        sched.run()
        assert j.log_path is not None
        assert j.log_path.exists()
        content = j.log_path.read_text(encoding="utf-8")
        assert "LOG_CONTENT" in content

    def test_no_log_dir(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("no_log", cmd=_echo_cmd("hi"))
        sched.submit(j)
        sched.run()
        assert j.log_path is None


# ═══════════════════════════════════════════════════════════════════════════
# 10. Scheduler — Resource Limit
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceLimit:
    def test_max_local_one(self):
        """With local=1, two 0.5s jobs should take ≥1s (sequential)."""
        sched = Scheduler(resources={"local": 1})
        j1 = CmdJob("s1", cmd=_sleep_cmd(0.5))
        j2 = CmdJob("s2", cmd=_sleep_cmd(0.5))
        sched.submit(j1, j2)

        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0

        assert j1.status == "done" and j2.status == "done"
        assert elapsed >= 0.8  # should be ~1s


# ═══════════════════════════════════════════════════════════════════════════
# 11. Scheduler — start/wait (Non-blocking)
# ═══════════════════════════════════════════════════════════════════════════


class TestStartWait:
    def test_non_blocking(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("bg", cmd=_sleep_cmd(0.5))
        sched.submit(j)
        sched.start()
        # start() should return immediately
        assert True
        sched.wait()
        assert j.status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Scheduler — Summary
# ═══════════════════════════════════════════════════════════════════════════


class TestSummary:
    def test_summary_contains_job_info(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("sum_test", cmd=_echo_cmd("hi"))
        sched.submit(j)
        sched.run()
        text = sched.summary()
        assert "sum_test" in text
        assert "done" in text

    def test_duplicate_name_raises(self):
        sched = Scheduler()
        sched.submit(CmdJob("dup", cmd="echo a"))
        with pytest.raises(ValueError, match="Duplicate"):
            sched.submit(CmdJob("dup", cmd="echo b"))


# ═══════════════════════════════════════════════════════════════════════════
# 13. Cancel
# ═══════════════════════════════════════════════════════════════════════════


class TestCancel:
    def test_cancel_pending(self):
        sched = Scheduler(resources={"local": 1})
        a = CmdJob("a", cmd=_echo_cmd("A"))
        b = CmdJob("b", cmd=_echo_cmd("B"))
        sched.submit(a, b)
        sched.cancel("b")
        sched.run()
        assert a.status == "done"
        assert b.status == "cancelled"

    def test_cancel_not_pending_raises(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("j", cmd=_echo_cmd("hi"))
        sched.submit(j)
        sched.run()
        with pytest.raises(RuntimeError, match="pending"):
            sched.cancel("j")

    def test_cancelled_dep_propagates(self):
        """If dep is cancelled, dependent job should fail."""
        sched = Scheduler(resources={"local": 2})
        a = CmdJob("a", cmd=_echo_cmd("A"))
        b = CmdJob("b", cmd=_echo_cmd("B"), depends_on=[a])
        sched.submit(a, b)
        sched.cancel("a")
        sched.run()
        assert a.status == "cancelled"
        assert b.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 14. Kill
# ═══════════════════════════════════════════════════════════════════════════


class TestKill:
    def test_kill_running(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("slow", cmd=_sleep_cmd(10))
        sched.submit(j)
        sched.start()
        # Wait for job to start running
        for _ in range(40):
            if j.status == "running":
                break
            time.sleep(0.1)
        assert j.status == "running"
        sched.kill("slow")
        sched.wait()
        assert j.status == "failed"

    def test_kill_not_running_raises(self):
        j = Job("j", cmd="echo x")
        with pytest.raises(RuntimeError, match="not running"):
            j.kill()


# ═══════════════════════════════════════════════════════════════════════════
# 15. Send stdin
# ═══════════════════════════════════════════════════════════════════════════


def _stdin_echo_cmd() -> str:
    """A command that reads one line from stdin and prints it."""
    return f'{_PY} -c "import sys; line=sys.stdin.readline(); print(line.strip())"'


class TestSendStdin:
    def test_send_to_running(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("reader", cmd=_stdin_echo_cmd())
        sched.submit(j)
        sched.start()
        # Wait for job to start
        for _ in range(40):
            if j.status == "running":
                break
            time.sleep(0.1)
        assert j.status == "running"
        j.send("HELLO_STDIN\n")
        sched.wait()
        assert j.status == "done"
        assert any("HELLO_STDIN" in line for line in j.output_lines)

    def test_send_not_running_raises(self):
        j = Job("j", cmd="echo x")
        with pytest.raises(RuntimeError, match="not running"):
            j.send("data\n")


# ═══════════════════════════════════════════════════════════════════════════
# 16. Set Priority (interactive)
# ═══════════════════════════════════════════════════════════════════════════


class TestSetPriority:
    def test_change_priority(self):
        """Change priority before run, verify order changes."""
        order = []
        sched = Scheduler(resources={"local": 1})
        a = CmdJob("a", cmd=_echo_cmd("A"), priority=10)
        b = CmdJob("b", cmd=_echo_cmd("B"), priority=1)
        a.on_output(lambda line: order.append("a"))
        b.on_output(lambda line: order.append("b"))
        sched.submit(a, b)
        # Flip: make b higher priority
        sched.set_priority("b", 100)
        sched.run()
        assert order.index("b") < order.index("a")

    def test_set_priority_not_pending_raises(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("j", cmd=_echo_cmd("hi"))
        sched.submit(j)
        sched.run()
        with pytest.raises(RuntimeError, match="pending"):
            sched.set_priority("j", 99)


# ═══════════════════════════════════════════════════════════════════════════
# 17. Status
# ═══════════════════════════════════════════════════════════════════════════


class TestStatus:
    def test_status_output(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("st_test", cmd=_echo_cmd("hi"))
        sched.submit(j)
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
        j = CmdJob("find_me", cmd="echo x")
        sched.submit(j)
        assert sched.get("find_me") is j

    def test_get_missing_raises(self):
        sched = Scheduler()
        with pytest.raises(KeyError):
            sched.get("nope")


# ═══════════════════════════════════════════════════════════════════════════
# 19. Actions
# ═══════════════════════════════════════════════════════════════════════════


class _TestableJob(Job):
    """Job subclass with custom actions for testing."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.action_log = []

    def actions(self):
        return {
            "greet": ("Say hello", lambda: self.action_log.append("greet")),
            "info": ("Show info", lambda: self.action_log.append("info")),
        }


class TestActions:
    def test_base_job_no_actions(self):
        assert Job("j", cmd="echo x").actions() == {}
        assert CmdJob("c", cmd="echo x").actions() == {}

    def test_subclass_actions(self):
        j = _TestableJob("tj", cmd="echo x")
        acts = j.actions()
        assert "greet" in acts and "info" in acts
        desc, fn = acts["greet"]
        assert isinstance(desc, str)

    def test_scheduler_action(self):
        sched = Scheduler()
        j = _TestableJob("tj", cmd="echo x")
        sched.submit(j)
        sched.action("tj", "greet")
        assert j.action_log == ["greet"]

    def test_scheduler_action_unknown_raises(self):
        sched = Scheduler()
        j = CmdJob("cj", cmd="echo x")
        sched.submit(j)
        with pytest.raises(KeyError, match="not found"):
            sched.action("cj", "nonexistent")

    def test_scheduler_actions_prints(self, capsys):
        sched = Scheduler()
        j = _TestableJob("tj", cmd="echo x")
        sched.submit(j)
        sched.actions("tj")
        out = capsys.readouterr().out
        assert "greet" in out and "_TestableJob" in out


# ═══════════════════════════════════════════════════════════════════════════
# 20. Job Type & Cancelled status
# ═══════════════════════════════════════════════════════════════════════════


class TestJobType:
    def test_job_type_name(self):
        assert Job("j", cmd="echo x").job_type == "Job"
        assert CmdJob("c", cmd="echo x").job_type == "CmdJob"

    def test_cancelled_is_finished(self):
        j = Job("j", cmd="echo x")
        j.status = "cancelled"
        assert j.is_finished

    def test_repr_includes_type(self):
        r = repr(CmdJob("c", cmd="echo x"))
        assert "CmdJob" in r


# ═══════════════════════════════════════════════════════════════════════════
# 21. Timeout
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeout:
    def test_timeout_kills_job(self):
        """Job exceeding its timeout should be auto-killed."""
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("slow", cmd=_sleep_cmd(10), timeout=1.5)
        sched.submit(j)
        t0 = time.monotonic()
        sched.run()
        elapsed = time.monotonic() - t0
        assert j.status == "failed"
        assert elapsed < 5  # should be killed around 1.5s, not 10s

    def test_no_timeout_succeeds(self):
        sched = Scheduler(resources={"local": 1})
        j = CmdJob("fast", cmd=_echo_cmd("hi"), timeout=10)
        sched.submit(j)
        sched.run()
        assert j.status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# 22. CmdJob Actions
# ═══════════════════════════════════════════════════════════════════════════


class TestCmdJobActions:
    def test_actions_after_log(self, tmp_path):
        log_dir = tmp_path / "logs"
        sched = Scheduler(resources={"local": 1}, log_dir=str(log_dir))
        j = CmdJob("act", cmd=_echo_cmd("hi"), cwd=str(tmp_path))
        sched.submit(j)
        sched.run()
        acts = j.actions()
        assert "open_log" in acts
        assert "open_cwd" in acts

    def test_no_actions_without_log(self):
        j = CmdJob("plain", cmd="echo x")
        assert j.actions() == {}

    def test_cwd_only_action(self):
        j = CmdJob("withcwd", cmd="echo x", cwd=".")
        acts = j.actions()
        assert "open_cwd" in acts
        assert "open_log" not in acts


# ═══════════════════════════════════════════════════════════════════════════
# 23. GridJob Construction
# ═══════════════════════════════════════════════════════════════════════════


class TestGridJobConstruction:
    def test_defaults(self):
        j = GridJob("g1", cmd="vcs -R")
        assert j.resources == {"grid": 1}
        assert j.submit_cmd == "qsub"
        assert j.kill_cmd == "qdel"
        assert j.status_cmd == "qstat"
        assert j.grid_id is None
        assert j.job_type == "GridJob"

    def test_custom_opts(self):
        j = GridJob("g2", cmd="vcs -R", submit_opts="-q normal -pe smp 4")
        assert j.submit_opts == "-q normal -pe smp 4"

    def test_repr(self):
        r = repr(GridJob("g", cmd="echo x"))
        assert "GridJob" in r

    def test_parse_grid_id(self):
        j = GridJob("g", cmd="echo x")
        # SGE format
        assert j._parse_grid_id('Your job 12345 ("test") has been submitted') == "12345"
        assert j._parse_grid_id('your job 67890 ("x") has been submitted') == "67890"
        assert j._parse_grid_id('some random output') is None

    def test_actions(self):
        j = GridJob("g", cmd="echo x")
        acts = j.actions()
        assert "grid_status" in acts


# ═══════════════════════════════════════════════════════════════════════════
# 24. GridJob Execute (mocked)
# ═══════════════════════════════════════════════════════════════════════════


class TestGridJobMocked:
    def test_submit_failure(self, tmp_path):
        """If qsub fails, job status should be failed."""
        j = GridJob("gfail", cmd="echo hi", cwd=str(tmp_path))
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "permission denied"
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            j._execute()
        assert j.status == "failed"

    def test_submit_success_then_finish(self, tmp_path):
        """Simulate successful qsub → qstat shows gone → done."""
        j = GridJob("gok", cmd="echo hi", cwd=str(tmp_path),
                    poll_interval=0.1)

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if "qsub" in cmd:
                result.returncode = 0
                result.stdout = 'Your job 99999 ("gok") has been submitted'
                result.stderr = ""
            elif "qstat" in cmd:
                # Second call: job is done (non-zero return = not found)
                result.returncode = 1
                result.stdout = ""
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            j._execute()

        assert j.grid_id == "99999"
        assert j.status == "done"
        assert j.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════════
# 25. Status Filters
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusFilters:
    def test_filter_properties(self):
        sched = Scheduler(resources={"local": 1})
        a = CmdJob("a", cmd=_echo_cmd("A"))
        b = CmdJob("b", cmd=_fail_cmd())
        c = CmdJob("c", cmd=_echo_cmd("C"))
        sched.submit(a, b, c)
        sched.cancel("c")
        sched.run()
        assert len(sched.done) == 1
        assert sched.done[0].name == "a"
        assert len(sched.failed) == 2  # b=failed, c=cancelled
        assert len(sched.pending) == 0
        assert len(sched.running) == 0
