from directorcut.assembly.crossref import match_events
from directorcut.assembly.cuts import generate_cuts
from directorcut.models import (
    EnergyLevel,
    NarrativeNode,
    ThematicTag,
    VisualEvent,
    VisualState,
)


def test_match_comedic_set_to_death():
    nodes = [
        NarrativeNode(
            node_id="N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="I can beat this boss.",
            thematic_tag=ThematicTag.COMEDIC_SET,
            energy_level=EnergyLevel.HIGH,
        )
    ]
    events = [
        VisualEvent(
            event_id="V_001",
            time_start="00:00:06.000",
            time_end="00:00:08.000",
            classification=VisualState.STATE_DEATH,
            visual_description="Game over.",
        )
    ]
    matches = match_events(nodes, events)
    assert len(matches) == 1
    assert matches[0].reason == "comedic payoff"


def test_match_callback_req_looks_backward():
    """A callback line refers to something that already happened - the
    matching visual event should be BEFORE the callback, not after."""
    nodes = [
        NarrativeNode(
            node_id="N_001",
            time_start="00:10:00.000",
            time_end="00:10:05.000",
            spoken_text="Why didn't they tell me?!",
            thematic_tag=ThematicTag.CALLBACK_REQ,
            energy_level=EnergyLevel.HIGH,
        )
    ]
    events = [
        VisualEvent(
            event_id="V_before",
            time_start="00:05:00.000",
            time_end="00:05:02.000",
            classification=VisualState.STATE_TUTORIAL,
            visual_description="Tutorial window shown earlier.",
        ),
        VisualEvent(
            event_id="V_after",
            time_start="00:15:00.000",
            time_end="00:15:02.000",
            classification=VisualState.STATE_TUTORIAL,
            visual_description="Tutorial window shown later.",
        ),
    ]
    matches = match_events(nodes, events)
    assert len(matches) == 1
    assert matches[0].event_id == "V_before"


def test_generate_cuts_primary_uses_audio_source(tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "voiceover.mp3"
    nodes = [
        NarrativeNode(
            node_id="N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="Line one.",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
        )
    ]
    events = [
        VisualEvent(
            event_id="V_001",
            time_start="00:00:00.000",
            time_end="00:00:02.000",
            classification=VisualState.STATE_TUTORIAL,
            visual_description="Tutorial popup.",
        )
    ]
    blueprint = generate_cuts(
        project_name="test",
        nodes=nodes,
        events=events,
        matches=match_events(nodes, events),
        video_source=video,
        audio_source=audio,
    )
    assert len(blueprint.cuts) > 0
    assert blueprint.timeline_meta.project_name == "test"
    primary_cuts = [c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"]
    broll_cuts = [c for c in blueprint.cuts if c.track == "V2_OVERLAY_BROLL"]
    assert all(c.source_file == audio for c in primary_cuts)
    assert all(c.source_file == video for c in broll_cuts)


def test_generate_cuts_falls_back_to_video_source_without_audio(tmp_path):
    video = tmp_path / "video.mp4"
    nodes = [
        NarrativeNode(
            node_id="N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="Line one.",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
        )
    ]
    blueprint = generate_cuts(
        project_name="test",
        nodes=nodes,
        events=[],
        matches=[],
        video_source=video,
        audio_source=video,
    )
    assert all(c.source_file == video for c in blueprint.cuts)
    assert blueprint.timeline_meta.target_fps == 60.0
