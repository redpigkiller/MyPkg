"""
stage_tracker.py

A workflow-oriented tracker for sequential, multi-stage processes.

Features:
- Stage lifecycle management (flat or context manager style, not mixed)
- Accumulated error handling with checkpoint and fatal support
- Auto-generated summary of warnings and errors per stage
- Level-based routing to console and file handlers

Typical usage:
    from mypkg.utils.stage_tracker import StageTracker

    tracker = StageTracker()
    tracker.set_stage("Load")
    tracker.info("Reading config...")
    tracker.set_stage("Process")
    tracker.error("File missing")
    tracker.summary()
"""

import sys
import logging
import threading
import json
from typing import Optional, List, Dict, Any, Callable, Union, Literal
from enum import Enum
from dataclasses import dataclass, field
from contextlib import contextmanager

_console_lock = threading.Lock()

# Optional Rich support
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = None


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ErrorLevelStr = Literal["debug", "info", "warning", "error", "critical"]
# ==================== Data Structures ====================

class Mode(Enum):
    FLAT = "flat"
    CONTEXT = "context"

class ErrorLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class Issue:
    level: ErrorLevel
    message: str
    stage: str

@dataclass
class Artifact:
    stage: str
    value: Any

class StageFailedError(Exception):
    """Raised when a stage fails due to accumulated errors or a fatal error."""
    def __init__(self, stage: str, issues: List[Issue], message: Optional[str] = None):
        self.stage = stage
        self.issues = issues
        self.error_count = sum(1 for i in issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL))
        if message is None:
            message = f"Stage '{stage}' failed with {self.error_count} error(s)"
        super().__init__(message)

class UsageError(Exception):
    """Raised for incorrect usage of StageLogger (e.g. mixing flat/context modes)."""
    pass

class StageFormatter(logging.Formatter):
    """Fallback Formatter to inject the current 'stage' if rich is not available."""
    def format(self, record):
        record.stage = getattr(record, 'stage', 'System')
        return super().format(record)

class EnableFilter(logging.Filter):
    """Filter to toggle handlers dynamically."""
    def __init__(self, flag_getter: Callable[[], bool]):
        self.flag_getter = flag_getter

    def filter(self, record):
        return self.flag_getter()

# ==================== StageTracker ====================

class StageTracker:
    """
    Workflow tracker optimized for sequential workflows (stages).
    Supports accumulated errors, checkpoints, execution logs, and artifacts tracking.
    """
    
    def __init__(self, name: str = "StageTracker"):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG) # handle filtering via handlers
        self.logger.propagate = False # Prevent multi-logging duplication up to root logger
        
        # Thread-Local State
        self._local = threading.local()
        
        # Configuration
        self.console_enabled = True
        self.file_enabled = True
        
        # Rich Console
        self._console = Console() if HAS_RICH else None
        
        # Default Console Handler
        self.add_console_handler()

    # ==================== Thread-Local Properties ====================

    @property
    def current_stage(self) -> Optional[str]:
        return getattr(self._local, 'current_stage', None)

    @current_stage.setter
    def current_stage(self, value: Optional[str]):
        self._local.current_stage = value

    @property
    def issues(self) -> List[Issue]:
        if not hasattr(self._local, 'issues'):
            self._local.issues = []
        return self._local.issues

    def clear_issues(self):
        self._local.issues = []

    @property
    def artifacts(self) -> List[Artifact]:
        if not hasattr(self._local, 'artifacts'):
            self._local.artifacts = []
        return self._local.artifacts

    def clear_artifacts(self):
        self._local.artifacts = []

    @property
    def stage_history(self) -> List[str]:
        if not hasattr(self._local, 'stage_history'):
            self._local.stage_history = []
        return self._local.stage_history

    @stage_history.setter
    def stage_history(self, value: List[str]):
        self._local.stage_history = value

    @property
    def _mode(self) -> Optional[Mode]:
        return getattr(self._local, '_mode', None)

    @_mode.setter
    def _mode(self, value: Optional[Mode]):
        self._local._mode = value

    # ==================== Configuration ====================
    
    def add_console_handler(self, level: LogLevel = "INFO", fmt: Optional[str] = None):
        """
        Add a console handler.
        Note: If the 'rich' library is installed, the `fmt` parameter is ignored
        and formatting is fully delegated to RichHandler.
        """
        with _console_lock:
            if getattr(self.logger, '_stage_console_added', False):
                return
            self.logger._stage_console_added = True

            if HAS_RICH:
                handler = RichHandler(
                    console=self._console,
                    show_time=True,
                    show_path=False,
                    markup=True,
                    rich_tracebacks=True,
                    omit_repeated_times=True
                )
            else:
                handler = logging.StreamHandler(sys.stdout)
                fmt_str = fmt if fmt is not None else "%(asctime)s [%(levelname)s] %(stage)s: %(message)s"
                formatter = StageFormatter(fmt=fmt_str, datefmt="%H:%M:%S")
                handler.setFormatter(formatter)
                
            handler.setLevel(getattr(logging, level.upper()))
            handler.addFilter(EnableFilter(lambda: self.console_enabled))
            self.logger.addHandler(handler)

    def add_file_handler(self, path: str, level: LogLevel = "DEBUG", mode: Literal["w", "a"] = "w", 
                         fmt: str = "%(asctime)s [%(levelname)s] %(stage)s: %(message)s",
                         max_bytes: int = 0, backup_count: int = 0):
        """
        Add a file handler.
        Supported rotation if max_bytes > 0.
        Note: If max_bytes > 0, 'mode' is forced to 'a' to prevent truncation of the active log upon rotation setup.
        """
        if max_bytes > 0:
            from logging.handlers import RotatingFileHandler
            safe_mode = "a" # Prevent erasing the current file mistakenly if rotation is active
            handler = RotatingFileHandler(path, mode=safe_mode, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
        else:
            handler = logging.FileHandler(path, mode=mode, encoding='utf-8')
            
        formatter = StageFormatter(fmt)
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, level.upper()))
        handler.addFilter(EnableFilter(lambda: self.file_enabled))
        self.logger.addHandler(handler)

    def reset(self, keep_handlers: bool = False):
        """Reset the logger state (clears issues, artifacts, history, and current stage)."""
        self.clear_issues()
        self.clear_artifacts()
        self.stage_history = []
        self.current_stage = None
        self._mode = None

        if not keep_handlers:
            with _console_lock:
                for h in list(self.logger.handlers):
                    h.close()
                    self.logger.removeHandler(h)
                    
                if hasattr(self.logger, '_stage_console_added'):
                    delattr(self.logger, '_stage_console_added')
            self.add_console_handler()
        
    def get_issues(self, stage: Optional[str] = None, level: Optional[Union[ErrorLevel, List[ErrorLevel], ErrorLevelStr, List[ErrorLevelStr]]] = None) -> List[Issue]:
        """Retrieve issues filtered by stage and/or level."""
        result = list(self.issues) # Get copy instead of reference
        
        if stage:
            result = [i for i in result if i.stage == stage]
            
        if level:
            if not isinstance(level, list):
                levels_to_check = [level]
            else:
                levels_to_check = level
                
            parsed_levels = []
            valid_levels = [e.value for e in ErrorLevel]
            for lvl in levels_to_check:
                if isinstance(lvl, ErrorLevel):
                    parsed_levels.append(lvl)
                elif isinstance(lvl, str):
                    lvl_lower = lvl.strip().lower()
                    if lvl_lower in valid_levels:
                        parsed_levels.append(ErrorLevel(lvl_lower))
                    else:
                        raise ValueError(f"Invalid Level: '{lvl}'. Available options: {valid_levels}")
                else:
                    raise TypeError(f"Expected ErrorLevel or str, got {type(lvl)}")
                    
            result = [i for i in result if i.level in parsed_levels]
            
        return result

    # ==================== Stage Management ====================

    def _set_mode(self, mode: Mode):
        if self._mode is not None and self._mode != mode:
            raise UsageError(f"Cannot mix {mode.value} mode with previously used {self._mode.value} mode.")
        self._mode = mode

    def set_stage(self, name: str):
        """
        Start a new stage (Flat Mode). 
        Finalizes the previous stage if one exists.
        """
        self._set_mode(Mode.FLAT)
        self.finalize_stage() # Close previous stage

        self.current_stage = name
        self.stage_history.append(name)
        self._log_system(f"Stage: {name}", level="DEBUG")

    def finalize_stage(self):
        """
        Explicitly close the current stage.
        Primarily used in Flat Mode to trigger the health check before moving on or summarizing.
        """
        stage = self.current_stage
        if stage:
            self._check_stage_health(stage)
            self.current_stage = None

    @contextmanager
    def stage(self, name: str):
        """
        Context manager for a stage.
        """
        self._set_mode(Mode.CONTEXT)
        
        if self.current_stage is not None:
             raise UsageError(f"Nested stages are not allowed. Currently in '{self.current_stage}'. Please exhaust the current stage or use a sub-tracker.")
             
        self.current_stage = name
        self.stage_history.append(name)
        self._log_system(f"Stage: {name}", level="DEBUG")
        
        has_exception = False
        try:
            yield
        except Exception:
            has_exception = True
            raise
        finally:
            stage_name = self.current_stage
            
            if has_exception:
                self.current_stage = None
                errors = [i for i in self.issues if i.stage == stage_name
                    and i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
                
                if errors:
                    self._log_system(
                        f"Stage '{stage_name}' had {len(errors)} error(s) before exception",
                        level="WARNING"
                    )
            else:
                self.finalize_stage()
    
    def _check_stage_health(self, stage: str):
        """Check if the given stage has accumulated fatal errors."""
        errors = [i for i in self.issues if i.stage == stage and i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
        if errors:
            raise StageFailedError(stage, errors)

    def checkpoint(self):
        """
        Check if the current stage has accumulated errors and raise StageFailedError if so.
        Useful in Context Mode to fail early inside a long loop rather than waiting for 
        the block to exit.
        """
        if not self.current_stage:
            return
        self._check_stage_health(self.current_stage)

    # ==================== Logging & Tracking Methods ====================

    def add_artifact(self, value: Any):
        """Record an artifact, config, or execution context variable to the current stage."""
        stage = self.current_stage if self.current_stage else "-"
        artifact = Artifact(stage=stage, value=value)
        self.artifacts.append(artifact)

    def _log(self, level_enum: ErrorLevel, msg: str, track: bool, **kwargs):
        """Internal logging handler."""
        stage = self.current_stage if self.current_stage else "-"
        extra = dict(kwargs.pop('extra', {}))
        extra['stage'] = stage
        kwargs['extra'] = extra
        
        # 1. Record Issue
        issue = None

        if track:
            issue = Issue(
                level=level_enum,
                message=msg,
                stage=stage
            )
            self.issues.append(issue)

        # 2. Emit to Logging System
        getattr(self.logger, level_enum.value)(msg, **kwargs)

        return issue if track else None

    # Note: debug/info track defaults to False, warning defaults to True
    def debug(self, msg: str, track: bool = False, **kwargs):
        self._log(ErrorLevel.DEBUG, msg, track, **kwargs)

    def info(self, msg: str, track: bool = False, **kwargs):
        self._log(ErrorLevel.INFO, msg, track, **kwargs)

    def warning(self, msg: str, track: bool = True, **kwargs):
        self._log(ErrorLevel.WARNING, msg, track, **kwargs)

    def error(self, msg: str, exc_info: bool = False, **kwargs):
        self._log(ErrorLevel.ERROR, msg, True, exc_info=exc_info, **kwargs)

    def fatal(self, msg: str, exc_info: bool = False, **kwargs):
        issue = self._log(ErrorLevel.CRITICAL, msg, True, exc_info=exc_info, **kwargs)
        # Raise immediately
        stage = self.current_stage or "Unknown"
        stage_issues = [i for i in self.issues if i.stage == stage and i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
        raise StageFailedError(stage, stage_issues, message=f"Fatal error in stage '{stage}': {msg}")
    
    def _log_system(self, msg: str, level: LogLevel = "DEBUG"):
        """Log system messages (like stage headers) that don't produce Issues."""
        # mypy type checking workaround: use getattr string since LogLevel includes custom formats
        getattr(self.logger, level.lower())(msg, extra={'stage': self.current_stage or "System"})

    # ==================== Summary ====================

    def summary(self, title: str = "EXECUTION SUMMARY") -> bool:
        """Print execution summary and return True if successful (no errors), False otherwise."""
        # Force a health check for the last unclosed flat stage
        if self._mode == Mode.FLAT and self.current_stage:
            try:
                self.finalize_stage()
            except StageFailedError:
                # We intentionally swallow the exception here because we just want the 
                # health check to potentially commit the stage issue count before summary.
                # The user will check summary() return value to see if anything failed.
                self.logger.debug("Final stage health check failed, proceeding to error summary.")
                pass
                
        if HAS_RICH and self._console:
            self._summary_rich(title)
        else:
            self._summary_plain(title)
            
        errors = [i for i in self.issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
        return len(errors) == 0
            
    def _summary_rich(self, title: str):
        stages_str = " → ".join(self.stage_history)
        self._console.print(f"[dim]Stages: {stages_str}[/]")
        self._console.print("\n")
        self._console.print(Panel(f"[bold cyan]{title}[/]", expand=False))
        
        # Group by level
        infos = [i for i in self.issues if i.level == ErrorLevel.INFO]
        warnings = [i for i in self.issues if i.level == ErrorLevel.WARNING]
        errors = [i for i in self.issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]

        if infos:
            self._console.print(f"\n[bold blue]{len(infos)} info:[/]")
            for i in infos:
                self._console.print(f"  [{i.stage}] {i.message}")

        if warnings:
            self._console.print(f"\n[bold yellow]{len(warnings)} warnings:[/]")
            for i in warnings:
                self._console.print(f"  [{i.stage}] {i.message}")
                
        if errors:
            self._console.print(f"\n[bold red]{len(errors)} errors:[/]")
            for i in errors:
                self._console.print(f"  [{i.stage}] {i.message}")
                
        # Group artifacts by stage
        if self.artifacts:
            self._console.print("\n[bold magenta]\\[RECORDS & ARTIFACTS][/]")
            artifacts_by_stage = {}
            for r in self.artifacts:
                artifacts_by_stage.setdefault(r.stage, []).append(r)
                
            for stg, recs in artifacts_by_stage.items():
                self._console.print(f"  [cyan]\\[{stg}][/cyan]")
                for r in recs:
                    # Format dicts/lists nicely, else standard str
                    val_str = ""
                    if isinstance(r.value, (dict, list)):
                        try:
                            formatted = json.dumps(r.value, indent=2, ensure_ascii=False, default=str)
                            # Indent the JSON block
                            val_str = "\n".join("      " + line for line in formatted.splitlines())
                        except Exception:
                            val_str = "      " + str(r.value)
                    else:
                        val_str = "      " + str(r.value)
                    
                    self._console.print(val_str)

        if errors:
             self._console.print(f"\n[bold red]FAILED ({len(errors)} errors)[/]")
        else:
             self._console.print(f"\n[bold green]SUCCESS[/]")

    def _summary_plain(self, title: str):
        stages_str = " → ".join(self.stage_history)
        print(f"Stages: {stages_str}")
        print("\n" + "="*40)
        print(title)
        print("="*40)
        
        infos = [i for i in self.issues if i.level == ErrorLevel.INFO]
        warnings = [i for i in self.issues if i.level == ErrorLevel.WARNING]
        errors = [i for i in self.issues if i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]

        if infos:
            print(f"\n{len(infos)} info:")
            for i in infos:
                print(f"  [{i.stage}] {i.message}")

        if warnings:
            print(f"\n{len(warnings)} warnings:")
            for i in warnings:
                print(f"  [{i.stage}] {i.message}")
                
        if errors:
            print(f"\n{len(errors)} errors:")
            for i in errors:
                print(f"  [{i.stage}] {i.message}")
                
        # Group artifacts by stage
        if self.artifacts:
            print("\n[RECORDS & ARTIFACTS]")
            artifacts_by_stage = {}
            for r in self.artifacts:
                artifacts_by_stage.setdefault(r.stage, []).append(r)
                
            for stg, recs in artifacts_by_stage.items():
                print(f"  [{stg}]")
                for r in recs:
                    val_str = ""
                    if isinstance(r.value, (dict, list)):
                        try:
                            formatted = json.dumps(r.value, indent=2, ensure_ascii=False, default=str)
                            val_str = "\n".join("      " + line for line in formatted.splitlines())
                        except Exception:
                            val_str = "      " + str(r.value)
                    else:
                        val_str = "      " + str(r.value)
                    print(val_str)
        
        print("\n" + "-"*40)
        if errors:
            print(f"FAILED ({len(errors)} errors)")
        else:
            print("SUCCESS")

