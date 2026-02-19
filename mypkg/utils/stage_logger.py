"""
mypkg.utils.stage_logger - Sequential workflow logger with stage support.
"""

import logging
import sys
from typing import Optional, List, Dict, Any, Callable, Union
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

# ==================== Data Structures ====================

class ErrorLevel(Enum):
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

# ==================== StageLogger ====================

class StageLogger:
    """
    Logger optimized for sequential workflows (stages).
    Supports accumulated errors, checkpoints, and strict mode separation.
    """
    
    def __init__(self, name: str = "StageLogger"):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG) # handle filtering via handlers
        
        # Internal State
        self.current_stage: Optional[str] = None
        self.issues: List[Issue] = []
        self.stage_history: List[str] = []
        
        # Mode Enforcement
        self._mode: Optional[str] = None # 'flat' or 'context'
        
        # Configuration
        self.console_enabled = True
        self.file_enabled = True
        
        # Rich Console
        self._console = Console() if HAS_RICH else None
        
        # Default Console Handler
        self.add_console_handler()

    # ==================== Configuration ====================
    
    def add_console_handler(self, level: str = "INFO", fmt: str = "%(message)s"):
        """Add a console handler."""
        if HAS_RICH:
            handler = RichHandler(
                console=self._console,
                show_time=True,
                show_path=False,
                markup=True,
                rich_tracebacks=True
            )
        else:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(fmt)
            handler.setFormatter(formatter)
            
        handler.setLevel(getattr(logging, level.upper()))
        handler.addFilter(lambda record: self.console_enabled)
        self.logger.addHandler(handler)

    def add_file_handler(self, path: str, level: str = "DEBUG", fmt: str = "%(asctime)s [%(levelname)s] %(stage)s: %(message)s"):
        """Add a file handler."""
        handler = logging.FileHandler(path, mode='w')
        formatter = logging.Formatter(fmt)
        handler.setFormatter(formatter)
        handler.setLevel(getattr(logging, level.upper()))
        handler.addFilter(lambda record: self.file_enabled)
        self.logger.addHandler(handler)

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
        self._set_mode('flat')
        
        # Finalize previous stage
        if self.current_stage:
            self._check_stage_health(self.current_stage)
            
        self.current_stage = name
        self.stage_history.append(name)
        self._log_system(f"▶ Stage: {name}", level="INFO")

    @contextmanager
    def stage(self, name: str):
        """
        Context manager for a stage.
        """
        self._set_mode('context')
        
        if self.current_stage is not None:
             raise UsageError("Nested stages are not allowed.")
             
        self.current_stage = name
        self.stage_history.append(name)
        self._log_system(f"▶ Stage: {name}", level="INFO")
        
        try:
            yield
        finally:
            stage_name = self.current_stage
            self.current_stage = None # Reset before check to allow strict check to pass if needed, 
                                      # though for context manager we just check issues.
            self._check_stage_health(stage_name)

    def _check_stage_health(self, stage: str):
        """Check if the given stage has accumulated fatal errors."""
        errors = [i for i in self.issues if i.stage == stage and i.level in (ErrorLevel.ERROR, ErrorLevel.CRITICAL)]
        if errors:
            raise StageFailedError(stage, errors)

    def checkpoint(self):
        """Raise StageFailedError if current stage has accumulated errors."""
        if not self.current_stage:
            return
        self._check_stage_health(self.current_stage)

    # ==================== Logging Methods ====================

    def _log(self, level_enum: ErrorLevel, msg: str, summary: bool, **kwargs):
        """Internal logging handler."""
        stage = self.current_stage or "Init"
        
        # 1. Record Issue
        # Info is only recorded if summary=True
        should_record = (level_enum != ErrorLevel.INFO) or summary
        
        if should_record:
            issue = Issue(
                level=level_enum,
                message=msg,
                stage=stage,
                context=kwargs.get('data', {})
            )
            self.issues.append(issue)

        # 2. Emit to Logging System
        extra = {'stage': stage}
        if HAS_RICH and 'data' in kwargs:
             # Pretty print data for Rich
             import json
             try:
                 data_str = json.dumps(kwargs['data'], indent=2, ensure_ascii=False)
                 msg = f"{msg}\nData: {data_str}"
             except:
                 msg = f"{msg}\nData: {kwargs['data']}"
        
        if level_enum == ErrorLevel.INFO:
            self.logger.info(msg, extra=extra)
        elif level_enum == ErrorLevel.WARNING:
            self.logger.warning(msg, extra=extra)
        elif level_enum == ErrorLevel.ERROR:
            self.logger.error(msg, extra=extra)
        elif level_enum == ErrorLevel.CRITICAL:
            self.logger.critical(msg, extra=extra)

    def info(self, msg: str, summary: bool = False, **kwargs):
        self._log(ErrorLevel.INFO, msg, summary, **kwargs)

    def warning(self, msg: str, summary: bool = True, **kwargs):
        self._log(ErrorLevel.WARNING, msg, summary, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(ErrorLevel.ERROR, msg, float('inf'), **kwargs) # Always summary

    def fatal(self, msg: str, **kwargs):
        self._log(ErrorLevel.CRITICAL, msg, float('inf'), **kwargs)
        # Raise immediately
        stage = self.current_stage or "Unknown"
        # We need to construct the error from the just-added issue
        last_issue = self.issues[-1]
        raise StageFailedError(stage, [last_issue], message=f"Fatal error in stage '{stage}': {msg}")
    
    def _log_system(self, msg: str, level="INFO"):
        """Log system messages (like stage headers) that don't produce Issues."""
        getattr(self.logger, level.lower())(msg, extra={'stage': self.current_stage or "System"})

    # ==================== Summary ====================

    def summary(self):
        """Print execution summary."""
        if HAS_RICH and self._console:
            self._summary_rich()
        else:
            self._summary_plain()
            
    def _summary_rich(self):
        self._console.print("\n")
        self._console.print(Panel("[bold cyan]EXECUTION SUMMARY[/]", expand=False))
        
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

    def _summary_plain(self):
        print("\n" + "="*40)
        print("EXECUTION SUMMARY")
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
