"""Narrative input ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NarrativeInput:
    """Input to the narrative engine."""

    text: str | None
    audio_path: Path | None
    has_script: bool


def load_narrative_input(
    script_paths: Path | Sequence[Path] | None,
    audio_path: Path | None,
) -> NarrativeInput:
    """Load script and audio inputs for the narrative pass.

    Accepts one or many script paths; multiple scripts are concatenated with a
    blank line between them so a dumped folder can carry several documents.
    """
    if script_paths is None:
        paths: list[Path] = []
    elif isinstance(script_paths, Path):
        paths = [script_paths]
    else:
        paths = list(script_paths)

    texts = []
    for path in paths:
        if path.exists():
            texts.append(path.read_text(encoding="utf-8-sig").strip())
    text = "\n\n".join(t for t in texts if t)
    return NarrativeInput(
        text=text or None,
        audio_path=audio_path,
        has_script=bool(text),
    )
