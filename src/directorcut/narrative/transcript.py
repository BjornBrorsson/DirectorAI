"""Transcript-to-timestamp mapping."""

from __future__ import annotations

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


def transcribe(script_text: str | None, audio_path: Path | None) -> list[NarrativeNode]:
    """Generate narrative nodes from script and/or audio.

    In v0.1, if a script exists we map each sentence to a 5-second segment.
    If no script, a placeholder node is returned.
    """
    if script_text:
        lines = _split_lines(script_text)
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
