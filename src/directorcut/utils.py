"""General helpers."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path


@contextmanager
def temp_dir(prefix: str = "directorcut_"):
    """Yield a temporary directory and clean it up on exit."""
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def human_duration(seconds: float) -> str:
    """Return a human-readable duration string."""
    td = timedelta(seconds=seconds)
    total = int(td.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def parse_timecode(tc: str) -> float:
    """Parse `HH:MM:SS.mmm` into seconds."""
    h, m, s = tc.split(":")
    sec, ms = s.split(".")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def format_timecode(seconds: float) -> str:
    """Format seconds as `HH:MM:SS.mmm`."""
    td = timedelta(seconds=seconds)
    total = int(td.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    ms = int(round((seconds - int(seconds)) * 1000)) % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
