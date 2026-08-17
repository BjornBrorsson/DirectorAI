"""Thematic tagging of narrative nodes via Gemini."""

from __future__ import annotations

from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient, _fake_tag_narrative
from directorcut.log import get_logger
from directorcut.models import NarrativeNode

logger = get_logger(__name__)


PROMPT = """You are a narrative editor.
Given the following spoken lines, classify each line with a thematic tag and energy level.

Tags:
- EXPOSITION: flat informational setup
- COMEDIC_SET: setup for a joke or punchline
- EMOTIONAL_SPIKE: yelling, cheering, intense disappointment
- CALLBACK_REQ: explicit query looking for evidence

Energy levels: LOW, MEDIUM, HIGH.

Return JSON matching the NarrativeNodeList schema.

Spoken lines:
{lines}
"""


class _NarrativeNodeList(BaseModel):
    narrative_nodes: list[NarrativeNode]


def tag_narrative(nodes: list[NarrativeNode], settings: Settings) -> list[NarrativeNode]:
    """Tag and refine energy levels for each narrative node."""
    client = GeminiClient(settings)
    lines = "\n".join(f"{n.node_id}: {n.spoken_text}" for n in nodes)
    prompt = PROMPT.format(lines=lines)
    dev_override = _fake_tag_narrative(nodes)
    result = client.call(
        prompt=prompt,
        media=None,
        response_model=_NarrativeNodeList,
        dev_mode_override=dev_override,
    )
    # Preserve original timecodes if the model changed them.
    tagged = []
    for original, out in zip(nodes, result.narrative_nodes, strict=True):
        tagged.append(
            NarrativeNode(
                node_id=original.node_id,
                time_start=original.time_start,
                time_end=original.time_end,
                spoken_text=original.spoken_text,
                thematic_tag=out.thematic_tag,
                energy_level=out.energy_level,
            )
        )
    logger.info("Tagged %d narrative node(s)", len(tagged))
    return tagged
