"""
stage_logger.py

A workflow-oriented logger for sequential, multi-stage processes.

Features:
- Stage lifecycle management (flat or context manager style, not mixed)
- Accumulated error handling with checkpoint and fatal support
- Auto-generated summary of warnings and errors per stage
- Level-based routing to console and file handlers

Typical usage:
    from mypkg.utils.stage_logger import StageLogger

    log = StageLogger()
    log.set_stage("Load")
    log.info("Reading config...")
    log.set_stage("Process")
    log.error("File missing")
    log.summary()
"""

import sys
import logging
import threading
from typing import Optional, List, Dict, Any, Callable, Union, Literal
from enum import Enum
from dataclasses import dataclass, field
from contextlib import contextmanager

# Optional Rich support
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = None


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ErrorLevelStr = Literal["debug", "info", "warning", "error", "critical"]
# ==================== Data Structures ====================

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
    context: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self):
        return self.message

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
    def format(self, record):
        if not hasattr(record, 'stage'):
            record.stage = 'System'
        return super().format(record)

# ==================== StageLogger ====================

class StageLogger:
    """
    Logger optimized for sequential workflows (stages).
    Supports accumulated errors, checkpoints, and strict mode separation.

    Thread Safety:
        This class is Thread-Safe. It uses `threading.local()` to maintain isolated 
        stage hierarchies, issues, and execution states per thread, while sharing 
        the underlying log routing (handlers/formatters).
    """
    
    def __init__(self, name: str = "StageLogger", strict_thread: bool = False):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG) # handle filtering via handlers
        
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
    def stage_history(self) -> List[str]:
        if not hasattr(self._local, 'stage_history'):
            self._local.stage_history = []
        return self._local.stage_history

    @stage_history.setter
    def stage_history(self, value: List[str]):
        self._local.stage_history = value

    @property
    def _mode(self) -> Optional[str]:
        return getattr(self._local, '_mode', None)

    @_mode.setter
    def _mode(self, value: Optional[str]):
        self._local._mode = value

    @property
    def _console_handler_added(self) -> bool:
        return getattr(self._local, '_console_handler_added', False)

    @_console_handler_added.setter
    def _console_handler_added(self, value: bool):
        self._local._console_handler_added = value

    def _check_thread(self):
        # With thread-local storage, this is no longer strictly necessary to catch errors,
        # but we can keep it as a no-op so we don't need to change other code, 
        # or we can remove the calls entirely. For simplicity, we just pass.
        pass

    # ==================== Configuration ====================
    
    def add_console_handler(self, level: LogLevel = "INFO", fmt: str = "%(message)s"):
        """
        Add a console handler.
        Note: If the 'rich' library is installed, the `fmt` parameter is ignored
        and formatting is fully delegated to RichHandler.
        """
        if self._console_handler_added:
            return
        self._console_handler_added = True

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
            fmt_str = fmt if "%(stage)s" in fmt else "%(asctime)s [%(levelname)s] %(stage)s: %(message)s"
            formatter = StageFormatter(fmt=fmt_str, datefmt="%H:%M:%S")
            handler.setFormatter(formatter)
            
        handler.setLevel(getattr(logging, level.upper()))
        handler.addFilter(lambda record: self.console_enabled)
        self.logger.addHandler(handler)

    def add_file_handler(self, path: str, level: LogLevel = "DEBUG", mode: Literal["w", "a"] = "w", 
                         fmt: str = "%(asctime)s [%(levelname)s] %(stage)s: %(message)s",
                         max_bytes: int = 0, backup_count: int = 0):
        """
        Add a file handler.
        Supported rotation if max_bytes > 0.
        """
        if max_bytes > 0:
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(path, mode=mode, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
        else:
            handler = logging.FileHandler(path, mode=mode, encoding='utf-8')
            
        formatter = StageFormatter(fmt)
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, level.upper()))
        handler.addFilter(lambda record: self.file_enabled)
        self.logger.addHandler(handler)

    def reset(self, keep_handlers: bool = False):
        """Reset the logger state (clears issues, history, and current stage)."""
        self._check_thread()
        self.clear_issues()
        self.stage_history = []
        self.current_stage = None
        self._mode = None

        if not keep_handlers:
            for h in list(self.logger.handlers):
                h.close()
                self.logger.removeHandler(h)
                
            self._console_handler_added = False
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
            for lvl in levels_to_check:
                if isinstance(lvl, str):
                    try:
                        parsed_levels.append(ErrorLevel(lvl.lower()))
                    except ValueError:
                        valid_levels = [e.value for e in ErrorLevel]
                        raise ValueError(f"Invalid Level: '{lvl}'. Available options: {valid_levels}")
                else:
                    parsed_levels.append(lvl)
                    
            result = [i for i in result if i.level in parsed_levels]
            
        return result

    # ==================== Stage Management ====================

    def _set_mode(self, mode: str):
        if self._mode is not None and self._mode != mode:
            raise UsageError(f"Cannot mix {mode} mode with previously used {self._mode} mode.")
        self._mode = mode

    def set_stage(self, name: str):
        """
        Start a new stage (Flat Mode). 
        Finalizes the previous stage if one exists.
        """
        self._check_thread()
        self._set_mode('flat')
        prev_stage = self.current_stage

        # Finalize previous stage
        if prev_stage:
            self._check_stage_health(prev_stage)

        self.current_stage = name
        self.stage_history.append(name)
        self._log_system(f"Stage: {name}", level="DEBUG")

    @contextmanager
    def stage(self, name: str):
        """
        Context manager for a stage.
        """
        self._check_thread()
        self._set_mode('context')
        
        if self.current_stage is not None:
             raise UsageError(f"Nested stages are not allowed. Currently in '{self.current_stage}'.")
             
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
            self.current_stage = None

            if has_exception:
                errors = [i for i in self.issues if i.stage == stage_name
                    and i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
                
                if errors:
                    self._log_system(
                        f"Stage '{stage_name}' had {len(errors)} error(s) before exception",
                        level="WARNING"
                    )
            else:
                self._check_stage_health(stage_name)
    
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

    # ==================== Logging Methods ====================

    def _log(self, level_enum: ErrorLevel, msg: str, track: bool, **kwargs):
        """Internal logging handler."""
        self._check_thread()
        stage = self.current_stage if self.current_stage else "-"
        kwargs.setdefault('extra', {})['stage'] = stage
        data = kwargs.pop('data', {})
        
        # 1. Record Issue
        issue = None

        if track:
            issue = Issue(
                level=level_enum,
                message=msg,
                stage=stage,
                context=data
            )
            self.issues.append(issue)

        # 2. Emit to Logging System
        if data:
            class LazyJSONFormatter:
                def __init__(self, msg, data):
                    self.msg = msg
                    self.data = data
                def __str__(self):
                    import json
                    try:
                        data_str = json.dumps(self.data, indent=2, ensure_ascii=False, default=str)
                        return f"{self.msg}\nData: {data_str}"
                    except Exception:
                        return f"{self.msg}\nData: {self.data}"
            
            # Use %s formatting so serialize only happens if handler accepts level
            getattr(self.logger, level_enum.value)("%s", LazyJSONFormatter(msg, data), **kwargs)
        else:
            getattr(self.logger, level_enum.value)(msg, **kwargs)

        return issue if track else None

    def debug(self, msg: str, track: bool = False, **kwargs):
        self._log(ErrorLevel.DEBUG, msg, track, **kwargs)

    def info(self, msg: str, track: bool = False, **kwargs):
        self._log(ErrorLevel.INFO, msg, track, **kwargs)

    def warning(self, msg: str, track: bool = True, **kwargs):
        self._log(ErrorLevel.WARNING, msg, track, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(ErrorLevel.ERROR, msg, True, **kwargs)

    def fatal(self, msg: str, **kwargs):
        issue = self._log(ErrorLevel.CRITICAL, msg, True, **kwargs)
        # Raise immediately
        stage = self.current_stage or "Unknown"
        if issue is None:
            raise RuntimeError("fatal() must always produce an Issue. 'track' may have been forced to False.")
        raise StageFailedError(stage, [issue], message=f"Fatal error in stage '{stage}': {msg}")
    
    def _log_system(self, msg: str, level : LogLevel = "DEBUG"):
        """Log system messages (like stage headers) that don't produce Issues."""
        getattr(self.logger, level.lower())(msg, extra={'stage': self.current_stage or "System"})

    # ==================== Summary ====================

    def summary(self, title: str = "EXECUTION SUMMARY") -> bool:
        """Print execution summary and return True if successful (no errors), False otherwise."""
        # Force a health check for the last unclosed flat stage
        if self._mode == 'flat' and self.current_stage:
            try:
                self.checkpoint()
            except StageFailedError:
                # We expect the exception, it means the last stage had errors.
                # Continue printing summary.
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
        
        print("\n" + "-"*40)
        if errors:
            print(f"FAILED ({len(errors)} errors)")
        else:
            print("SUCCESS")

