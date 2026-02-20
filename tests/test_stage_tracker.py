import pytest
import os
import logging
from mypkg.utils.stage_tracker import StageTracker, StageFailedError, UsageError, ErrorLevel

@pytest.fixture
def tracker():
    # Use a unique name to avoid conflicts
    log = StageTracker("TestLogger")
    log.console_enabled = False # Disable console for tests to keep output clean
    yield log
    for h in log.logger.handlers:
        h.close()
        log.logger.removeHandler(h)

def test_strict_mode_flat_to_context(tracker):
    tracker.set_stage("Flat")
    with pytest.raises(UsageError):
        with tracker.stage("Context"):
            pass

def test_strict_mode_context_to_flat(tracker):
    with tracker.stage("Context"):
        pass
    with pytest.raises(UsageError):
        tracker.set_stage("Flat")

def test_nesting_prohibited(tracker):
    with pytest.raises(UsageError):
        with tracker.stage("Outer"):
             with tracker.stage("Inner"):
                 pass

def test_accumulated_error_flat(tracker):
    tracker.set_stage("Stage1")
    tracker.error("Error 1")
    tracker.error("Error 2")
    
    # Should raise when starting next stage
    with pytest.raises(StageFailedError) as exc:
        tracker.set_stage("Stage2")
    
    assert exc.value.stage == "Stage1"
    assert exc.value.error_count == 2

def test_accumulated_error_context(tracker):
    with pytest.raises(StageFailedError) as exc:
        with tracker.stage("Stage1"):
            tracker.error("Error 1")
    
    assert exc.value.stage == "Stage1"
    assert exc.value.error_count == 1

def test_fatal_error(tracker):
    tracker.set_stage("Stage1")
    with pytest.raises(StageFailedError):
        tracker.fatal("Fatal error")

def test_checkpoint(tracker):
    tracker.set_stage("Stage1")
    tracker.error("Error 1")
    
    with pytest.raises(StageFailedError):
        tracker.checkpoint()

def test_success_path(tracker):
    tracker.set_stage("Stage1")
    tracker.info("Info")
    tracker.warning("Warning")
    
    # Should not raise
    tracker.set_stage("Stage2")
    tracker.info("Done")

def test_summary_inclusion(tracker):
    tracker.set_stage("Stage1")
    tracker.info("Ignored Info")
    tracker.warning("Important Warning", track=True)
    tracker.warning("Warning")
    
    infos = [i for i in tracker.issues if i.level == ErrorLevel.INFO]
    assert len(infos) == 0
    
    warnings = [i for i in tracker.issues if i.level == ErrorLevel.WARNING]
    assert len(warnings) == 2

def test_artifacts(tracker):
    tracker.set_stage("Stage1")
    data = {"key": "value"}
    tracker.add_artifact(data)
    assert len(tracker.artifacts) == 1
    assert tracker.artifacts[0].value == data

def test_exc_info(tracker):
    tracker.set_stage("Stage1")
    try:
        1 / 0
    except ZeroDivisionError:
        tracker.error("Math error", exc_info=True)
    assert len(tracker.issues) == 1
    assert tracker.issues[0].message == "Math error"

def test_reset(tracker):
    tracker.set_stage("Stage1")
    tracker.error("Error 1")
    assert len(tracker.issues) == 1
    assert tracker.current_stage == "Stage1"
    
    tracker.reset()
    
    assert len(tracker.issues) == 0
    assert tracker.current_stage is None
    assert tracker.stage_history == []

def test_get_issues(tracker):
    tracker.set_stage("Stage1")
    tracker.warning("Warning 1", track=True)
    tracker.error("Error 1")
    
    # Filter by stage
    s1_issues = tracker.get_issues(stage="Stage1")
    assert len(s1_issues) == 2, f"Expected 2 issues (Warning 1, Error 1), got {len(s1_issues)}: {[str(i) for i in s1_issues]}"
    assert s1_issues[0].message == "Warning 1"
    
    # Filter by level
    errors = tracker.get_issues(level=ErrorLevel.ERROR)
    assert len(errors) == 1
    assert errors[0].message == "Error 1"
    
    warnings = tracker.get_issues(level="WARNING")
    assert len(warnings) == 1
    assert warnings[0].message == "Warning 1"
    
    # Filter by list of levels
    major_issues = tracker.get_issues(level=[ErrorLevel.WARNING, ErrorLevel.ERROR])
    assert len(major_issues) == 2
    
    # Filter by list of strings
    str_issues = tracker.get_issues(level=["warning", "error"])
    assert len(str_issues) == 2

def test_debug(tracker):
    tracker.set_stage("Stage1")
    tracker.debug("Debug 1", track=True)
    tracker.debug("Debug 2") # track defaults to False
    
    debug_issues = tracker.get_issues(level=ErrorLevel.DEBUG)
    assert len(debug_issues) == 1
    assert debug_issues[0].message == "Debug 1"

def test_reset_keep_handlers(tracker, tmp_path):
    log_file = tmp_path / "test_keep_hndlr.log"
    tracker.add_file_handler(str(log_file))
    
    initial_handler_count = len(tracker.logger.handlers)
    assert initial_handler_count > 0
    
    tracker.reset(keep_handlers=True)
    
    assert len(tracker.logger.handlers) == initial_handler_count
    
def test_summary_returns_bool(tracker):
    tracker.set_stage("Stage1")
    assert tracker.summary() is True
    
    tracker.error("Got an error")
    assert tracker.summary() is False

def test_log_rotation(tmp_path):
    log_file = tmp_path / "test_rotate.log"
    log = StageTracker("RotationTestLogger")
    log.console_enabled = False
    
    # max_bytes=100, backup_count=1
    log.add_file_handler(str(log_file), max_bytes=100, backup_count=1)
    
    # Write enough to rotate
    # Each line is roughly ~50-60 chars depending on format
    log.set_stage("RotateStage")
    for i in range(5):
        log.warning(f"Message {i}" * 5) # Long message
        
    # Check if backup file exists
    backup_file = tmp_path / "test_rotate.log.1"
    assert backup_file.exists()
    
    # Cleanup
    for h in log.logger.handlers:
        h.close()
        log.logger.removeHandler(h)

def test_formatting(tmp_path):
    log_file = tmp_path / "test_fmt.log"
    log = StageTracker("FmtTestLogger")
    log.console_enabled = False
    log.add_file_handler(str(log_file), level="DEBUG")
    
    log.set_stage("FmtStage")
    
    log.warning("Warning Message")
    
    content = log_file.read_text(encoding='utf-8')
    assert "Warning Message" in content
    
    # Cleanup
    for h in log.logger.handlers:
        h.close()
        log.logger.removeHandler(h)


