# modules/logger.py
import logging
import logging.handlers
import sys
from pathlib import Path

from modules.clock import format_uptime, is_clock_synchronized, read_uptime_seconds


class UptimeFormatter(logging.Formatter):
    """Wall-clock timestamp plus boot uptime; marks unsynced wall time."""

    def format(self, record: logging.LogRecord) -> str:
        uptime = format_uptime(read_uptime_seconds())
        wall = self.formatTime(record, self.datefmt)
        synced = is_clock_synchronized()
        if synced is False:
            record.asctime = f"{wall} ({uptime} ~unsynced)"
        else:
            record.asctime = f"{wall} ({uptime})"
        return super().format(record)


class ToastLoggingHandler(logging.Handler):
    """Automatically turns WARNING and ERROR logs into UI toasts"""
    
    def __init__(self, toast_manager):
        super().__init__()
        self.toast_manager = toast_manager

    def emit(self, record):
        if not self.toast_manager or not hasattr(self.toast_manager, 'error'):
            return

        # Skip very noisy internal loggers
        if record.name in ("werkzeug", "engineio", "socketio", "urllib3", "asyncio"):
            return

        message = record.getMessage()
        
        # Include exception info if present
        if record.exc_info:
            message = f"{message} — {record.exc_info[1]}"

        try:
            if record.levelno >= logging.ERROR:
                self.toast_manager.error(
                    message=message,
                    title=record.name.split('.')[-1] or "Error",
                    persistent=True
                )
            elif record.levelno >= logging.WARNING:
                self.toast_manager.warning(
                    message=message,
                    title=record.name.split('.')[-1] or "Warning",
                    persistent=False
                )
        except:
            # Never let toast handler crash the logging system
            pass


def setup_logging(config, toast_manager=None) -> logging.Logger:
    """
    Setup logging using values from [logging] section in sccs.conf
    """
    level_name = config.get('logging', 'level', fallback='INFO')
    log_dir = config.get('logging', 'log_directory', fallback='logs')
    retention_days = config.getint('logging', 'log_retention_days', fallback=31)

    level = getattr(logging, level_name.upper(), logging.INFO)

    # Ensure logs directory exists
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Main logger
    logger = logging.getLogger("sccs")
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    formatter = UptimeFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ------------------- Console Handler -------------------
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # ------------------- Daily Rotating File Handler -------------------
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_path / "sccs.log",
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
        utc=False
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # ------------------- Toast Handler (if available) -------------------
    if toast_manager:
        toast_handler = ToastLoggingHandler(toast_manager)
        toast_handler.setLevel(logging.WARNING)        # Only warnings + errors
        logger.addHandler(toast_handler)

    return logger