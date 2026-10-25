"""Application log: a rotating file under the config directory, plus live lines for the GUI."""

from __future__ import annotations

import logging
import logging.handlers
from collections.abc import Callable
from pathlib import Path

from .store import config_dir

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured: Path | None = None


def log_dir() -> Path:
    return config_dir() / "logs"


def setup(level: int = logging.INFO) -> Path:
    """Sends the app's log records to camandroid.log (1 MB x 3). Returns the log file path."""
    global _configured
    if _configured:
        return _configured
    logger = logging.getLogger("camandroid_desktop")
    logger.setLevel(level)
    path = log_dir() / "camandroid.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
    except OSError:
        pass  # read-only profile: the Logs tab still works
    _configured = path
    return path


class CallbackHandler(logging.Handler):
    """Forwards formatted records to a callback (which must be thread-safe)."""

    def __init__(self, callback: Callable[[int, str], None]):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(record.levelno, self.format(record))
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
