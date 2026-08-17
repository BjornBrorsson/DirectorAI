"""Visual proxy generation."""

from __future__ import annotations

from pathlib import Path

from directorcut.ffmpeg import run_ffmpeg
from directorcut.log import get_logger

logger = get_logger(__name__)


def generate_visual_proxy(source: Path, output_dir: Path, fps: float = 1.0) -> Path:
    """Generate a 360p, low-fps visual proxy."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{source.stem}_visual.mp4"
    if out.exists():
        logger.info("Visual proxy already exists: %s", out)
        return out

    run_ffmpeg([
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=-2:360,format=yuv420p",
        "-r",
        str(fps),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "28",
        str(out),
    ])
    logger.info("Visual proxy created: %s", out)
    return out
