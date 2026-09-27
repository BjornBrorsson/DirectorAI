"""Transcript-to-timestamp mapping."""

from __future__ import annotations

import math
import re
from pathlib import Path

from directorcut.log import get_logger
from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag
from directorcut.utils import format_timecode

logger = get_logger(__name__)

# Split on sentence-ending punctuation followed by whitespace, but keep the
# punctuation attached to each sentence. Downstream tagging (real Gemini call
# or the dev-mode stub) relies on "?" / "!" surviving as a real signal for
# CALLBACK_REQ / EMOTIONAL_SPIKE - a plain `.split(".")` after replacing "?"
# and "!" with "." destroys that signal entirely.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")


def _split_lines(text: str) -> list[str]:
    """Split script into sentence-like segments, preserving punctuation."""
    raw = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip())]
    return [r for r in raw if r]


_UNSCRIPTED_SEGMENT_S = 60.0


def _unscripted_segments(duration_seconds: float) -> list[NarrativeNode]:
    """Tile an unscripted source (livestream, gameplay capture) with segment
    markers covering its full duration. The director needs real coverage to
    assemble anything longer than a few seconds; the visual event log then
    tells it which segments are worth keeping."""
    nodes = []
    n_segments = max(1, math.ceil(duration_seconds / _UNSCRIPTED_SEGMENT_S))
    for i in range(n_segments):
        start = i * _UNSCRIPTED_SEGMENT_S
        end = min(start + _UNSCRIPTED_SEGMENT_S, duration_seconds)
        nodes.append(
            NarrativeNode(
                node_id=f"N_{i + 1:03d}",
                time_start=format_timecode(start),
                time_end=format_timecode(end),
                spoken_text=(
                    f"Unscripted segment {format_timecode(start)}"
                    f"-{format_timecode(end)}"
                ),
                thematic_tag=ThematicTag.EXPOSITION,
                energy_level=EnergyLevel.MEDIUM,
            )
        )
    return nodes


def transcribe(
    script_text: str | None,
    audio_path: Path | None,
    duration_seconds: float | None = None,
) -> list[NarrativeNode]:
    """Generate narrative nodes from script and/or audio.

    If a script exists we map each sentence to a 5-second segment. With no
    script but a known source duration, the audio is assumed unscripted and
    the full duration is tiled with segment markers; otherwise a single
    placeholder node is returned.
    """
    if script_text:
        lines = _split_lines(script_text)
    elif duration_seconds and duration_seconds > 0.0:
        return _unscripted_segments(duration_seconds)
    elif audio_path and audio_path.exists():
        lines = ["Voiceover without script."]
    else:
        lines = ["No spoken content provided."]

    nodes: list[NarrativeNode] = []
    for i, line in enumerate(lines, start=1):
        start = (i - 1) * 5.0
        end = i * 5.0
        nodes.append(
            NarrativeNode(
                node_id=f"N_{i:03d}",
                time_start=format_timecode(start),
                time_end=format_timecode(end),
                spoken_text=line,
                thematic_tag=ThematicTag.EXPOSITION,
                energy_level=EnergyLevel.MEDIUM,
            )
        )
    logger.info("Transcribed %d narrative node(s)", len(nodes))
    return nodes
