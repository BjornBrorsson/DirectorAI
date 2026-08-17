import pytest

from directorcut.models import (
    AssemblyBlueprint,
    Cut,
    EnergyLevel,
    NarrativeNode,
    ThematicTag,
    TimelineMeta,
    VisualEvent,
    VisualState,
)


def test_narrative_node_creation():
    node = NarrativeNode(
        node_id="N_001",
        time_start="00:01:12.500",
        time_end="00:01:15.200",
        spoken_text="I bet I can beat this boss first try.",
        thematic_tag=ThematicTag.COMEDIC_SET,
        energy_level=EnergyLevel.HIGH,
    )
    assert node.spoken_text == "I bet I can beat this boss first try."


def test_narrative_node_end_must_be_after_start():
    with pytest.raises(ValueError):
        NarrativeNode(
            node_id="N_001",
            time_start="00:01:15.200",
            time_end="00:01:12.500",
            spoken_text="backwards",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.LOW,
        )


def test_visual_event_creation():
    ev = VisualEvent(
        event_id="V_001",
        time_start="00:00:01.000",
        time_end="00:00:03.000",
        classification=VisualState.STATE_DEATH,
        visual_description="Game over screen.",
    )
    assert ev.classification == "STATE_DEATH"


def test_assembly_blueprint_creation():
    from pathlib import Path

    bp = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            Cut(
                track="V1_A1_PRIMARY",
                source_file=Path("video.mp4"),
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:01:12.500",
                source_out="00:01:15.200",
            )
        ],
    )
    assert len(bp.cuts) == 1
