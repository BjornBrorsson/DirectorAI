"""Cut-list generation."""

from __future__ import annotations

from pathlib import Path

from directorcut.assembly.crossref import Match
from directorcut.log import get_logger
from directorcut.models import AssemblyBlueprint, Cut, NarrativeNode, TimelineMeta, VisualEvent
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)


def generate_cuts(
    project_name: str,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
    matches: list[Match],
    video_source: Path,
    audio_source: Path,
    target_fps: float = 60.0,
) -> AssemblyBlueprint:
    """Generate an assembly blueprint from matched narrative and visual events.

    `V1_A1_PRIMARY` cuts use `audio_source` (the voiceover) as their source
    file when one is available, falling back to `video_source` only when no
    separate voiceover exists - matching the TDD's own schema example, where
    the primary track's `source_file` is the voiceover, not the gameplay
    capture. `V2_OVERLAY_BROLL` cuts always reference `video_source`.
    """
    event_by_id = {e.event_id: e for e in events}
    primary_source = audio_source or video_source
    cuts: list[Cut] = []
    timeline_pos = 0.0

    # Add primary voiceover / gameplay cuts from narrative nodes.
    for node in nodes:
        start = parse_timecode(node.time_start)
        end = parse_timecode(node.time_end)
        duration = end - start
        cuts.append(
            Cut(
                track="V1_A1_PRIMARY",
                source_file=primary_source,
                timeline_in=format_timecode(timeline_pos),
                timeline_out=format_timecode(timeline_pos + duration),
                source_in=node.time_start,
                source_out=node.time_end,
            )
        )
        # Add matching B-roll overlay if it exists.
        match = next((m for m in matches if m.narrative_id == node.node_id), None)
        if match and match.event_id in event_by_id:
            ev = event_by_id[match.event_id]
            ev_start = parse_timecode(ev.time_start)
            ev_end = parse_timecode(ev.time_end)
            ev_dur = ev_end - ev_start
            cuts.append(
                Cut(
                    track="V2_OVERLAY_BROLL",
                    source_file=video_source,
                    timeline_in=format_timecode(timeline_pos),
                    timeline_out=format_timecode(timeline_pos + ev_dur),
                    source_in=ev.time_start,
                    source_out=ev.time_end,
                )
            )
        timeline_pos += duration

    logger.info("Generated %d cut(s)", len(cuts))
    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name=project_name, target_fps=target_fps),
        cuts=cuts,
    )
