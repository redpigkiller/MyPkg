import pytest
import os
import logging
from mypkg.utils.stage_logger import StageLogger, ErrorLevel

@pytest.fixture
def logger():
    log = StageLogger("RefinementTestLogger")
    log.console_enabled = False 
    yield log
    # Cleanup handlers to release files
    for h in log.logger.handlers:
        h.close()
        log.logger.removeHandler(h)

def test_reset(logger):
    logger.set_stage("Stage1")
    logger.error("Error 1")
    assert len(logger.issues) == 1
    assert logger.current_stage == "Stage1"
    
    logger.reset()
    
    assert len(logger.issues) == 0
    assert logger.current_stage is None
    assert logger.stage_history == []

def test_get_issues(logger):
    logger.set_stage("Stage1")
    logger.warning("Warning 1", track=True)
    logger.error("Error 1")
    
    # Filter by stage
    s1_issues = logger.get_issues(stage="Stage1")
    assert len(s1_issues) == 2, f"Expected 2 issues (Warning 1, Error 1), got {len(s1_issues)}: {[str(i) for i in s1_issues]}"
    assert s1_issues[0].message == "Warning 1"
    
    # Filter by level
    errors = logger.get_issues(level=ErrorLevel.ERROR)
    assert len(errors) == 1
    assert errors[0].message == "Error 1"
    
    warnings = logger.get_issues(level="WARNING")
    assert len(warnings) == 1
    assert warnings[0].message == "Warning 1"
    
    # Filter by list of levels
    major_issues = logger.get_issues(level=[ErrorLevel.WARNING, ErrorLevel.ERROR])
    assert len(major_issues) == 2
    
    # Filter by list of strings
    str_issues = logger.get_issues(level=["warning", "error"])
    assert len(str_issues) == 2

def test_debug(logger):
    logger.set_stage("Stage1")
    logger.debug("Debug 1", track=True)
    logger.debug("Debug 2") # track defaults to False
    
    debug_issues = logger.get_issues(level=ErrorLevel.DEBUG)
    assert len(debug_issues) == 1
    assert debug_issues[0].message == "Debug 1"

def test_reset_keep_handlers(logger, tmp_path):
    log_file = tmp_path / "test_keep_hndlr.log"
    logger.add_file_handler(str(log_file))
    
    initial_handler_count = len(logger.logger.handlers)
    assert initial_handler_count > 0
    
    logger.reset(keep_handlers=True)
    
    assert len(logger.logger.handlers) == initial_handler_count
    
def test_summary_returns_bool(logger):
    logger.set_stage("Stage1")
    assert logger.summary() is True
    
    logger.error("Got an error")
    assert logger.summary() is False

def test_log_rotation(tmp_path):
    log_file = tmp_path / "test_rotate.log"
    log = StageLogger("RotationTestLogger")
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
    log = StageLogger("FmtTestLogger")
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

