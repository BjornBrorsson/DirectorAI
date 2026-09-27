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


def extract_audio_tracks(
    source: Path, output_dir: Path, stream_count: int
) -> list[Path]:
    """Extract every embedded audio stream of a video as its own mp3.

    Multi-track captures (OBS mic/game/desktop on separate streams) carry
    independent audio that Pass 3 may want to place separately - e.g. keep
    the mic track but drop game audio. Naming uses `_streamN` rather than
    `_trackN` on purpose: "track" is a music keyword in the dev-mode
    role heuristic and would misclassify mic audio as a music bed.

    Streams are kept at 48kHz stereo (not the 16kHz mono transcript proxy)
    because these files can land on the FCPXML timeline directly.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    outs: list[Path] = []
    for i in range(stream_count):
        # Resolve now: extracted streams become manifest assets, and asset
        # paths must be absolute for downstream `file://` URIs.
        out = (output_dir / f"{source.stem}_stream{i}.mp3").resolve()
        if out.exists():
            logger.info("Audio stream already exists: %s", out)
            outs.append(out)
            continue
        run_ffmpeg([
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:a:{i}",
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(out),
        ])
        logger.info("Audio stream %d extracted: %s", i, out)
        outs.append(out)
    return outs
