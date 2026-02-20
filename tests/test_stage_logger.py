import pytest
import logging
from mypkg.utils.stage_logger import StageLogger, StageFailedError, UsageError, ErrorLevel

@pytest.fixture
def logger():
    # Use a unique name to avoid conflicts
    log = StageLogger("TestLogger")
    log.console_enabled = False # Disable console for tests to keep output clean
    return log

def test_strict_mode_flat_to_context(logger):
    logger.set_stage("Flat")
    with pytest.raises(UsageError):
        with logger.stage("Context"):
            pass

def test_strict_mode_context_to_flat(logger):
    with logger.stage("Context"):
        pass
    with pytest.raises(UsageError):
        logger.set_stage("Flat")

def test_nesting_prohibited(logger):
    with pytest.raises(UsageError):
        with logger.stage("Outer"):
             with logger.stage("Inner"):
                 pass

def test_accumulated_error_flat(logger):
    logger.set_stage("Stage1")
    logger.error("Error 1")
    logger.error("Error 2")
    
    # Should raise when starting next stage
    with pytest.raises(StageFailedError) as exc:
        logger.set_stage("Stage2")
    
    assert exc.value.stage == "Stage1"
    assert exc.value.error_count == 2

def test_accumulated_error_context(logger):
    with pytest.raises(StageFailedError) as exc:
        with logger.stage("Stage1"):
            logger.error("Error 1")
    
    assert exc.value.stage == "Stage1"
    assert exc.value.error_count == 1

def test_fatal_error(logger):
    logger.set_stage("Stage1")
    with pytest.raises(StageFailedError):
        logger.fatal("Fatal error")

def test_checkpoint(logger):
    logger.set_stage("Stage1")
    logger.error("Error 1")
    
    with pytest.raises(StageFailedError):
        logger.checkpoint()

def test_success_path(logger):
    logger.set_stage("Stage1")
    logger.info("Info")
    logger.warning("Warning")
    
    # Should not raise
    logger.set_stage("Stage2")
    logger.info("Done")

def test_summary_inclusion(logger):
    logger.set_stage("Stage1")
    logger.info("Ignored Info")
    logger.warning("Important Warning", track=True)
    logger.warning("Warning")
    
    infos = [i for i in logger.issues if i.level == ErrorLevel.INFO]
    assert len(infos) == 0
    
    warnings = [i for i in logger.issues if i.level == ErrorLevel.WARNING]
    assert len(warnings) == 2

def test_rich_data_formatting(logger):
    logger.set_stage("Stage1")
    data = {"key": "value"}
    logger.warning("Data", data=data)
    # Just verify it didn't crash and issue has context
    assert logger.issues[0].context == data


