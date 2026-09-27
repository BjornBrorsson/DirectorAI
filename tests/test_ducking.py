from pathlib import Path

from directorcut.conformer.ducking import duck_keyframes, voice_ranges
from directorcut.models import AssemblyBlueprint, Cut, TimelineMeta


def _cut(track, timeline_in, timeline_out, source_file=None):
    return Cut(
        track=track,
        source_file=source_file or Path("/tmp/src.mp4"),
        timeline_in=timeline_in,
        timeline_out=timeline_out,
        source_in="00:00:00.000",
        source_out=timeline_out,
    )


def _blueprint(cuts):
    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="t", target_fps=60.0), cuts=cuts
    )


def test_voice_ranges_merges_adjacent_primary_clips():
    bp = _blueprint([
        _cut("V1_A1_PRIMARY", "00:00:00.000", "00:00:05.000"),
        _cut("V1_A1_PRIMARY", "00:00:05.000", "00:00:10.000"),
        _cut("V1_A1_PRIMARY", "00:00:15.000", "00:00:20.000"),
    ])
    assert voice_ranges(bp) == [(0.0, 10.0), (15.0, 20.0)]


def test_music_ducks_under_dialogue(tmp_path):
    music = _cut("A2_MUSIC", "00:00:00.000", "00:00:20.000", tmp_path / "bed.mp3")
    voice = [(5.0, 10.0)]
    kfs = duck_keyframes(music, voice)
    values = {round(v, 1) for _, v in kfs}
    assert -12.0 in values
    assert kfs[0] == (0.0, 0.0)
    assert kfs[-1] == (20.0, 0.0)
    # The dip itself spans the voice range (clip-local time).
    times = [t for t, v in kfs if v == -12.0]
    assert min(times) <= 5.0 and max(times) >= 10.0


def test_sfx_duck_is_shallower_than_music(tmp_path):
    sfx = _cut("A3_SFX", "00:00:00.000", "00:00:02.000", tmp_path / "hit.mp3")
    voice = [(0.5, 1.5)]
    kfs = duck_keyframes(sfx, voice)
    assert min(v for _, v in kfs) == -6.0


def test_no_overlap_no_keyframes(tmp_path):
    music = _cut("A2_MUSIC", "00:00:20.000", "00:00:40.000", tmp_path / "bed.mp3")
    voice = [(0.0, 10.0)]
    assert duck_keyframes(music, voice) == []


def test_close_voice_segments_merge_into_one_dip(tmp_path):
    """Two dialogue lines closer than the ramp must not pump the music."""
    music = _cut("A2_MUSIC", "00:00:00.000", "00:00:20.000", tmp_path / "bed.mp3")
    voice = [(5.0, 5.5), (5.7, 6.2)]
    kfs = duck_keyframes(music, voice)
    # One merged dip -> exactly two keyframes at the duck depth, not four.
    dips = [t for t, v in kfs if v == -12.0]
    assert len(dips) == 2
