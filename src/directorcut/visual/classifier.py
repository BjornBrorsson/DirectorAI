"""Visual state classification."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient, _fake_visual
from directorcut.log import get_logger
from directorcut.models import VisualEvent
from directorcut.token_budget import VideoChunk

logger = get_logger(__name__)


PROMPT = """You are a gameplay video analyst.
Given a sequence of frames sampled every {interval} second(s) (frame i
shows second i*{interval} of this segment), identify contiguous gameplay
state events and report their start/end in SECONDS.

Classifications:
- STATE_UI: inventory menus, vendor windows, skill selection lists
- STATE_ACTION: intense movement, kill feeds, combo meters
- STATE_DEATH: sudden UI screens indicating reset or fail state
- STATE_TUTORIAL: popup informational windows on screen

Return JSON matching the VisualEventList schema.
"""


class _VisualEventList(BaseModel):
    visual_events: list[VisualEvent]


def classify_visual(
    chunk: VideoChunk,
    frames: list[bytes],
    settings: Settings,
    source_file: Path | None = None,
    sample_interval_s: float = 1.0,
) -> list[VisualEvent]:
    """Classify visual state events in a chunk."""
    client = GeminiClient(settings)
    result = client.call(
        prompt=PROMPT.format(interval=f"{sample_interval_s:g}"),
        media=frames,
        response_model=_VisualEventList,
        dev_mode_override=_fake_visual(frames),
        task="visual",
    )
    # Shift event timecodes by chunk start.
    from directorcut.utils import format_timecode, parse_timecode
    shifted = []
    for ev in result.visual_events:
        start = parse_timecode(ev.time_start) + chunk.start_time
        end = parse_timecode(ev.time_end) + chunk.start_time
        shifted.append(
            VisualEvent(
                event_id=ev.event_id,
                time_start=format_timecode(start),
                time_end=format_timecode(end),
                classification=ev.classification,
                visual_description=ev.visual_description,
                source_file=source_file or chunk.source_path,
            )
        )
    logger.info("Classified %d visual event(s) in chunk %d", len(shifted), chunk.index)
    return shifted
