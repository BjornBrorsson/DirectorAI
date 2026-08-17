"""Chunked visual analysis for long assets."""

from __future__ import annotations

from pathlib import Path

from directorcut.config import Settings
from directorcut.log import get_logger
from directorcut.models import VisualEvent
from directorcut.safety.token_guard import check_token_budget
from directorcut.token_budget import chunk_proxy
from directorcut.visual.classifier import classify_visual
from directorcut.visual.ingest import load_visual_proxy

logger = get_logger(__name__)


def analyze_visual_proxy(
    proxy_path: Path,
    duration: float,
    settings: Settings,
) -> list[VisualEvent]:
    """Analyze a visual proxy in token-bounded chunks and merge event logs."""
    chunks = chunk_proxy(proxy_path, duration, fps=1.0)
    all_events: list[VisualEvent] = []
    frame_cache = list(load_visual_proxy(proxy_path, fps=1.0))

    for chunk in chunks:
        # Defense-in-depth: chunk_proxy() already sizes chunks to fit the
        # budget, but assert it per-chunk so a future change to the chunking
        # math (or a chunk fed in from elsewhere) fails fast and loudly
        # instead of silently blowing the context window.
        check_token_budget(chunk.source_path, chunk.frame_count)
        start_idx = max(0, int(chunk.start_time))
        end_idx = min(int(chunk.end_time), len(frame_cache))
        frames = [frame for _, frame in frame_cache[start_idx:end_idx]]
        events = classify_visual(chunk, frames, settings)
        all_events.extend(events)

    # Deduplicate overlapping events across chunk boundaries (simple merge).
    if not all_events:
        return []
    merged = [all_events[0]]
    for ev in all_events[1:]:
        last = merged[-1]
        if ev.classification == last.classification and ev.time_start <= last.time_end:
            merged[-1] = last.model_copy(update={"time_end": ev.time_end})
        else:
            merged.append(ev)
    logger.info("Merged %d visual event(s)", len(merged))
    return merged
