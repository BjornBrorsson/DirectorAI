"""Audio proxy extraction."""

from __future__ import annotations

from pathlib import Path

from directorcut.ffmpeg import run_ffmpeg
from directorcut.log import get_logger

logger = get_logger(__name__)


def extract_audio(source: Path, output_dir: Path, sample_rate: int = 16000) -> Path:
    """Extract a compact mp3 audio proxy."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{source.stem}_audio.mp3"
    if out.exists():
        logger.info("Audio proxy already exists: %s", out)
        return out

    run_ffmpeg([
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(out),
    ])
    logger.info("Audio proxy created: %s", out)
    return out
