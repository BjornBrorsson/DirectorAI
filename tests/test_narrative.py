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


def test_transcribe_unscripted_tiles_full_duration(tmp_path):
    """No script + known duration (e.g. a 2h livestream) must produce
    segment nodes covering the whole source, not a single 5s stub - the
    director can only cut inside narrative coverage."""
    nodes = transcribe(None, tmp_path / "mic.mp3", duration_seconds=130.0)

    assert len(nodes) == 3  # 60s segments: 0-60, 60-120, 120-130
    assert nodes[0].time_start == "00:00:00.000"
    assert nodes[1].time_start == "00:01:00.000"
    assert nodes[-1].time_end == "00:02:10.000"
    assert all("Unscripted segment" in n.spoken_text for n in nodes)


def test_transcribe_unscripted_without_audio_still_tiles():
    nodes = transcribe(None, None, duration_seconds=90.0)

    assert len(nodes) == 2
    assert nodes[-1].time_end == "00:01:30.000"


def test_transcribe_no_script_no_duration_placeholder():
    nodes = transcribe(None, None)

    assert len(nodes) == 1


def test_tag_narrative(settings, tmp_path):
    from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag

    nodes = [
        NarrativeNode(
            node_id="S1_N_001",
            time_start="00:00:00.000",
            time_end="00:00:05.000",
            spoken_text="Test line.",
            thematic_tag=ThematicTag.EXPOSITION,
            energy_level=EnergyLevel.MEDIUM,
            source_file=tmp_path / "take1.mp4",
        )
    ]
    tagged = tag_narrative(nodes, settings)
    assert len(tagged) == 1
    assert tagged[0].node_id == "S1_N_001"
    # Tagging rebuilds nodes - the source identity must ride along.
    assert tagged[0].source_file == tmp_path / "take1.mp4"


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


def test_tag_narrative_survives_short_response(settings):
    """A model that drops/merges nodes must not crash the pipeline: match by
    node_id, keep originals for whatever the response omitted."""
    from directorcut.gemini_client import GeminiClient
    from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag

    nodes = transcribe("First sentence. Second sentence. Third one here.", None)
    assert len(nodes) == 3

    class _FakeResult:
        narrative_nodes = [
            NarrativeNode(
                node_id=nodes[0].node_id,
                time_start=nodes[0].time_start,
                time_end=nodes[0].time_end,
                spoken_text=nodes[0].spoken_text,
                thematic_tag=ThematicTag.EMOTIONAL_SPIKE,
                energy_level=EnergyLevel.HIGH,
            )
        ]

    def _fake_call(self, **kwargs):
        return _FakeResult()

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(GeminiClient, "call", _fake_call)
        tagged = tag_narrative(nodes, settings)

    assert len(tagged) == 3  # no node lost
    assert tagged[0].thematic_tag == ThematicTag.EMOTIONAL_SPIKE
    # Untagged nodes keep their original values.
    assert tagged[2].spoken_text == nodes[2].spoken_text


def test_split_lines_preserves_punctuation():
    nodes = transcribe("Why didn't they tell me?! I bet I can beat this.", None)
    assert nodes[0].spoken_text.endswith("?!") or nodes[0].spoken_text.endswith("?")
    assert nodes[-1].spoken_text.endswith(".")
