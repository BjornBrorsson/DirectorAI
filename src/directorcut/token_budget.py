"""Token budget estimation and proxy chunking."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from directorcut.log import get_logger
from directorcut.utils import format_timecode

logger = get_logger(__name__)

# Gemini image token approximation for 360p frames (very rough heuristic).
TOKENS_PER_360P_FRAME = 263
VIDEO_TOKEN_OVERHEAD = 1.05
MAX_INPUT_TOKENS = 1_000_000
SAFE_INPUT_TOKENS = int(MAX_INPUT_TOKENS * 0.95)


@dataclass(frozen=True)
class VideoChunk:
    """A chunk of a video proxy."""

    index: int
    start_time: float
    end_time: float
    start_timecode: str
    end_timecode: str
    frame_count: int
    estimated_tokens: int
    source_path: Path


def estimate_video_tokens(frame_count: int) -> int:
    """Estimate tokens for a number of 360p frames."""
    return int(frame_count * TOKENS_PER_360P_FRAME * VIDEO_TOKEN_OVERHEAD)


def chunk_proxy(
    proxy_path: Path,
    duration_seconds: float,
    fps: float = 1.0,
    max_tokens: int = SAFE_INPUT_TOKENS,
) -> list[VideoChunk]:
    """Split a proxy into chunks that fit the token budget."""
    total_frames = int(duration_seconds * fps)
    tokens_per_frame = int(TOKENS_PER_360P_FRAME * VIDEO_TOKEN_OVERHEAD)
    frames_per_chunk = max(1, int(max_tokens // tokens_per_frame))
    seconds_per_chunk = frames_per_chunk / fps

    chunks: list[VideoChunk] = []
    num_chunks = math.ceil(total_frames / frames_per_chunk) if frames_per_chunk else 1
    for i in range(num_chunks):
        start = i * seconds_per_chunk
        end = min(start + seconds_per_chunk, duration_seconds)
        chunk_frames = min(frames_per_chunk, total_frames - i * frames_per_chunk)
        chunks.append(
            VideoChunk(
                index=i,
                start_time=start,
                end_time=end,
                start_timecode=format_timecode(start),
                end_timecode=format_timecode(end),
                frame_count=chunk_frames,
                estimated_tokens=estimate_video_tokens(chunk_frames),
                source_path=proxy_path,
            )
        )
    logger.info(
        "Split proxy %s into %d chunk(s), ~%d tokens each",
        proxy_path,
        len(chunks),
        chunks[0].estimated_tokens if chunks else 0,
    )
    return chunks
