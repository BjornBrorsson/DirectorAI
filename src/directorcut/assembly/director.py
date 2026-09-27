"""Pass 3: the AI assembly director.

Sends the narrative map, visual event log, asset inventory, and the editor's
free-text direction to Gemini and gets back a full assembly plan: timeline
cuts across all tracks plus audio cues for ElevenLabs-generated music/SFX.
The deterministic match_events/generate_cuts path remains the dev-mode
fallback and as a safety net when the model returns no usable cuts.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from directorcut.assembly.crossref import match_events
from directorcut.assembly.cuts import generate_cuts
from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient
from directorcut.ingest import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
from directorcut.log import get_logger
from directorcut.models import (
    AssemblyBlueprint,
    AssetRole,
    AudioCue,
    Cut,
    IngestManifest,
    NarrativeNode,
    TimelineMeta,
    VisualEvent,
)
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)

PROMPT = """You are the assembly director for an automated rough-cut editor.

You receive:
- the narrative map: spoken lines with timecodes, thematic tags, energy levels
- the visual event log: events with timecodes and the source file each came from
- the asset inventory: every available file with role, duration, description
- the editor's creative direction: free text, may be empty

Produce an assembly plan:
- cuts: timeline edits. Tracks:
  - V1_A1_PRIMARY: the main storyline, laid down contiguously in narrative
    order. Sources must be visual (A-roll video, stills) or audio clips that
    carry the narrative (voiceover) - never a music/SFX bed.
  - V2_OVERLAY_BROLL: B-roll video or still images overlaid on primary cuts.
    Sources must be video or image files only - audio cannot overlay.
  - A2_MUSIC / A3_SFX: music and sound-effect files from the inventory only.
- audio_cues: requests to GENERATE new audio. kind=music is a background bed,
  kind=sfx is a short stinger. Only request generation for gaps the existing
  inventory can't fill.

Rules:
- `source_file` must be an exact path from the asset inventory. Never invent files.
- source_in/source_out are positions inside the source file;
  timeline_in/timeline_out are positions on the edited timeline.
- For still images use source_in=00:00:00.000 and source_out equal to the
  desired on-screen duration.
- Nodes whose spoken_text reads "Unscripted segment ..." mark raw footage
  coverage, not speech. Pick and order the segments that best serve the
  direction - guided by the visual event log - instead of covering the
  whole runtime.
- Overlays should support the narrative moment - show the thing being talked
  about, cut away on jokes and callbacks.
- Honor the editor's direction (pacing, mood, music style) wherever possible.
- When several A-roll files are takes of the same script, pick the best take
  for each beat. NEVER use the same line from two takes, and NEVER reuse any
  source range (source_in..source_out) of a file - every second of source may
  appear on the timeline at most once, across all tracks.
- Re-takes inside one file (the speaker repeats a sentence) count as
  duplicates: keep exactly one.
- source_in/source_out for a V1 cut must lie inside the source_file the
  narrative line came from.

Direction from the editor:
{direction}

Asset inventory (path | role | duration | description):
{assets}

Narrative map:
{narrative}

Visual event log:
{visual}
"""


class _AssemblyPlan(BaseModel):
    cuts: list[Cut]
    audio_cues: list[AudioCue] = []


def _inventory_lines(manifest: IngestManifest) -> str:
    lines = []
    for a in manifest.assets:
        dur = f"{a.duration_seconds:.1f}s" if a.duration_seconds else "?"
        lines.append(f"{a.path} | {a.role.value} | {dur} | {a.description or ''}")
    return "\n".join(lines)


def _narrative_block(nodes: list[NarrativeNode]) -> str:
    """Render the narrative map grouped by source file.

    Multi-A-roll projects transcribe each source separately; grouping with a
    `## Narrative from <path>` header makes the take boundaries explicit.
    The per-line `source_file` field is only worth the tokens when more than
    one source exists - the header already says it otherwise.
    """
    groups: dict[str, list[NarrativeNode]] = {}
    for n in nodes:
        key = str(n.source_file) if n.source_file else "(unknown source)"
        groups.setdefault(key, []).append(n)
    multi = len(groups) > 1
    lines: list[str] = []
    for src, group in groups.items():
        lines.append(f"## Narrative from {src}")
        for n in group:
            d = n.model_dump(mode="json")
            if not multi:
                d.pop("source_file", None)
            lines.append(json.dumps(d))
    return "\n".join(lines)


def _deterministic_blueprint(
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
) -> AssemblyBlueprint:
    """The pre-AI heuristic path, kept as the offline/fallback plan."""
    video_source = manifest.video_path or Path("video.mp4")
    audio_source = manifest.audio_path or video_source
    return generate_cuts(
        project_name=manifest.project_name,
        nodes=nodes,
        events=events,
        matches=match_events(nodes, events),
        video_source=video_source,
        audio_source=audio_source,
        target_fps=manifest.target_fps,
    )


def _fake_plan(
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
) -> dict:
    """Dev-mode stub: serialize the deterministic blueprint into plan shape.

    Also emits one full-length music cue and an SFX cue per EMOTIONAL_SPIKE,
    and lays down user-supplied music/SFX/image/B-roll assets, so the
    audio-generation, overlay, and ducking paths all get exercised offline.
    """
    blueprint = _deterministic_blueprint(manifest, nodes, events)
    cues: list[dict] = []
    duration = manifest.duration_seconds or 0.0
    if duration <= 0.0 and nodes:
        duration = parse_timecode(nodes[-1].time_end)

    if duration > 0.0:
        cues.append(
            {
                "cue_id": "cue_music_001",
                "kind": "music",
                "prompt": f"Subtle background bed. Direction: {manifest.direction or 'neutral'}",
                "timeline_in": "00:00:00.000",
                "timeline_out": format_timecode(duration),
            }
        )

    extra_cuts: list[dict] = []
    for asset in manifest.assets_with_role(AssetRole.MUSIC):
        asset_len = asset.duration_seconds or duration
        span = min(asset_len, duration) if duration > 0 else asset_len
        if span <= 0:
            continue
        extra_cuts.append(
            {
                "track": "A2_MUSIC",
                "source_file": str(asset.path),
                "timeline_in": "00:00:00.000",
                "timeline_out": format_timecode(span),
                "source_in": "00:00:00.000",
                "source_out": format_timecode(span),
            }
        )

    spikes = [n for n in nodes if n.thematic_tag == "EMOTIONAL_SPIKE"]
    for i, node in enumerate(spikes):
        start = parse_timecode(node.time_start)
        if start + 1.5 > duration:
            continue  # cue would end before it begins
        cues.append(
            {
                "cue_id": f"cue_sfx_{i:03d}",
                "kind": "sfx",
                "prompt": "Short punchy stinger",
                "timeline_in": node.time_start,
                "timeline_out": format_timecode(start + 1.5),
            }
        )
    for i, asset in enumerate(manifest.assets_with_role(AssetRole.SFX)):
        anchor = spikes[i] if i < len(spikes) else (nodes[0] if nodes else None)
        if anchor is None:
            break
        start = parse_timecode(anchor.time_start)
        span = min(asset.duration_seconds or 1.5, 1.5)
        if start + span > duration:
            continue
        extra_cuts.append(
            {
                "track": "A3_SFX",
                "source_file": str(asset.path),
                "timeline_in": anchor.time_start,
                "timeline_out": format_timecode(start + span),
                "source_in": "00:00:00.000",
                "source_out": format_timecode(span),
            }
        )

    # Lay stills and unreferenced B-roll over the first primary cut so dev
    # runs exercise the overlay lanes too.
    overlay_sources = manifest.assets_with_role(AssetRole.IMAGE, AssetRole.B_ROLL)
    first_primary = next(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"), None
    )
    for asset in overlay_sources:
        if first_primary is None:
            break
        clip_in = parse_timecode(first_primary.timeline_in)
        span = 2.0
        if duration > 0 and clip_in + span > parse_timecode(first_primary.timeline_out):
            span = max(0.0, parse_timecode(first_primary.timeline_out) - clip_in)
        if span <= 0:
            continue
        already = any(
            c.source_file == asset.path and c.track == "V2_OVERLAY_BROLL"
            for c in blueprint.cuts
        )
        if already:
            continue
        extra_cuts.append(
            {
                "track": "V2_OVERLAY_BROLL",
                "source_file": str(asset.path),
                "timeline_in": first_primary.timeline_in,
                "timeline_out": format_timecode(clip_in + span),
                "source_in": "00:00:00.000",
                "source_out": format_timecode(span),
            }
        )

    return {
        "cuts": [c.model_dump(mode="json") for c in blueprint.cuts] + extra_cuts,
        "audio_cues": cues,
    }


def _resolve_source(raw: str | Path, manifest: IngestManifest) -> Path | None:
    """Map a model-returned source_file onto a known asset path."""
    src = Path(raw)
    for asset in manifest.assets:
        if asset.path == src or asset.path.name == src.name:
            return asset.path
    resolved = src.resolve()
    for asset in manifest.assets:
        if asset.path == resolved:
            return asset.path
    return None


# File kinds each track is allowed to carry. The model occasionally puts an
# audio-only stream on a visual lane (e.g. an mp3 "B-roll overlay"), which
# conforms into a broken or empty timeline - reject the combination here.
_TRACK_EXTS = {
    "V1_A1_PRIMARY": VIDEO_EXTS | IMAGE_EXTS | AUDIO_EXTS,
    "V2_OVERLAY_BROLL": VIDEO_EXTS | IMAGE_EXTS,
    "A2_MUSIC": AUDIO_EXTS,
    "A3_SFX": AUDIO_EXTS,
}

_FILLER_WORDS = {"um", "uh", "like"}
# How much two source ranges may overlap before the later cut counts as a
# reused take (frame-rate jitter lives under this).
_SOURCE_OVERLAP_TOLERANCE_S = 0.25

_EPS = 1e-3
# Narrative nodes within this margin count as covered by a cut.
_NODE_WINDOW_TOLERANCE_S = 0.15
# difflib ratio above which two cuts' spoken text is the same take.
_TAKE_SIMILARITY = 0.85


def _normalize_speech(text: str) -> str:
    """Lowercase, punctuation-free spoken text minus filler words."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return " ".join(w for w in words if w not in _FILLER_WORDS)


def _cut_text(cut: Cut, nodes: list[NarrativeNode]) -> str:
    """Normalized concatenation of the narrative text a V1 cut covers."""
    s_in = parse_timecode(cut.source_in) - _NODE_WINDOW_TOLERANCE_S
    s_out = parse_timecode(cut.source_out) + _NODE_WINDOW_TOLERANCE_S
    parts = [
        n.spoken_text
        for n in nodes
        if n.source_file == cut.source_file
        and s_in <= parse_timecode(n.time_start)
        and parse_timecode(n.time_end) <= s_out
    ]
    return _normalize_speech(" ".join(parts))


def _canonical_source_keys(manifest: IngestManifest) -> dict[Path, str]:
    """Map every asset path to a canonical identity for uniqueness checks.

    Two kinds of aliasing hide the same recording behind different paths:
    media_prep derivatives (`X_voicenorm.mp4` -> `derived_from` the ingest
    original) and the same physical file registered twice under different
    roles (a stream recording that serves as both A-roll and B-roll). The
    former resolves through the `derived_from` chain; the latter collapses
    via filesystem identity - hardlinks share (st_dev, st_ino).
    """
    by_path: dict[Path, Path] = {}
    for a in manifest.assets:
        by_path[a.path.resolve()] = a.path.resolve()
        if a.derived_from is not None:
            by_path[a.derived_from.resolve()] = a.derived_from.resolve()

    # derived_from edges form a small parent-pointer forest.
    parent: dict[Path, Path] = {
        a.path.resolve(): a.derived_from.resolve()
        for a in manifest.assets
        if a.derived_from is not None
    }

    def root(path: Path) -> Path:
        cur = path.resolve()
        seen: set[Path] = set()
        while cur in parent and cur not in seen:
            seen.add(cur)
            cur = parent[cur]
        return cur

    def identity(path: Path) -> str:
        r = root(path)
        try:
            st = r.stat()
            return f"{st.st_dev}:{st.st_ino}"
        except OSError:
            return f"path:{r}"

    return {p: identity(p) for p in by_path}


def _enforce_unique_source(
    cuts: list[Cut],
    nodes: list[NarrativeNode],
    canonical: dict[Path, str] | None = None,
) -> list[Cut]:
    """Drop duplicate-take cuts and re-compact the timeline around them.

    The prompt tells the model never to reuse a source range or a repeated
    line, but multi-take dumps still produce them. Steps:
    1. In timeline order, drop V1/V2 cuts whose (source_file, source range)
       overlaps an already-accepted cut on the same file by >0.25s.
    2. Drop V1 cuts whose covered narrative text is ~identical to an
       accepted V1 cut's (difflib ratio > 0.85, both >= 4 words) - re-takes
       inside one file don't share timecodes.
    3. Re-lay kept V1 cuts contiguously from 00:00:00.000 preserving order
       and duration; shift V2/A2/A3 cuts by the shift of the V1 cut that
       contained their old timeline_in (overlays anchored to a dropped V1
       are dropped; anything left past the new end is clamped/dropped).
    """
    visual = sorted(
        (c for c in cuts if c.track in ("V1_A1_PRIMARY", "V2_OVERLAY_BROLL")),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    canonical = canonical or {}
    used_ranges: dict[str, list[tuple[float, float]]] = {}
    seen_texts: list[str] = []
    dropped: list[Cut] = []
    trimmed: dict[int, Cut] = {}
    for cut in visual:
        s_in = parse_timecode(cut.source_in)
        s_out = parse_timecode(cut.source_out)
        t_in = parse_timecode(cut.timeline_in)
        t_out = parse_timecode(cut.timeline_out)
        key = canonical.get(cut.source_file.resolve(), str(cut.source_file))
        ranges = used_ranges.setdefault(key, [])
        # Sub-threshold reuse gets trimmed, not dropped - a 0.2s double-up
        # shouldn't cost a whole cut. Advance the in-point past accepted
        # ranges containing it (and likewise retreat the out-point); the
        # timeline moves with the source so clip speed stays 1:1.
        head = [e for s, e in ranges if s <= s_in + _EPS and e > s_in + _EPS]
        if head:
            delta = max(head) - s_in
            s_in += delta
            t_in += delta
        tail = [s for s, e in ranges if s < s_out - _EPS and e >= s_out - _EPS]
        if tail:
            delta = s_out - min(tail)
            s_out -= delta
            t_out -= delta
        if s_out <= s_in:
            dropped.append(cut)
            continue
        overlap = max(
            (min(s_out, e) - max(s_in, s) for s, e in ranges), default=0.0
        )
        if overlap > _SOURCE_OVERLAP_TOLERANCE_S:
            logger.warning(
                "Dropping %s cut on %s %.2f-%.2f - source range already used",
                cut.track,
                cut.source_file.name,
                s_in,
                s_out,
            )
            dropped.append(cut)
            continue
        if head or tail:
            logger.info(
                "Trimmed %s cut on %s to %.2f-%.2f - source range already used",
                cut.track,
                cut.source_file.name,
                s_in,
                s_out,
            )
            trimmed[id(cut)] = cut.model_copy(
                update={
                    "source_in": format_timecode(s_in),
                    "source_out": format_timecode(s_out),
                    "timeline_in": format_timecode(t_in),
                    "timeline_out": format_timecode(t_out),
                }
            )
            cut = trimmed[id(cut)]
        ranges.append((s_in, s_out))
        if cut.track == "V1_A1_PRIMARY":
            text = _cut_text(cut, nodes)
            if len(text.split()) >= 4 and any(
                len(t.split()) >= 4
                and difflib.SequenceMatcher(None, text, t).ratio()
                > _TAKE_SIMILARITY
                for t in seen_texts
            ):
                logger.warning(
                    "Dropping V1 cut - repeated take text: %r", text[:80]
                )
                dropped.append(cut)
                continue
            seen_texts.append(text)
    return compact_timeline([trimmed.get(id(c), c) for c in cuts], dropped)


def compact_timeline(cuts: list[Cut], dropped: Iterable[Cut]) -> list[Cut]:
    """Re-lay V1 contiguously from 0 with `dropped` cuts removed.

    Kept V1 cuts are placed back-to-back preserving order, each occupying
    its SOURCE span (the conformer derives clip duration from
    source_in..source_out, so the timeline span must match or the spine
    gets a hole). Every original V1 cut - kept or dropped - anchors a
    segment of the old timeline; V2/A2/A3 cuts are shifted by the shift
    of the V1 segment containing their old timeline_in. Overlays anchored
    to a dropped V1 are dropped; anything past the new end is clamped or
    dropped.
    """
    dropped_ids = {id(c) for c in dropped}
    visual = sorted(
        (c for c in cuts if c.track in ("V1_A1_PRIMARY", "V2_OVERLAY_BROLL")),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    others = [c for c in cuts if c.track not in ("V1_A1_PRIMARY", "V2_OVERLAY_BROLL")]

    v1_all = [c for c in visual if c.track == "V1_A1_PRIMARY"]
    if not v1_all:
        return cuts
    segs: list[tuple[float, float, float, bool]] = []
    new_v1: list[Cut] = []
    cursor = 0.0
    for cut in v1_all:
        old_in = parse_timecode(cut.timeline_in)
        old_out = parse_timecode(cut.timeline_out)
        src_dur = parse_timecode(cut.source_out) - parse_timecode(cut.source_in)
        is_dropped = id(cut) in dropped_ids
        segs.append((old_in, old_out, cursor - old_in, is_dropped))
        if not is_dropped:
            new_v1.append(
                cut.model_copy(
                    update={
                        "timeline_in": format_timecode(cursor),
                        "timeline_out": format_timecode(cursor + src_dur),
                    }
                )
            )
            cursor += src_dur
    timeline_end = cursor

    def _seg_for(t: float) -> tuple[float, bool] | None:
        for old_in, old_out, shift, was_dropped in segs:
            if old_in <= t < old_out:
                return shift, was_dropped
        prior = [s for s in segs if s[1] <= t]
        if prior:
            return prior[-1][2], prior[-1][3]
        return (segs[0][2], segs[0][3]) if segs else None

    result = list(new_v1)
    anchors = [
        c
        for c in visual
        if c.track == "V2_OVERLAY_BROLL" and id(c) not in dropped_ids
    ] + others
    for cut in anchors:
        seg = _seg_for(parse_timecode(cut.timeline_in))
        if seg is not None and seg[1] and cut.track == "V2_OVERLAY_BROLL":
            logger.warning(
                "Dropping overlay on %s - its anchor V1 cut was dropped",
                cut.source_file.name,
            )
            continue
        shift = seg[0] if seg else 0.0
        new_in = parse_timecode(cut.timeline_in) + shift
        new_out = min(parse_timecode(cut.timeline_out) + shift, timeline_end)
        if new_in >= timeline_end or new_out - new_in <= 0:
            logger.warning(
                "Dropping %s cut on %s - fell past the compacted timeline end",
                cut.track,
                cut.source_file.name,
            )
            continue
        result.append(
            cut.model_copy(
                update={
                    "timeline_in": format_timecode(new_in),
                    "timeline_out": format_timecode(new_out),
                }
            )
        )
    return result


def _plan_to_blueprint(
    plan: _AssemblyPlan,
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
) -> AssemblyBlueprint:
    cuts: list[Cut] = []
    for cut in plan.cuts:
        src = _resolve_source(cut.source_file, manifest)
        if src is None:
            # Hallucinated path - never emit a cut pointing at a file that
            # isn't in the inventory.
            logger.warning(
                "Director returned unknown source_file %s - dropping cut",
                cut.source_file,
            )
            continue
        if src.suffix.lower() not in _TRACK_EXTS.get(cut.track, set()):
            logger.warning(
                "Director put %s on %s - file type not allowed, dropping cut",
                src.name,
                cut.track,
            )
            continue
        cuts.append(cut.model_copy(update={"source_file": src}))

    cuts = _enforce_unique_source(cuts, nodes, _canonical_source_keys(manifest))

    if not cuts:
        logger.warning(
            "Director plan had no usable cuts; falling back to deterministic assembly"
        )
        blueprint = _deterministic_blueprint(manifest, nodes, events)
        return blueprint.model_copy(update={"audio_cues": plan.audio_cues})

    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(
            project_name=manifest.project_name, target_fps=manifest.target_fps
        ),
        cuts=cuts,
        audio_cues=plan.audio_cues,
    )


def run_director(
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
    settings: Settings,
    correction: str | None = None,
) -> AssemblyBlueprint:
    """Run the AI assembly pass and return a validated blueprint.

    `correction` is appended to the direction text verbatim - the target
    validation step uses it for its single corrective re-prompt, feeding the
    measured violations back because the model can't see its own numbers.
    """
    client = GeminiClient(settings)
    direction = manifest.direction or ""
    if correction:
        direction = f"{direction}\n\n{correction}" if direction else correction
    prompt = PROMPT.format(
        direction=direction,
        assets=_inventory_lines(manifest),
        narrative=_narrative_block(nodes),
        visual=json.dumps([e.model_dump(mode="json") for e in events], indent=2),
    )
    plan = client.call(
        prompt=prompt,
        media=None,
        response_model=_AssemblyPlan,
        dev_mode_override=_fake_plan(manifest, nodes, events),
        task="director",
    )
    blueprint = _plan_to_blueprint(plan, manifest, nodes, events)
    logger.info(
        "Director produced %d cut(s) and %d audio cue(s)",
        len(blueprint.cuts),
        len(blueprint.audio_cues),
    )
    return blueprint
