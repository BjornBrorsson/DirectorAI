"""Thematic tagging of narrative nodes via Gemini."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient, _fake_tag_narrative
from directorcut.log import get_logger
from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag

logger = get_logger(__name__)


PROMPT = """You are a narrative editor.
Given the following spoken lines, classify each line with a thematic tag and energy level.

Tags:
- EXPOSITION: flat informational setup
- COMEDIC_SET: setup for a joke or punchline
- EMOTIONAL_SPIKE: yelling, cheering, intense disappointment
- CALLBACK_REQ: explicit query looking for evidence

Energy levels: LOW, MEDIUM, HIGH.

Each line is prefixed with its existing timecode range in brackets.

Return JSON matching the NarrativeNodeList schema: one entry per input
line carrying node_id, thematic_tag, and energy_level.

Spoken lines:
{lines}
"""


class _TaggedNode(BaseModel):
    """Lenient tagger response row.

    Only these three fields are consumed - the caller always preserves the
    original node's timecodes/text - so parsing them as plain strings keeps
    one model-mangled timestamp or enum value from nuking a whole batch."""

    model_config = ConfigDict(extra="ignore")

    node_id: str = ""
    thematic_tag: str = ""
    energy_level: str = ""


class _NarrativeNodeList(BaseModel):
    narrative_nodes: list[_TaggedNode]


# Nodes per model call. The tagger's response must re-emit every node, and
# small models truncate long lists - batching keeps each reply comfortably
# inside the output budget.
_TAG_BATCH_SIZE = 100


def tag_narrative(
    nodes: list[NarrativeNode],
    settings: Settings,
    direction: str | None = None,
) -> list[NarrativeNode]:
    """Tag and refine energy levels for each narrative node."""
    client = GeminiClient(settings)
    tagged: list[NarrativeNode] = []
    for start in range(0, len(nodes), _TAG_BATCH_SIZE):
        batch = nodes[start : start + _TAG_BATCH_SIZE]
        lines = "\n".join(
            f"{n.node_id} [{n.time_start} - {n.time_end}]: {n.spoken_text}"
            for n in batch
        )
        prompt = PROMPT.format(lines=lines)
        if direction:
            prompt += (
                "\nEditor's direction (use it as context for tone and pacing):\n"
                f"{direction}\n"
            )
        dev_override = _fake_tag_narrative(batch)
        result = client.call(
            prompt=prompt,
            media=None,
            response_model=_NarrativeNodeList,
            dev_mode_override=dev_override,
            task="narrative",
        )
        outs = result.narrative_nodes
        if len(outs) != len(batch):
            # The model dropped/merged nodes - never crash on it (a strict
            # zip used to kill the whole run here); match by node_id and
            # keep originals for whatever didn't come back.
            logger.warning(
                "Tagger returned %d node(s) for a batch of %d",
                len(outs),
                len(batch),
            )
        by_id = {n.node_id: n for n in outs}
        for j, original in enumerate(batch):
            out = by_id.get(original.node_id)
            if out is None and j < len(outs):
                out = outs[j]  # model rewrote the ids - positional fallback
            if out is None:
                logger.warning("No tag for %s - keeping defaults", original.node_id)
                tagged.append(original)
                continue
            # Preserve original timecodes (and source identity) whatever the
            # model did to them; coerce tag/energy with fallback to the
            # original so a stray value degrades to defaults, not a crash.
            try:
                tag = ThematicTag(out.thematic_tag)
            except ValueError:
                tag = original.thematic_tag
            try:
                energy = EnergyLevel(out.energy_level)
            except ValueError:
                energy = original.energy_level
            tagged.append(
                NarrativeNode(
                    node_id=original.node_id,
                    time_start=original.time_start,
                    time_end=original.time_end,
                    spoken_text=original.spoken_text,
                    thematic_tag=tag,
                    energy_level=energy,
                    source_file=original.source_file,
                )
            )
    logger.info("Tagged %d narrative node(s)", len(tagged))
    return tagged
