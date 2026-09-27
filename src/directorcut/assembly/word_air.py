"""V1 cut-edge air: pad edges onto silence using STT word timing.

The direction prompt asks for "sentence boundaries with ~0.2s air", but
the director still emits cuts that start/end tight on words. ElevenLabs
Scribe returns word-level `start`/`end` timing - this pass walks each
V1_A1_PRIMARY cut's edges outward until the edge sits inside the silence
immediately adjacent to the covered words:

- If the neighbouring silence is >= `_MIN_SILENCE_S`, the edge is placed
  inside it leaving up to `_AIR_S` of air before/after the first/last
  covered word.
- If the gap is tighter, the edge can only move to the word boundary -
  never INTO the neighbouring word: that speech belongs to another node
  ("never extend past the node's span").
- Edges never land inside a word afterwards, and two cuts on the same
  source file can never overlap (shared silence is split by clamping the
  extension back).

After padding, `compact_timeline` re-lays the spine - the conformer derives
clip duration from `source_in..source_out`, so timeline positions must be
recomputed and connected cuts re-shifted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from directorcut.assembly.director import compact_timeline
from directorcut.log import get_logger
from directorcut.models import Cut
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)

# Minimum silence able to host a cut edge (from the ticket/recipe).
MIN_SILENCE_S = 0.15
# Air target left before/after the covered words when the gap allows it.
AIR_S = 0.2
# Keep at least this much silence on the far side of an edge so it never
# touches the neighbouring word - also guarantees two cuts sharing one gap
# can never overlap (2 * guard of separation).
_EDGE_GUARD_S = 0.02
# Sub-millisecond slop so boundary-touching words don't count as covered.
_EPS = 0.001


def _edge_in(
    cut: Cut,
    words: Sequence[tuple[float, float]],
    covered_first_idx: int,
) -> float:
    """Desired padded source_in for a cut whose first covered word is
    `words[covered_first_idx]`. Extend-only: returns <= current source_in."""
    in_s = parse_timecode(cut.source_in)
    first_start = words[covered_first_idx][0]
    prev_end = words[covered_first_idx - 1][1] if covered_first_idx > 0 else None

    if prev_end is None:
        # First word of the file - the head of the media is the silence.
        desired = max(0.0, first_start - AIR_S)
    elif first_start - prev_end >= MIN_SILENCE_S:
        air = min(AIR_S, first_start - prev_end - _EDGE_GUARD_S)
        desired = first_start - air
    else:
        # No room for air - settle for the word boundary (still not
        # *inside* the word).
        desired = first_start
    return min(in_s, desired)


def _edge_out(
    cut: Cut,
    words: Sequence[tuple[float, float]],
    covered_last_idx: int,
) -> float:
    """Desired padded source_out - extend-only, returns >= current."""
    out_s = parse_timecode(cut.source_out)
    last_end = words[covered_last_idx][1]
    next_start = (
        words[covered_last_idx + 1][0]
        if covered_last_idx + 1 < len(words)
        else None
    )

    if next_start is None:
        desired = last_end + AIR_S
    elif next_start - last_end >= MIN_SILENCE_S:
        air = min(AIR_S, next_start - last_end - _EDGE_GUARD_S)
        desired = last_end + air
    else:
        desired = last_end
    return max(out_s, desired)


def _clamp_against_ranges(
    ni: float,
    no: float,
    own: tuple[float, float],
    others: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """Clamp a padded range so it can't overlap another cut's source range.

    `others` are the ORIGINAL source ranges of every non-V1 cut on the same
    file (they don't move), plus a pathological-overlap sweep for safety.
    """
    o_in, o_out = own
    for b_in, b_out in others:
        if b_out <= o_in:  # blocker fully before the cut -> head bound
            ni = max(ni, b_out)
        elif b_in >= o_out:  # blocker fully after -> tail bound
            no = min(no, b_in)
        elif b_in < ni < b_out:  # overlap on the left edge
            ni = b_out
        elif b_in < no < b_out:  # overlap on the right edge
            no = b_in
    return ni, no


def pad_v1_cut_edges(
    cuts: Sequence[Cut],
    words_by_source: Mapping[Path, Sequence[tuple[float, float]]],
) -> list[Cut]:
    """Pad V1 cut edges onto silence and recompact the timeline.

    `words_by_source` maps each A-roll source file to its sorted
    (start_s, end_s) word timings (Scribe `words[]` of type=="word").
    Cuts whose source has no word timing are left untouched. Returns the
    full cut list, re-laid via `compact_timeline` so V1 stays contiguous
    and connected cuts shift with their anchors.
    """
    v1 = [c for c in cuts if c.track == "V1_A1_PRIMARY"]

    # Pass 1: desired padded source range per V1 cut (word logic only).
    desired: dict[int, tuple[float, float]] = {}
    for cut in v1:
        words = sorted(words_by_source.get(cut.source_file) or [])
        if not words:
            continue
        in_s = parse_timecode(cut.source_in)
        out_s = parse_timecode(cut.source_out)
        covered = [
            i
            for i, (ws, we) in enumerate(words)
            if we > in_s + _EPS and ws < out_s - _EPS
        ]
        if not covered:
            continue
        ni = _edge_in(cut, words, covered[0])
        no = _edge_out(cut, words, covered[-1])
        desired[id(cut)] = (ni, no)

    # Pass 2: two V1 cuts reaching into the SAME silence from opposite ends
    # can cross (each took up to `air` of it) - split contested regions at
    # their midpoint so source ranges never overlap. Per file, in source
    # order.
    by_file: dict[Path, list[Cut]] = {}
    for cut in v1:
        by_file.setdefault(cut.source_file, []).append(cut)
    for file_cuts in by_file.values():
        ordered = sorted(file_cuts, key=lambda c: parse_timecode(c.source_in))
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            p = desired.get(id(prev))
            c = desired.get(id(cur))
            if p is None or c is None:
                continue
            if p[1] > c[0]:
                mid = (p[1] + c[0]) / 2
                desired[id(prev)] = (p[0], mid)
                desired[id(cur)] = (mid, c[1])

    # Pass 3: clamp against unmoving ranges on the same file - non-V1 cuts
    # keep their original span, V1 cuts keep their resolved span (original
    # when unpadded) - unique-source still applies to padded spans.
    blockers: dict[Path, list[tuple[Cut, float, float]]] = {}
    for c in cuts:
        if c.track == "V1_A1_PRIMARY" and id(c) in desired:
            rng = desired[id(c)]
        else:
            rng = (parse_timecode(c.source_in), parse_timecode(c.source_out))
        blockers.setdefault(c.source_file, []).append((c, rng[0], rng[1]))

    out_cuts: list[Cut] = []
    padded = 0
    for cut in cuts:
        want = desired.get(id(cut))
        if want is None:
            out_cuts.append(cut)
            continue
        in_s = parse_timecode(cut.source_in)
        out_s = parse_timecode(cut.source_out)
        others = [
            (bi, bo)
            for c, bi, bo in blockers.get(cut.source_file, [])
            if c is not cut
        ]
        ni, no = _clamp_against_ranges(want[0], want[1], (in_s, out_s), others)
        ni = max(0.0, ni)
        if no <= ni:
            out_cuts.append(cut)
            continue
        if ni < in_s - _EPS or no > out_s + _EPS:
            logger.debug(
                "Air-pad %s %.3f-%.3f -> %.3f-%.3f",
                cut.source_file.name,
                in_s,
                out_s,
                ni,
                no,
            )
            cut = cut.model_copy(
                update={
                    "source_in": format_timecode(ni),
                    "source_out": format_timecode(no),
                }
            )
            padded += 1
        out_cuts.append(cut)

    if padded:
        logger.info("Air-padded %d V1 cut edge(s) onto silence", padded)
        # Source spans changed, so the spine must be re-laid (source range
        # is authoritative for clip duration) and connected cuts re-anchored.
        out_cuts = compact_timeline(out_cuts, dropped=[])
    return out_cuts


def words_from_scribe(entries: Sequence[dict]) -> list[tuple[float, float]]:
    """(start, end) pairs for Scribe `words[]` entries of type == "word".

    `spacing`/`audio_event` entries are dropped - only spoken words bound a
    cut edge.
    """
    out: list[tuple[float, float]] = []
    for w in entries:
        if w.get("type") != "word":
            continue
        try:
            out.append((float(w["start"]), float(w["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    return out
