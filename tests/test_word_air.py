"""Pass 3 V1 edge-air padding: sentence boundaries with ~0.2s of air.

Fabricated word timings + fabricated cuts only - no media, no API.
The rule under test: after padding, a V1 edge never lands inside a word,
edges gain ~0.2s of air where the source allows it, padded cuts can never
overlap, and the spine is recompacted so connected cuts keep their anchor.
"""

from __future__ import annotations

from pathlib import Path

from directorcut.assembly.word_air import (
    AIR_S,
    MIN_SILENCE_S,
    pad_v1_cut_edges,
    words_from_scribe,
)
from directorcut.models import Cut
from directorcut.utils import format_timecode, parse_timecode

A = Path("/media/aroll.mp4")
BROLL = Path("/media/broll.mp4")


def v1(in_s: float, out_s: float, tl_in: float, file: Path = A) -> Cut:
    dur = out_s - in_s
    return Cut(
        track="V1_A1_PRIMARY",
        source_file=file,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_in + dur),
        source_in=format_timecode(in_s),
        source_out=format_timecode(out_s),
    )


def overlay(tl_in: float, tl_out: float) -> Cut:
    return Cut(
        track="V2_OVERLAY_BROLL",
        source_file=BROLL,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_out),
        source_in=format_timecode(0.0),
        source_out=format_timecode(tl_out - tl_in),
    )


def _assert_edges_outside_words(
    cut: Cut, words: list[tuple[float, float]]
) -> None:
    """Neither source edge may sit strictly inside a word."""
    for edge in (
        parse_timecode(cut.source_in),
        parse_timecode(cut.source_out),
    ):
        for ws, we in words:
            assert not (ws + 0.001 < edge < we - 0.001), (
                f"edge {edge} inside word {ws}-{we}"
            )


def test_edges_gain_air_where_silence_allows() -> None:
    words = [(9.0, 9.5), (10.0, 10.8), (11.0, 12.0), (12.4, 13.0)]
    cuts = [v1(10.0, 12.0, 0.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    assert parse_timecode(out[0].source_in) == 10.0 - AIR_S
    assert parse_timecode(out[0].source_out) == 12.0 + AIR_S
    # Recompacted: spine starts at 0 and matches the new source span.
    assert parse_timecode(out[0].timeline_in) == 0.0
    assert parse_timecode(out[0].timeline_out) == 2 * AIR_S + 2.0
    _assert_edges_outside_words(out[0], words)


def test_edge_starting_inside_word_is_extended_to_cover_it() -> None:
    # Cut currently truncates w1 (ends 10.8) - it must extend to cover the
    # whole word plus air, never land inside a word.
    words = [(9.0, 9.5), (10.0, 10.8), (11.0, 12.0), (12.4, 13.0)]
    cuts = [v1(10.5, 11.5, 0.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    assert parse_timecode(out[0].source_in) == 9.8
    assert parse_timecode(out[0].source_out) == 12.2
    _assert_edges_outside_words(out[0], words)


def test_sub_min_silence_gap_gets_no_air() -> None:
    # Gap of 0.10s < MIN_SILENCE_S before the covered word: no air can be
    # added, so the edge stays at the word boundary (still valid silence).
    words = [(10.0, 11.0), (11.10, 12.0)]
    cuts = [v1(11.10, 12.0, 0.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    assert parse_timecode(out[0].source_in) == 11.10
    # Tail has no following word -> still gains full air.
    assert parse_timecode(out[0].source_out) == 12.0 + AIR_S
    _assert_edges_outside_words(out[0], words)


def test_first_word_of_file_uses_media_head_as_silence() -> None:
    words = [(0.5, 1.0), (2.0, 3.0)]
    cuts = [v1(0.5, 1.0, 0.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    assert parse_timecode(out[0].source_in) == 0.5 - AIR_S
    # Tail: gap to next word is 1.0s >= MIN_SILENCE_S -> full air.
    assert parse_timecode(out[0].source_out) == 1.0 + AIR_S


def test_shared_silence_is_split_never_overlapping() -> None:
    # Only 0.15s of silence between the words - each cut wants 0.13s of it,
    # which would cross; the contested region is split at the midpoint.
    words = [(10.0, 12.0), (12.15, 13.0)]
    gap = 12.15 - 12.0
    assert gap >= MIN_SILENCE_S
    cuts = [v1(10.0, 12.0, 0.0), v1(12.15, 13.0, 2.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    a_out = parse_timecode(out[0].source_out)
    b_in = parse_timecode(out[1].source_in)
    assert a_out == b_in  # split boundary
    assert 12.0 <= a_out <= 12.15  # still inside the silence
    _assert_edges_outside_words(out[0], words)
    _assert_edges_outside_words(out[1], words)


def test_padding_cannot_cross_another_cut_on_same_file() -> None:
    # A second V1 cut immediately after the first's original range: the
    # padded tail is clamped to the neighbour's (unpadded) rim.
    words = [(10.0, 12.0)]  # no word data for the second cut's region
    cuts = [v1(10.0, 12.0, 0.0), v1(12.0, 12.5, 2.0)]
    out = pad_v1_cut_edges(cuts, {A: words})

    assert parse_timecode(out[0].source_out) <= 12.0
    assert out[1].source_out == "00:00:12.500"


def test_overlay_shifts_with_its_padded_anchor() -> None:
    words = [(9.0, 9.5), (10.0, 12.0), (20.0, 21.0)]
    # First cut grows 0.4s (0.2 head + 0.2 tail); second cut's region has
    # no surrounding silence to grow into its head (prev word ends 21.0 ==
    # boundary... craft: second cut covers 20-21 with no other words).
    cuts = [
        v1(10.0, 12.0, 0.0),
        v1(20.0, 21.0, 2.0),
        overlay(2.2, 2.8),
    ]
    out = pad_v1_cut_edges(cuts, {A: words})
    v1s = [c for c in out if c.track == "V1_A1_PRIMARY"]
    ovl = next(c for c in out if c.track == "V2_OVERLAY_BROLL")

    assert parse_timecode(v1s[0].timeline_out) == 2.4
    assert parse_timecode(v1s[1].timeline_in) == 2.4
    # Overlay sat inside the second V1 segment -> shifted +0.4.
    assert parse_timecode(ovl.timeline_in) == 2.6
    assert parse_timecode(ovl.timeline_out) == 3.2


def test_idempotent_second_pass_changes_nothing() -> None:
    words = [(9.0, 9.5), (10.0, 10.8), (11.0, 12.0), (12.4, 13.0)]
    cuts = [v1(10.0, 12.0, 0.0)]
    once = pad_v1_cut_edges(cuts, {A: words})
    twice = pad_v1_cut_edges(once, {A: words})
    assert twice == once


def test_no_words_leaves_cuts_untouched() -> None:
    cuts = [v1(10.0, 12.0, 0.0)]
    out = pad_v1_cut_edges(cuts, {})
    assert out == cuts
    out = pad_v1_cut_edges(cuts, {A: []})
    assert out == cuts


def test_cut_covering_no_words_untouched() -> None:
    words = [(20.0, 21.0)]
    cuts = [v1(10.0, 12.0, 0.0)]
    out = pad_v1_cut_edges(cuts, {A: words})
    assert out == cuts


def test_words_from_scribe_filters_non_word_types() -> None:
    entries = [
        {"type": "word", "text": "hi", "start": 1.0, "end": 2.0},
        {"type": "spacing", "text": " "},
        {"type": "audio_event", "text": "(laughs)", "start": 2.5, "end": 3.0},
        {"type": "word", "text": "there", "start": 3.5, "end": 4.0},
    ]
    assert words_from_scribe(entries) == [(1.0, 2.0), (3.5, 4.0)]
