"""Timecode <-> FCPXML rational conversion."""

from __future__ import annotations

from directorcut.utils import parse_timecode


def to_rational(timecode: str, fps: float = 60.0) -> str:
    """Convert `HH:MM:SS.mmm` to an FCPXML rational time string.

    Example: at 60fps, 00:01:12.500 -> '435000/6000s'.
    """
    seconds = parse_timecode(timecode)
    denom = int(round(fps))
    numer = int(round(seconds * fps))
    return f"{numer}/{denom}s"


def frame_duration(fps: float = 60.0) -> str:
    """Return the FCPXML frameDuration for a given fps."""
    denom = int(round(fps * 100))
    return f"100/{denom}s"
