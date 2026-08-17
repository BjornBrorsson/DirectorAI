from directorcut.narrative.ingest import load_narrative_input
from directorcut.narrative.tagger import tag_narrative
from directorcut.narrative.transcript import transcribe


def test_load_narrative_input(tmp_path, settings):
    script = tmp_path / "script.txt"
    script.write_text("This is a test. Two sentences.", encoding="utf-8")
    inp = load_narrative_input(script, None)
    assert inp.has_script
    assert "test" in inp.text


def test_transcribe_with_script():
    nodes = transcribe("First sentence. Second sentence.", None)
    assert len(nodes) == 2
    assert nodes[0].node_id == "N_001"


def test_tag_narrative(settings):
    from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag

    nodes = [
        NarrativeNode(
            node_id="N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="Test line.",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
        )
    ]
    tagged = tag_narrative(nodes, settings)
    assert len(tagged) == 1
    assert tagged[0].node_id == "N_001"


def test_tag_narrative_multi_sentence_does_not_crash(settings):
    """Regression test: tag_narrative used to crash (zip length mismatch) on
    any script with more than one sentence, because its dev-mode stub
    re-split joined text instead of mapping 1:1 by node index."""
    nodes = transcribe("First sentence. Second sentence. Third one here.", None)
    assert len(nodes) == 3

    tagged = tag_narrative(nodes, settings)

    assert len(tagged) == 3
    assert [n.node_id for n in tagged] == [n.node_id for n in nodes]
    assert [n.spoken_text for n in tagged] == [n.spoken_text for n in nodes]


def test_split_lines_preserves_punctuation():
    nodes = transcribe("Why didn't they tell me?! I bet I can beat this.", None)
    assert nodes[0].spoken_text.endswith("?!") or nodes[0].spoken_text.endswith("?")
    assert nodes[-1].spoken_text.endswith(".")
