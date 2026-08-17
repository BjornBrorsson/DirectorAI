"""FFmpeg/FFprobe discovery and execution."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.log import get_logger

logger = get_logger(__name__)


_COMMON_PATHS = {
    "Windows": [
        Path(r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"),
        Path(r"C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe"),
        Path(r"C:\\ffmpeg\\bin\\ffmpeg.exe"),
    ],
    "Darwin": [
        Path("/opt/homebrew/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
    ],
    "Linux": [
        Path("/usr/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
        Path("/opt/ffmpeg/ffmpeg"),
    ],
}


def _find_binary(name: str, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise FFmpegError(f"Explicit {name} path not found: {explicit}")

    from_path = shutil.which(name)
    if from_path:
        return Path(from_path)

    system = platform.system()
    for candidate in _COMMON_PATHS.get(system, []):
        if candidate.exists():
            return candidate

    raise FFmpegError(
        f"{name} not found in PATH or common install locations. "
        f"Install FFmpeg or set {name.upper()}_PATH."
    )


def find_ffmpeg(settings: Settings | None = None) -> Path:
    """Locate the FFmpeg binary."""
    explicit = settings.ffmpeg_path if settings else None
    return _find_binary("ffmpeg", explicit)


def find_ffprobe(settings: Settings | None = None) -> Path:
    """Locate the FFprobe binary."""
    explicit = settings.ffprobe_path if settings else None
    return _find_binary("ffprobe", explicit)


def version(path: Path) -> str:
    """Return the binary version string."""
    result = subprocess.run(
        [str(path), "-version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FFmpegError(f"Could not read version from {path}: {result.stderr}")
    match = re.search(r"version\s+([\d.]+)", result.stdout)
    return match.group(1) if match else "unknown"


def run_ffmpeg(args: Sequence[str], settings: Settings | None = None) -> str:
    """Run FFmpeg with the given args and return stdout."""
    ffmpeg = find_ffmpeg(settings)
    cmd = [str(ffmpeg), *args]
    logger.info("Running FFmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"FFmpeg failed: {result.stderr}")
    return result.stdout


def run_ffprobe(args: Sequence[str], settings: Settings | None = None) -> str:
    """Run FFprobe with the given args and return stdout."""
    ffprobe = find_ffprobe(settings)
    cmd = [str(ffprobe), *args]
    logger.info("Running FFprobe: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"FFprobe failed: {result.stderr}")
    return result.stdout
