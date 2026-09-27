"""Tests for the faster-whisper local STT backend.

faster_whisper is an optional dependency, so these tests stub it via a fake
`faster_whisper` module in sys.modules - they must never download a model or
touch the network.
"""

import json
import sys
import types
from dataclasses import dataclass

import pytest

import directorcut.narrative.stt as stt
from directorcut.config import Settings
from directorcut.exceptions import DirectorCutError
from directorcut.narrative.stt import (
    _stt_cache_file,
    _stt_model_component,
    load_cached_words,
    transcribe_audio,
)


def _prod_settings(**kwargs) -> Settings:
    # _env_file=None keeps repo .env / user config /dotenv out; env="prod"
    # turns dev stubs off so the backend dispatch actually runs.
    return Settings(_env_file=None, env="prod", **kwargs)


@dataclass
class _FakeWord:
    word: str
    start: float
    end: float


@dataclass
class _FakeSegment:
    text: str
    words: list


class _FakeWhisperModel:
    """Records constructor args; returns fixed segments from transcribe()."""

    instances = []

    def __init__(self, model_size, device=None, compute_type=None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.calls = []
        _FakeWhisperModel.instances.append(self)

    def transcribe(self, path, word_timestamps=False, **kwargs):
        self.calls.append({"path": path, "word_timestamps": word_timestamps})
        segments = [
            _FakeSegment(
                " Hello there.",
                [_FakeWord(" Hello", 0.0, 0.4), _FakeWord(" there.", 0.4, 0.8)],
            ),
            _FakeSegment(
                " Second line.",
                [_FakeWord(" Second", 1.0, 1.3), _FakeWord(" line.", 1.3, 1.7)],
            ),
        ]
        return iter(segments), object()


def _fake_whisper_module():
    module = types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    return module


@pytest.fixture(autouse=True)
def _clean_whisper_cache(monkeypatch):
    # Ambient ELEVENLABS_API_KEY would flip `auto` resolution to elevenlabs.
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    _FakeWhisperModel.instances.clear()
    stt._WHISPER_MODELS.clear()
    yield
    stt._WHISPER_MODELS.clear()


def test_resolved_stt_backend_auto():
    with_key = _prod_settings(elevenlabs_api_key="el-key")
    assert with_key.resolved_stt_backend == "elevenlabs"
    keyless = _prod_settings()
    assert keyless.resolved_stt_backend == "whisper"
    explicit = _prod_settings(stt_backend="whisper", elevenlabs_api_key="el-key")
    assert explicit.resolved_stt_backend == "whisper"


def test_whisper_transcribe_maps_words(monkeypatch, tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "faster_whisper", _fake_whisper_module())
    settings = _prod_settings(stt_backend="whisper")

    nodes = transcribe_audio(audio, settings)

    assert nodes is not None
    assert [n.spoken_text for n in nodes] == ["Hello there.", "Second line."]
    assert nodes[0].time_start == "00:00:00.000"
    assert nodes[0].time_end == "00:00:00.800"
    inst = _FakeWhisperModel.instances[0]
    assert inst.model_size == "small"  # DIRECTORCUT_WHISPER_MODEL default
    assert inst.device == "cpu"
    assert inst.compute_type == "int8"
    assert inst.calls[0]["word_timestamps"] is True


def test_whisper_model_instance_cached(monkeypatch, tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "faster_whisper", _fake_whisper_module())
    settings = _prod_settings(stt_backend="whisper")

    transcribe_audio(audio, settings)
    transcribe_audio(audio, settings)

    assert len(_FakeWhisperModel.instances) == 1


def test_whisper_missing_package_explicit_raises(monkeypatch, tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    # A None sys.modules entry forces ImportError on `import faster_whisper`.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    settings = _prod_settings(stt_backend="whisper")

    with pytest.raises(DirectorCutError, match="pip install faster-whisper"):
        transcribe_audio(audio, settings)


def test_whisper_missing_package_auto_falls_back(monkeypatch, tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    settings = _prod_settings()  # auto + no key -> whisper

    assert transcribe_audio(audio, settings) is None


def test_explicit_elevenlabs_without_key_falls_back(tmp_path):
    audio = tmp_path / "mic.mp3"
    audio.write_bytes(b"fake")
    settings = _prod_settings(stt_backend="elevenlabs")
    assert transcribe_audio(audio, settings) is None


def test_stt_cache_key_includes_backend(tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    whisper = _prod_settings(stt_backend="whisper")
    elevenlabs = _prod_settings(stt_backend="elevenlabs", elevenlabs_api_key="k")

    whisper_file = _stt_cache_file(audio, _stt_model_component(whisper), tmp_path)
    elevenlabs_file = _stt_cache_file(
        audio, _stt_model_component(elevenlabs), tmp_path
    )

    assert whisper_file.name.endswith("_whisper_small.json")
    assert elevenlabs_file.name.endswith("_elevenlabs_scribe_v1.json")
    assert whisper_file != elevenlabs_file


def test_whisper_transcribe_writes_backend_keyed_cache(monkeypatch, tmp_path):
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "faster_whisper", _fake_whisper_module())
    settings = _prod_settings(stt_backend="whisper")
    cache_dir = tmp_path / "stt_cache"

    nodes = transcribe_audio(audio, settings, cache_dir=cache_dir)

    assert nodes
    entries = list(cache_dir.glob("*_whisper_small.json"))
    assert len(entries) == 1
    cached = json.loads(entries[0].read_text(encoding="utf-8"))
    assert cached["words"][0]["type"] == "word"
    # Second run is served from cache - no second model call.
    assert transcribe_audio(audio, settings, cache_dir=cache_dir)
    assert len(_FakeWhisperModel.instances[0].calls) == 1


def test_load_cached_words_falls_back_across_backends(tmp_path):
    """An elevenlabs cache entry still serves word lookups when the current
    settings resolve to whisper (e.g. export_pack's bare Settings)."""
    audio = tmp_path / "mic.wav"
    audio.write_bytes(b"fake")
    digest = stt._audio_digest(audio)
    entry = tmp_path / f"{digest}_elevenlabs_scribe_v1.json"
    entry.write_text(
        json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.2, "type": "word"}]}),
        encoding="utf-8",
    )
    settings = _prod_settings(stt_backend="whisper")

    words = load_cached_words(audio, settings, tmp_path)

    assert words == [{"text": "hi", "start": 0.0, "end": 0.2, "type": "word"}]
