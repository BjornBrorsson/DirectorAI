"""Smoke test: paid-key Gemini API + response_json_schema acceptance.

Run: .venv/Scripts/python.exe tasks/multi-aroll-api-run/smoke_api.py

Three calls, all through GeminiClient(Settings()) -> real HTTP backend:
  a) text-only structured call on the FAST model
  b) one JPEG frame on the FAST model using the real classifier response model
  c) the director's _AssemblyPlan (nested Cut/AudioCue, Literal track, enums)
     on the DIRECTOR model

We capture WARNING records to detect whether the endpoint accepted
`response_json_schema` or the client fell back to sanitized `response_schema`.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient


class Pick(BaseModel):
    label: str
    confidence: float


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    fallback_seen: list[str] = []

    class _Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "response_json_schema" in record.getMessage():
                fallback_seen.append(record.getMessage())

    logging.getLogger("directorcut.gemini_client").addHandler(_Catch())

    settings = Settings()
    client = GeminiClient(settings)
    fast = settings.model_for("visual")
    director = settings.model_for("director")
    print(f"models: fast={fast} director={director}")

    # a) text-only on the fast model
    t0 = time.time()
    pick = client.call(
        prompt='Pick a colour. Return JSON: {"label": "<colour>", "confidence": 0-1}.',
        media=None,
        response_model=Pick,
        model=fast,
    )
    print(f"[a] fast/text  {time.time() - t0:.1f}s -> {pick!r}")

    # b) one JPEG on the fast model with the real classifier response model
    from directorcut.visual.classifier import _VisualEventList

    frame = Path("tasks/multi-aroll-api-run/smoke_frame.jpg").read_bytes()
    t0 = time.time()
    events = client.call(
        prompt=(
            "You are a gameplay video analyst. This single frame comes from a "
            "gameplay clip. Return JSON matching the VisualEventList schema: "
            "one event covering 00:00:00.000-00:00:01.000 describing the frame."
        ),
        media=[frame],
        response_model=_VisualEventList,
        model=fast,
    )
    print(f"[b] fast/image {time.time() - t0:.1f}s -> {len(events.visual_events)} event(s)")
    for ev in events.visual_events[:3]:
        print(f"    {ev.classification} {ev.time_start}-{ev.time_end} {ev.visual_description[:80]}")

    # c) director schema on the main model - exercises nested models + Literal
    from directorcut.assembly.director import _AssemblyPlan

    t0 = time.time()
    plan = client.call(
        prompt=(
            "Return an assembly plan with exactly one cut: track V1_A1_PRIMARY, "
            'source_file "take1.mp4", timeline_in 00:00:00.000, '
            "timeline_out 00:00:05.000, source_in 00:00:10.000, "
            "source_out 00:00:15.000. No audio cues."
        ),
        media=None,
        response_model=_AssemblyPlan,
        model=director,
    )
    print(f"[c] director   {time.time() - t0:.1f}s -> {len(plan.cuts)} cut(s)")
    for c in plan.cuts:
        print(f"    {c.track} {c.source_file} tl {c.timeline_in}-{c.timeline_out}")

    if fallback_seen:
        print("SCHEMA FALLBACK TRIGGERED:", *fallback_seen, sep="\n  ")
    else:
        print("response_json_schema accepted on all calls (no fallback).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
