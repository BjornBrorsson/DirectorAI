"""Ingestion engine: validate inputs and build a run manifest."""

from __future__ import annotations

from pathlib import Path

from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import run_ffprobe
from directorcut.log import get_logger
from directorcut.models import IngestManifest

logger = get_logger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
SCRIPT_EXTS = {".txt"}


def _first(paths: list[Path], exts: set[str]) -> Path | None:
    for p in paths:
        if p.suffix.lower() in exts:
            return p
    return None


def _probe_video_info(path: Path) -> tuple[float, float]:
    """Return (duration_seconds, fps) via FFprobe.

    Never raises: probing is best-effort. On any failure this logs a warning
    and falls back to (0.0, 60.0) so ingest() can proceed without a hard crash.
    """
    try:
        output = run_ffprobe([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ])
        lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
        # ffprobe -show_entries with both `format=` and `stream=` sections prints
        # the stream field(s) first, then the format field(s).
        fps = 60.0
        duration = 0.0
        for line in lines:
            if "/" in line:
                num, _, den = line.partition("/")
                fps = float(num) / float(den) if float(den) != 0 else fps
            else:
                duration = float(line)
        return duration, fps
    except (FFmpegError, ValueError, ZeroDivisionError) as exc:
        logger.warning("Could not probe video info for %s: %s", path, exc)
        return 0.0, 60.0


def ingest(ingest_dir: Path, project_name: str = "project") -> IngestManifest:
    """Scan the ingest directory and build a validated manifest."""
    if not ingest_dir.exists():
        raise FileNotFoundError(f"Ingest directory not found: {ingest_dir}")

    files = [p for p in ingest_dir.iterdir() if p.is_file()]
    if not files:
        raise FileNotFoundError(f"No files in ingest directory: {ingest_dir}")

    video = _first(files, VIDEO_EXTS)
    audio = _first(files, AUDIO_EXTS)
    script = _first(files, SCRIPT_EXTS)

    if video is None:
        raise ValueError("No video file found in ingest directory.")

    if audio is None:
        # If no standalone audio, we can extract from the video later.
        logger.warning("No dedicated audio/voiceover file found; will extract from video.")

    # Resolve to absolute paths now: everything downstream (proxies, FCPXML
    # `file://` URIs) assumes absolute paths, and the ingest dir is commonly
    # supplied as a relative path (e.g. the default `./ingest`).
    video = video.resolve()
    audio = audio.resolve() if audio else None
    script = script.resolve() if script else None

    duration, fps = _probe_video_info(video)

    logger.info(
        "Ingested: video=%s audio=%s script=%s duration=%.2fs fps=%.3f",
        video,
        audio,
        script,
        duration,
        fps,
    )

    return IngestManifest(
        project_name=project_name,
        video_path=video,
        audio_path=audio,
        voiceover_path=audio,
        script_path=script,
        target_fps=fps,
        duration_seconds=duration,
    )
