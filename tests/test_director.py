from pathlib import Path

from directorcut.assembly.director import _plan_to_blueprint, run_director
from directorcut.models import (
    Asset,
    AssetRole,
    AudioCueKind,
    EnergyLevel,
    IngestManifest,
    NarrativeNode,
    ThematicTag,
    VisualEvent,
    VisualState,
)


def _manifest(tmp_path, duration=12.0):
    return IngestManifest(
        project_name="t",
        video_path=tmp_path / "gameplay.mp4",
        audio_path=tmp_path / "voiceover.mp3",
        voiceover_path=tmp_path / "voiceover.mp3",
        script_path=tmp_path / "script.txt",
        duration_seconds=duration,
        assets=[
            Asset(path=tmp_path / "gameplay.mp4", role=AssetRole.A_ROLL),
            Asset(path=tmp_path / "cutaway.mp4", role=AssetRole.B_ROLL),
            Asset(path=tmp_path / "voiceover.mp3", role=AssetRole.VOICEOVER),
        ],
    )


def _nodes():
    return [
        NarrativeNode(
            node_id="N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="I bet I can beat this boss first try!",
            thematic_tag=ThematicTag.EMOTIONAL_SPIKE,
            energy_level=EnergyLevel.HIGH,
        )
    ]


def _events():
    return [
        VisualEvent(
            event_id="V_001",
            time_start="00:00:01.000",
            time_end="00:00:03.000",
            classification=VisualState.STATE_ACTION,
            visual_description="Fight starts.",
            source_file=None,
        )
    ]


def test_run_director_dev_mode_returns_blueprint(tmp_path, settings):
    manifest = _manifest(tmp_path)
    blueprint = run_director(manifest, _nodes(), _events(), settings)

    assert blueprint.cuts
    assert all(c.source_file.is_absolute() for c in blueprint.cuts)
    # Dev stub emits a full-length music cue + an SFX cue for the spike.
    kinds = {c.kind for c in blueprint.audio_cues}
    assert AudioCueKind.MUSIC in kinds
    assert AudioCueKind.SFX in kinds


def test_plan_drops_hallucinated_source_files(tmp_path):
    manifest = _manifest(tmp_path)
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.models import Cut

    plan = _AssemblyPlan(
        cuts=[
            Cut(
                track="V1_A1_PRIMARY",
                source_file=tmp_path / "gameplay.mp4",
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            ),
            Cut(
                track="V2_OVERLAY_BROLL",
                source_file=Path("does_not_exist.mp4"),
                timeline_in="00:00:00.000",
                timeline_out="00:00:02.000",
                source_in="00:00:00.000",
                source_out="00:00:02.000",
            ),
        ]
    )

    blueprint = _plan_to_blueprint(plan, manifest, _nodes(), _events())

    assert len(blueprint.cuts) == 1
    assert blueprint.cuts[0].source_file.name == "gameplay.mp4"


def test_plan_falls_back_when_all_cuts_invalid(tmp_path):
    manifest = _manifest(tmp_path)
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.models import Cut

    plan = _AssemblyPlan(
        cuts=[
            Cut(
                track="V1_A1_PRIMARY",
                source_file=Path("imaginary.mp4"),
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            )
        ]
    )

    blueprint = _plan_to_blueprint(plan, manifest, _nodes(), _events())

    # Deterministic fallback produced real cuts referencing known assets.
    assert blueprint.cuts
    assert all(
        c.source_file in {a.path for a in manifest.assets} for c in blueprint.cuts
    )


def test_plan_resolves_basename_match(tmp_path):
    """The model may return just the filename; resolve it to the asset."""
    manifest = _manifest(tmp_path)
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.models import Cut

    plan = _AssemblyPlan(
        cuts=[
            Cut(
                track="V2_OVERLAY_BROLL",
                source_file=Path("cutaway.mp4"),
                timeline_in="00:00:00.000",
                timeline_out="00:00:02.000",
                source_in="00:00:00.000",
                source_out="00:00:02.000",
            ),
            Cut(
                track="V1_A1_PRIMARY",
                source_file=tmp_path / "gameplay.mp4",
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            ),
        ]
    )

    blueprint = _plan_to_blueprint(plan, manifest, _nodes(), _events())
    broll = [c for c in blueprint.cuts if c.track == "V2_OVERLAY_BROLL"]
    assert broll[0].source_file == tmp_path / "cutaway.mp4"


def test_plan_drops_audio_on_visual_lane(tmp_path):
    """An mp3 cannot be a B-roll overlay - a real failure mode seen when an
    extracted audio stream was placed on V2, producing a video-free timeline."""
    manifest = _manifest(tmp_path)
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.models import Cut

    plan = _AssemblyPlan(
        cuts=[
            Cut(
                track="V1_A1_PRIMARY",
                source_file=tmp_path / "gameplay.mp4",
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            ),
            Cut(
                track="V2_OVERLAY_BROLL",
                source_file=tmp_path / "voiceover.mp3",
                timeline_in="00:00:00.000",
                timeline_out="00:00:02.000",
                source_in="00:00:00.000",
                source_out="00:00:02.000",
            ),
        ]
    )

    blueprint = _plan_to_blueprint(plan, manifest, _nodes(), _events())

    assert len(blueprint.cuts) == 1
    assert blueprint.cuts[0].track == "V1_A1_PRIMARY"


def test_plan_drops_video_on_audio_lane(tmp_path):
    manifest = _manifest(tmp_path)
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.models import Cut

    plan = _AssemblyPlan(
        cuts=[
            Cut(
                track="A2_MUSIC",
                source_file=tmp_path / "gameplay.mp4",
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            ),
            Cut(
                track="V1_A1_PRIMARY",
                source_file=tmp_path / "voiceover.mp3",
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            ),
        ]
    )

    blueprint = _plan_to_blueprint(plan, manifest, _nodes(), _events())

    # Audio on the primary storyline is legal (narrative-carrying clip) but
    # video on a music lane is not - only the voiceover cut survives.
    assert [c.track for c in blueprint.cuts] == ["V1_A1_PRIMARY"]


# --- _enforce_unique_source -------------------------------------------------


def _v1(src, tl_in, tl_out, s_in, s_out):
    from directorcut.models import Cut

    return Cut(
        track="V1_A1_PRIMARY",
        source_file=src,
        timeline_in=tl_in,
        timeline_out=tl_out,
        source_in=s_in,
        source_out=s_out,
    )


def test_enforce_trims_head_overlap_and_recompacts(tmp_path):
    """A cut whose head reuses already-seen source is trimmed, not dropped:
    its in-point advances to the accepted range's end (a natural splice),
    the timeline stays contiguous, and other tracks follow the shift."""
    from directorcut.assembly.director import _enforce_unique_source
    from directorcut.models import Cut

    take = tmp_path / "take1.mp4"
    cuts = [
        _v1(take, "00:00:00.000", "00:00:05.000", "00:00:10.000", "00:00:15.000"),
        # Reuses source 14-15 - trimmed to 15-18.
        _v1(take, "00:00:05.000", "00:00:09.000", "00:00:14.000", "00:00:18.000"),
        _v1(take, "00:00:09.000", "00:00:14.000", "00:00:30.000", "00:00:35.000"),
        # Anchored inside the trimmed V1 (new timeline 6-10) -> survives.
        Cut(
            track="V2_OVERLAY_BROLL",
            source_file=tmp_path / "b.mp4",
            timeline_in="00:00:06.000",
            timeline_out="00:00:08.000",
            source_in="00:00:00.000",
            source_out="00:00:02.000",
        ),
        # Anchored inside the third V1 (old 9-14) -> follows its shift.
        Cut(
            track="V2_OVERLAY_BROLL",
            source_file=tmp_path / "b.mp4",
            timeline_in="00:00:10.000",
            timeline_out="00:00:11.000",
            source_in="00:00:05.000",
            source_out="00:00:06.000",
        ),
        Cut(
            track="A2_MUSIC",
            source_file=tmp_path / "m.mp3",
            timeline_in="00:00:00.000",
            timeline_out="00:00:14.000",
            source_in="00:00:00.000",
            source_out="00:00:14.000",
        ),
    ]

    out = _enforce_unique_source(cuts, [])

    v1 = [c for c in out if c.track == "V1_A1_PRIMARY"]
    assert len(v1) == 3
    # Contiguous from 0; the trimmed middle cut is 3s (source 15-18).
    assert (v1[0].timeline_in, v1[0].timeline_out) == (
        "00:00:00.000",
        "00:00:05.000",
    )
    assert (v1[1].timeline_in, v1[1].timeline_out) == (
        "00:00:05.000",
        "00:00:08.000",
    )
    assert (v1[1].source_in, v1[1].source_out) == (
        "00:00:15.000",
        "00:00:18.000",
    )
    assert (v1[2].timeline_in, v1[2].timeline_out) == (
        "00:00:08.000",
        "00:00:13.000",
    )
    overlays = [c for c in out if c.track == "V2_OVERLAY_BROLL"]
    assert len(overlays) == 2
    music = [c for c in out if c.track == "A2_MUSIC"]
    assert (music[0].timeline_in, music[0].timeline_out) == (
        "00:00:00.000",
        "00:00:13.000",
    )


def test_enforce_drops_interior_source_overlap(tmp_path):
    """Reuse that head/tail trims can't fix - an accepted range wholly
    contained inside the new cut - still drops the whole cut."""
    from directorcut.assembly.director import _enforce_unique_source

    take = tmp_path / "take1.mp4"
    cuts = [
        _v1(take, "00:00:00.000", "00:00:02.000", "00:00:12.000", "00:00:14.000"),
        # Source 11-20 swallows the accepted 12-14 whole -> dropped.
        _v1(take, "00:00:02.000", "00:00:11.000", "00:00:11.000", "00:00:20.000"),
        _v1(take, "00:00:11.000", "00:00:16.000", "00:00:30.000", "00:00:35.000"),
    ]

    out = _enforce_unique_source(cuts, [])

    v1 = [c for c in out if c.track == "V1_A1_PRIMARY"]
    assert len(v1) == 2
    assert (v1[1].source_in, v1[1].source_out) == (
        "00:00:30.000",
        "00:00:35.000",
    )
    assert (v1[1].timeline_in, v1[1].timeline_out) == (
        "00:00:02.000",
        "00:00:07.000",
    )


def test_enforce_drops_repeated_take_text(tmp_path):
    """A re-take later in the same file repeats the line - different source
    range, same words. The later cut is dropped by text similarity."""
    from directorcut.assembly.director import _enforce_unique_source

    take = tmp_path / "take1.mp4"
    nodes = [
        NarrativeNode(
            node_id="S1_N_001",
            time_start="00:00:10.000",
            time_end="00:00:15.000",
            spoken_text="I cannot believe this boss fight took me three hours",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
            source_file=take,
        ),
        NarrativeNode(
            node_id="S1_N_002",
            time_start="00:00:40.000",
            time_end="00:00:45.000",
            spoken_text="Um, I cannot believe this boss fight took me three hours!",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
            source_file=take,
        ),
    ]
    cuts = [
        _v1(take, "00:00:00.000", "00:00:05.000", "00:00:10.000", "00:00:15.000"),
        _v1(take, "00:00:05.000", "00:00:10.000", "00:00:40.000", "00:00:45.000"),
    ]

    out = _enforce_unique_source(cuts, nodes)

    v1 = [c for c in out if c.track == "V1_A1_PRIMARY"]
    assert len(v1) == 1
    assert v1[0].source_in == "00:00:10.000"


def test_enforce_keeps_non_overlapping_cuts(tmp_path):
    """Distinct files and disjoint ranges pass through untouched."""
    from directorcut.assembly.director import _enforce_unique_source

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    cuts = [
        _v1(a, "00:00:00.000", "00:00:05.000", "00:00:10.000", "00:00:15.000"),
        _v1(b, "00:00:05.000", "00:00:10.000", "00:00:20.000", "00:00:25.000"),
        _v1(a, "00:00:10.000", "00:00:14.000", "00:00:30.000", "00:00:34.000"),
    ]

    out = _enforce_unique_source(cuts, [])

    assert [c.source_file for c in out] == [a, b, a]
    assert (out[2].timeline_in, out[2].timeline_out) == (
        "00:00:10.000",
        "00:00:14.000",
    )


def test_enforce_normalises_timeline_span_to_source_span(tmp_path):
    """A model slip where timeline span != source span is corrected on
    re-lay - the conformer renders source_in..source_out, so keeping the
    timeline span would leave a hole in the spine."""
    from directorcut.assembly.director import _enforce_unique_source

    take = tmp_path / "take1.mp4"
    cuts = [
        # Timeline claims 4.019s but the source range is only 3.319s.
        _v1(take, "00:00:00.000", "00:00:04.019", "00:00:00.700", "00:00:04.019"),
        _v1(take, "00:00:04.019", "00:00:09.019", "00:00:10.000", "00:00:15.000"),
    ]

    out = _enforce_unique_source(cuts, [])

    assert (out[0].timeline_in, out[0].timeline_out) == (
        "00:00:00.000",
        "00:00:03.319",
    )
    assert (out[1].timeline_in, out[1].timeline_out) == (
        "00:00:03.319",
        "00:00:08.319",
    )


def test_canonical_source_keys_collapse_dual_registered_file(tmp_path):
    """The same physical file registered as both A-roll and B-roll (a
    stream capture used for talk + gameplay) is one canonical source:
    a range cut on the V1 spine must not be reusable as an overlay, and
    media-prep derivatives resolve to the same identity via derived_from.
    """
    import os

    from directorcut.assembly.director import (
        _canonical_source_keys,
        _enforce_unique_source,
    )
    from directorcut.models import Cut

    aroll = tmp_path / "aroll" / "stream.mp4"
    broll = tmp_path / "broll" / "stream.mp4"
    baked = tmp_path / "media" / "stream_voicenorm.mp4"
    aroll.parent.mkdir()
    broll.parent.mkdir()
    baked.parent.mkdir()
    aroll.write_bytes(b"x")
    os.link(aroll, broll)  # hardlink: same inode, different path
    baked.write_bytes(b"y")

    manifest = IngestManifest(
        project_name="t",
        assets=[
            Asset(path=baked, role=AssetRole.A_ROLL, derived_from=aroll),
            Asset(path=broll, role=AssetRole.B_ROLL),
        ],
    )
    canon = _canonical_source_keys(manifest)
    assert canon[baked.resolve()] == canon[broll.resolve()]

    cuts = [
        _v1(baked, "00:00:00.000", "00:00:05.000", "00:00:10.000", "00:00:15.000"),
        Cut(
            track="V2_OVERLAY_BROLL",
            source_file=broll,
            timeline_in="00:00:02.000",
            timeline_out="00:00:04.000",
            source_in="00:00:12.000",  # inside the V1 cut's source range
            source_out="00:00:14.000",
        ),
        Cut(
            track="V2_OVERLAY_BROLL",
            source_file=broll,
            timeline_in="00:00:02.000",
            timeline_out="00:00:04.000",
            source_in="00:01:00.000",  # disjoint range - survives
            source_out="00:01:02.000",
        ),
    ]

    out = _enforce_unique_source(cuts, [], canon)

    overlays = [c for c in out if c.track == "V2_OVERLAY_BROLL"]
    assert len(overlays) == 1
    assert overlays[0].source_in == "00:01:00.000"
