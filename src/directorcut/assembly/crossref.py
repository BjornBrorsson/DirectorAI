"""Cross-reference narrative and visual events."""

from __future__ import annotations

from dataclasses import dataclass

from directorcut.log import get_logger
from directorcut.models import NarrativeNode, VisualEvent, VisualState
from directorcut.utils import parse_timecode

logger = get_logger(__name__)


@dataclass(frozen=True)
class Match:
    narrative_id: str
    event_id: str
    score: float
    reason: str


def _ts(node_or_event) -> float:
    """Return start time in seconds from a model with time_start."""
    return parse_timecode(node_or_event.time_start)


def _find_nearest(
    node: NarrativeNode,
    events: list[VisualEvent],
    predicate,
    direction: str = "after",
) -> VisualEvent | None:
    """Find the nearest event matching predicate relative to the node."""
    node_ts = _ts(node)
    candidates = [e for e in events if predicate(e)]
    if not candidates:
        return None
    if direction == "after":
        candidates = [e for e in candidates if _ts(e) >= node_ts]
    else:
        candidates = [e for e in candidates if _ts(e) <= node_ts]
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs(_ts(e) - node_ts))


def match_events(
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
) -> list[Match]:
    """Match narrative nodes to the best visual event candidates."""
    matches: list[Match] = []
    for node in nodes:
        tag = node.thematic_tag
        if tag == "CALLBACK_REQ":
            # A callback line ("Why didn't they tell me?!") refers to
            # something that already happened - look backward in time for
            # the evidence, not forward. See the TDD's own worked example
            # (Section 3.2, Pass 3) for the canonical case this models.
            ev = _find_nearest(node, events, lambda e: True, "before")
            if ev:
                matches.append(Match(node.node_id, ev.event_id, 1.0, "callback evidence"))
        elif tag == "COMEDIC_SET":
            ev = _find_nearest(
                node,
                events,
                lambda e: e.classification in (VisualState.STATE_DEATH, VisualState.STATE_ACTION),
                "after",
            )
            if ev:
                matches.append(Match(node.node_id, ev.event_id, 0.9, "comedic payoff"))
        elif tag == "EMOTIONAL_SPIKE":
            ev = _find_nearest(
                node,
                events,
                lambda e: e.classification == VisualState.STATE_ACTION,
                "after",
            )
            if ev:
                matches.append(Match(node.node_id, ev.event_id, 0.85, "emotional peak"))
        elif tag == "EXPOSITION":
            ev = _find_nearest(
                node,
                events,
                lambda e: e.classification == VisualState.STATE_TUTORIAL,
                "after",
            )
            if ev:
                matches.append(Match(node.node_id, ev.event_id, 0.7, "tutorial b-roll"))
    logger.info("Matched %d narrative-visual pair(s)", len(matches))
    return matches
