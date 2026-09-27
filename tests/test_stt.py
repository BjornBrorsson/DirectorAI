"""Tests for ElevenLabs Scribe speech-to-text -> narrative nodes."""

import httpx
import pytest
import respx

from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient
from directorcut.exceptions import ElevenLabsError
from directorcut.narrative.stt import _words_to_nodes, transcribe_audio


def _word(text, start, end):
    return {"text": text, "start": start, "end": end, "type": "word"}


def _spacing(start, end):
    return {"text": " ", "start": start, "end": end, "type": "spacing"}


def test_words_to_nodes_groups_sentences():
    words = [
        _word("Hello", 0.0, 0.3),
        _spacing(0.3, 0.35),
        _word("there.", 0.35, 0.7),
        _spacing(0.7, 0.8),
        _word("Second", 0.8, 1.1),
        _spacing(1.1, 1.15),
        _word("line.", 1.15, 1.5),
    ]
    nodes = _words_to_nodes(words)

    assert len(nodes) == 2
    assert nodes[0].spoken_text == "Hello there."
    assert nodes[0].time_start == "00:00:00.000"
    assert nodes[0].time_end == "00:00:00.800"
    assert nodes[1].spoken_text == "Second line."


def test_words_to_nodes_breaks_on_pause():
    words = [
        _word("First", 0.0, 0.4),
        _spacing(0.4, 0.5),
        _word("part", 0.5, 0.9),
        # 4s silence - new node even without punctuation
        _word("Later", 5.0, 5.4),
        _word("bit", 5.5, 5.8),
    ]
    nodes = _words_to_nodes(words)

    assert len(nodes) == 2
    assert nodes[1].time_start == "00:00:05.000"


def test_words_to_nodes_caps_node_length():
    # 20 one-second words with no punctuation -> two nodes at the 15s cap.
    words = []
    t = 0.0
    for i in range(20):
        words.append(_word(f"w{i}", t, t + 0.8))
        words.append(_spacing(t + 0.8, t + 1.0))
        t += 1.0
    nodes = _words_to_nodes(words)

    assert len(nodes) == 2
    assert nodes[0].time_start == "00:00:00.000"


def test_words_to_nodes_skips_audio_events():
    words = [
        _word("Nice", 0.0, 0.4),
        {"text": "(laughter)", "start": 0.4, "end": 0.9, "type": "audio_event"},
        _word("one.", 1.0, 1.3),
    ]
    nodes = _words_to_nodes(words)

    assert len(nodes) == 1
    assert "laughter" not in nodes[0].spoken_text


def test_transcribe_audio_offline_returns_none(settings, tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    assert transcribe_audio(audio, settings) is None  # settings fixture is dev


def test_transcribe_audio_calls_client(monkeypatch, tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")

    monkeypatch.setattr(
        ElevenLabsClient,
        "speech_to_text",
        lambda self, path: {"words": [_word("Hello.", 0.0, 0.5)]},
    )
    nodes = transcribe_audio(audio, settings)

    assert nodes is not None
    assert nodes[0].spoken_text == "Hello."


def test_transcribe_audio_falls_back_on_error(monkeypatch, tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")

    def _fail(self, path):
        raise ElevenLabsError("boom")

    monkeypatch.setattr(ElevenLabsClient, "speech_to_text", _fail)
    assert transcribe_audio(audio, settings) is None


def test_transcribe_audio_falls_back_on_empty(monkeypatch, tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")

    monkeypatch.setattr(
        ElevenLabsClient, "speech_to_text", lambda self, path: {"words": []}
    )
    assert transcribe_audio(audio, settings) is None


@respx.mock
def test_speech_to_text_posts_multipart(tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake-mp3")
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")
    route = respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
        return_value=httpx.Response(
            200, json={"text": "hi", "words": [_word("hi", 0.0, 0.2)]}
        )
    )

    result = ElevenLabsClient(settings).speech_to_text(audio)

    assert route.called
    assert result["words"][0]["text"] == "hi"


def test_speech_to_text_offline_raises(settings, tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient(settings).speech_to_text(audio)


def test_transcript_sources_multi_aroll(tmp_path, monkeypatch):
    """Pass 1 transcribes every speech source once: each A-roll video (its
    extracted stream0 proxy when present) plus standalone voiceovers, but
    not the already-extracted streams that would transcribe twice."""
    import directorcut.pipeline as pipeline
    from directorcut.models import Asset, AssetRole, IngestManifest

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    take1, take2 = tmp_path / "take1.mp4", tmp_path / "take2.mp4"
    stream0 = output_dir / "take1_stream0.mp3"
    stream0.write_bytes(b"x")  # multi-track split already ran for take1
    vo = tmp_path / "notes.mp3"
    manifest = IngestManifest(
        project_name="t",
        assets=[
            Asset(path=take1, role=AssetRole.A_ROLL),
            Asset(path=take2, role=AssetRole.A_ROLL),
            Asset(path=vo, role=AssetRole.VOICEOVER),
            Asset(path=stream0, role=AssetRole.VOICEOVER),
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_audio",
        lambda src, out: out / f"{src.stem}_audio.mp3",
    )

    sources = pipeline._transcript_sources(manifest, output_dir)

    assert sources == [
        (stream0, take1),  # reused stream0, source stamped as the video
        (output_dir / "take2_audio.mp3", take2),
        (vo, vo),
    ]


def test_words_to_nodes_handles_zero_duration_words():
    """Scribe emits zero-duration words on long captures (echoed re-takes
    share a timestamp). Nodes must satisfy time_end > time_start anyway."""
    words = [
        {"text": "Intro line.", "start": 1.0, "end": 3.0, "type": "word"},
        {"text": "Yeah.", "start": 10.0, "end": 10.0, "type": "word"},
        {"text": " ", "start": 10.0, "end": 10.0, "type": "spacing"},
        {"text": "Yeah,", "start": 10.0, "end": 10.4, "type": "word"},
        {"text": " ", "start": 10.4, "end": 10.6, "type": "spacing"},
        {"text": "we went.", "start": 10.6, "end": 11.2, "type": "word"},
    ]
    nodes = _words_to_nodes(words)
    assert nodes
    for n in nodes:
        assert n.time_end > n.time_start
    # The zero-duration "Yeah." survives as a ~40ms node rather than dying.
    assert any("Yeah" in n.spoken_text for n in nodes)


def test_words_to_nodes_monotonic_end_on_out_of_order():
    """An out-of-order entry must not drag node_end below node_start."""
    words = [
        {"text": "One.", "start": 5.0, "end": 5.5, "type": "word"},
        {"text": " ", "start": 4.9, "end": 5.0, "type": "spacing"},
        {"text": "Two.", "start": 4.8, "end": 5.1, "type": "word"},
    ]
    nodes = _words_to_nodes(words)
    for n in nodes:
        assert n.time_end > n.time_start
