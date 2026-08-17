"""Narrative input ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class NarrativeInput:
    """Input to the narrative engine."""

    text: str | None
    audio_path: Path | None
    has_script: bool


def load_narrative_input(script_path: Path | None, audio_path: Path | None) -> NarrativeInput:
    """Load script and audio inputs for the narrative pass."""
    text = None
    if script_path and script_path.exists():
        with open(script_path, encoding="utf-8-sig") as f:
            text = f.read()
    return NarrativeInput(
        text=text.strip() if text else None,
        audio_path=audio_path,
        has_script=bool(text and text.strip()),
    )
