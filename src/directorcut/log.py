"""Structured logging setup."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure root console + optional file logging."""
    root = logging.getLogger("directorcut")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    class _Formatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):  # noqa: N802
            dt = datetime.fromtimestamp(record.created, tz=UTC)
            return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    formatter = _Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
