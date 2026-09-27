"""Automatic audio ducking for music/SFX lanes.

Computes FCPXML `volume` keyframe animations that dip music and SFX clips
while primary dialogue/voiceover is playing. Non-destructive: the dips are
ordinary volume keyframes on the clip, so they stay tweakable in the NLE.
"""

from __future__ import annotations

from directorcut.models import AssemblyBlueprint, Cut
from directorcut.utils import parse_timecode

# Seconds of ramp into/out of a duck.
DUCK_RAMP_S = 0.4
# How far each track type dips while voice is present (dB, 0 = unity).
TRACK_DUCK_DB = {"A2_MUSIC": -12.0, "A3_SFX": -6.0}
# Connected-clip lane numbers for the audio lanes (-1 is the primary audio).
AUDIO_LANES = {"A2_MUSIC": "-2", "A3_SFX": "-3"}


def voice_ranges(blueprint: AssemblyBlueprint) -> list[tuple[float, float]]:
    """Merged primary-track timeline ranges - the dialogue to duck under."""
    spans = sorted(
        (parse_timecode(c.timeline_in), parse_timecode(c.timeline_out))
        for c in blueprint.cuts
        if c.track == "V1_A1_PRIMARY"
    )
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def duck_keyframes(
    cut: Cut,
    voice: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Clip-local (seconds, dB) keyframes ducking `cut` under `voice`.

    Returns [] when the clip never overlaps dialogue. Dip regions are
    expanded by the ramp time and merged first, so two dialogue lines close
    together produce one continuous dip rather than a pump.
    """
    db = TRACK_DUCK_DB.get(cut.track)
    if db is None:
        return []
    clip_in = parse_timecode(cut.timeline_in)
    clip_out = parse_timecode(cut.timeline_out)
    clip_len = clip_out - clip_in
    if clip_len <= 0:
        return []

    # Clip-local voice overlaps, expanded by the ramp, then merged.
    regions: list[list[float]] = []
    for vs, ve in voice:
        lo, hi = max(vs, clip_in) - clip_in, min(ve, clip_out) - clip_in
        if hi <= lo:
            continue
        rs, re = max(0.0, lo - DUCK_RAMP_S), min(clip_len, hi + DUCK_RAMP_S)
        if regions and rs <= regions[-1][1]:
            regions[-1][1] = max(regions[-1][1], re)
        else:
            regions.append([rs, re])

    if not regions:
        return []

    points: list[tuple[float, float]] = [(0.0, 0.0)]
    for rs, re in regions:
        ramp = min(DUCK_RAMP_S, (re - rs) / 2)
        points += [(rs, 0.0), (rs + ramp, db), (re - ramp, db), (re, 0.0)]
    points.append((clip_len, 0.0))

    # Dedupe identical timestamps (keep the first occurrence - later points
    # at the same time would be no-ops anyway since dips were merged).
    deduped: list[tuple[float, float]] = []
    for t, v in points:
        if deduped and t == deduped[-1][0]:
            continue
        deduped.append((t, v))
    return deduped
