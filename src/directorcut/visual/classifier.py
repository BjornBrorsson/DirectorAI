"""Visual state classification."""

from __future__ import annotations

from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient, _fake_visual
from directorcut.log import get_logger
from directorcut.models import VisualEvent
from directorcut.token_budget import VideoChunk

logger = get_logger(__name__)


PROMPT = """You are a gameplay video analyst.
Given a sequence of 1-FPS frames, identify contiguous gameplay state events.

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
) -> list[VisualEvent]:
    """Classify visual state events in a chunk."""
    client = GeminiClient(settings)
    result = client.call(
        prompt=PROMPT,
        media=frames,
        response_model=_VisualEventList,
        dev_mode_override=_fake_visual(frames),
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
            )
        )
    logger.info("Classified %d visual event(s) in chunk %d", len(shifted), chunk.index)
    return shifted
