"""Chunked visual analysis for long assets."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from directorcut.config import Settings
from directorcut.log import get_logger
from directorcut.models import VisualEvent
from directorcut.safety.token_guard import check_token_budget
from directorcut.token_budget import SAFE_INPUT_TOKENS, chunk_proxy
from directorcut.visual.classifier import classify_visual
from directorcut.visual.ingest import load_visual_proxy

logger = get_logger(__name__)

# The generateContent endpoint rejects request bodies over ~20MB. 360p
# JPEGs run ~45-50KB each, so on the HTTP API backend chunks must be far
# smaller than the model's 1M-token context (~145 frames ≈ 9MB base64).
# The CLI/agy backends ship frames as workspace files and keep the full
# context-sized chunks.
_API_CHUNK_TOKENS = 40_000


def analyze_visual_proxy(
    proxy_path: Path,
    duration: float,
    settings: Settings,
    source_file: Path | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
    checkpoint_path: Path | None = None,
) -> list[VisualEvent]:
    """Analyze a visual proxy in token-bounded chunks and merge event logs.

    `source_file` is the original full-res asset the proxy was built from; it
    is stamped on every event so Pass 3 can reference the right source when a
    project has several video files.

    `checkpoint_path` enables incremental resume: every finished chunk is
    appended as one JSONL line (`{"chunk_index", "events"}`), so a crash
    mid-asset loses only the in-flight chunk and a re-run replays the rest.
    """
    max_tokens = (
        _API_CHUNK_TOKENS
        if settings.gemini_backend == "api"
        else SAFE_INPUT_TOKENS
    )
    chunks = chunk_proxy(proxy_path, duration, fps=1.0, max_tokens=max_tokens)
    all_events: list[VisualEvent] = []

    # Replay already-finished chunks from the checkpoint. A torn last line
    # (crash mid-write) fails json parsing and is simply re-analyzed.
    done: dict[int, list[VisualEvent]] = {}
    ckpt = Path(checkpoint_path) if checkpoint_path else None
    if ckpt is not None and ckpt.exists():
        try:
            lines = ckpt.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                rec = json.loads(line)
                done[int(rec["chunk_index"])] = [
                    VisualEvent.model_validate(e) for e in rec["events"]
                ]
            except (KeyError, TypeError, ValueError):
                continue
        if done:
            logger.info(
                "Resuming %s: %d/%d chunk(s) already analyzed",
                proxy_path.name,
                len(done),
                len(chunks),
            )

    # Lazy frame decode: a fully checkpointed asset never touches cv2.
    frame_cache: list | None = None
    for i, chunk in enumerate(chunks, start=1):
        if chunk.index in done:
            all_events.extend(done[chunk.index])
            continue
        if on_chunk is not None:
            on_chunk(i, len(chunks))
        # Defense-in-depth: chunk_proxy() already sizes chunks to fit the
        # budget, but assert it per-chunk so a future change to the chunking
        # math (or a chunk fed in from elsewhere) fails fast and loudly
        # instead of silently blowing the context window.
        check_token_budget(chunk.source_path, chunk.frame_count)
        if frame_cache is None:
            frame_cache = list(load_visual_proxy(proxy_path, fps=1.0))
        # DIRECTORCUT_VISUAL_FPS < 1 stride-samples the 1fps proxy - frames
        # are the dominant input-token cost and B-roll relevance picks don't
        # need per-second granularity. The model is told the real interval.
        stride = max(1, round(1.0 / settings.visual_fps))
        start_idx = max(0, int(chunk.start_time))
        end_idx = min(int(chunk.end_time), len(frame_cache))
        frames = [frame for _, frame in frame_cache[start_idx:end_idx:stride]]
        events = classify_visual(
            chunk,
            frames,
            settings,
            source_file=source_file,
            sample_interval_s=float(stride),
        )
        all_events.extend(events)
        if ckpt is not None:
            ckpt.parent.mkdir(parents=True, exist_ok=True)
            with ckpt.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "chunk_index": chunk.index,
                            "events": [
                                e.model_dump(mode="json") for e in events
                            ],
                        }
                    )
                    + "\n"
                )

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
