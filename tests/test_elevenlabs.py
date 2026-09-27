import io
import wave

import httpx
import pytest
import respx

from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient, synth_wav_bytes
from directorcut.exceptions import ElevenLabsError


def _wav_seconds(blob: bytes) -> float:
    with wave.open(io.BytesIO(blob), "rb") as w:
        return w.getnframes() / w.getframerate()


def test_synth_wav_produces_valid_audio():
    blob = synth_wav_bytes(2.0)
    assert _wav_seconds(blob) == pytest.approx(2.0, abs=0.01)


def test_dev_mode_sfx_returns_wav(settings):
    client = ElevenLabsClient(settings)
    blob, ext = client.generate_sfx("boom", 2.0)
    assert ext == "wav"
    assert _wav_seconds(blob) == pytest.approx(2.0, abs=0.01)


def test_dev_mode_music_returns_wav(settings):
    client = ElevenLabsClient(settings)
    blob, ext = client.generate_music("lo-fi chill beat", 5000)
    assert ext == "wav"
    assert _wav_seconds(blob) == pytest.approx(5.0, abs=0.01)


def test_sfx_duration_clamped_to_api_max(settings):
    """The sound-generation endpoint caps at 22s; longer asks must clamp."""
    client = ElevenLabsClient(settings)
    blob, _ = client.generate_sfx("long rumble", 60.0)
    assert _wav_seconds(blob) <= 22.0


@respx.mock
def test_live_sfx_posts_to_sound_generation():
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")
    route = respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(200, content=b"mp3-bytes")
    )

    client = ElevenLabsClient(settings)
    blob, ext = client.generate_sfx("whoosh", 3.0)

    assert blob == b"mp3-bytes"
    assert ext == "mp3"
    assert route.called
    assert route.calls[0].request.headers["xi-api-key"] == "el-key"


@respx.mock
def test_live_music_posts_to_music_endpoint():
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")
    route = respx.post("https://api.elevenlabs.io/v1/music").mock(
        return_value=httpx.Response(200, content=b"song-bytes")
    )

    client = ElevenLabsClient(settings)
    blob, ext = client.generate_music("upbeat synth", 10_000)

    assert blob == b"song-bytes"
    assert ext == "mp3"
    body = route.calls[0].request.read()
    assert b"upbeat synth" in body


@respx.mock
def test_live_retries_then_raises(monkeypatch):
    monkeypatch.setattr("directorcut.elevenlabs_client.time.sleep", lambda s: None)
    settings = Settings(_env_file=None, elevenlabs_api_key="el-key", env="prod")
    route = respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    client = ElevenLabsClient(settings, max_retries=2, base_delay=0.01)
    with pytest.raises(ElevenLabsError):
        client.generate_sfx("bang", 1.0)
    assert route.call_count == 2


def test_tts_requires_configured_voice(settings):
    """No voice id anywhere -> clear error instead of a malformed request."""
    # .env may legitimately carry ELEVENLABS_VOICE_ID - pin it empty here.
    client = ElevenLabsClient(
        settings.model_copy(update={"elevenlabs_voice_id": ""})
    )
    with pytest.raises(ElevenLabsError, match="ELEVENLABS_VOICE_ID"):
        client.text_to_speech("pickup line")


def test_dev_mode_tts_returns_wav(settings):
    settings.elevenlabs_voice_id = "voice-1"
    client = ElevenLabsClient(settings)
    blob, ext = client.text_to_speech("a quick pickup line")
    assert ext == "wav"
    assert _wav_seconds(blob) == pytest.approx(len("a quick pickup line") / 15.0, abs=0.2)


@respx.mock
def test_live_tts_posts_to_voice_endpoint():
    settings = Settings(
        _env_file=None,
        elevenlabs_api_key="el-key",
        elevenlabs_voice_id="voice-1",
        env="prod",
    )
    route = respx.post(
        "https://api.elevenlabs.io/v1/text-to-speech/voice-1"
    ).mock(return_value=httpx.Response(200, content=b"mp3-bytes"))

    blob, ext = ElevenLabsClient(settings).text_to_speech("hello there")
    assert ext == "mp3"
    assert blob == b"mp3-bytes"
    body = route.calls[0].request.read()
    assert b"hello there" in body and b"eleven_multilingual_v2" in body
