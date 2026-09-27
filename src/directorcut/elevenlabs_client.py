"""ElevenLabs client for AI-generated SFX and music, with dev-mode synthesis.

Endpoints:
- SFX:   POST https://api.elevenlabs.io/v1/sound-generation  (audio/mpeg out)
- Music: POST https://api.elevenlabs.io/v1/music             (audio file out)

In dev mode or without a key, a deterministic sine-tone WAV is synthesized
with stdlib `wave` so the pipeline stays fully runnable offline.
"""

from __future__ import annotations

import io
import math
import struct
import time
import wave
from pathlib import Path

import httpx

from directorcut.config import Settings
from directorcut.exceptions import ElevenLabsError
from directorcut.ingest import _probe_duration
from directorcut.log import get_logger, register_secrets
from directorcut.safety.ledger import active_ledger

logger = get_logger(__name__)

SFX_URL = "https://api.elevenlabs.io/v1/sound-generation"
MUSIC_URL = "https://api.elevenlabs.io/v1/music"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# API limits: sound effects max out at 22s; music is bounded to 3s-600s.
MAX_SFX_SECONDS = 22.0
MIN_MUSIC_MS = 3_000
MAX_MUSIC_MS = 600_000
# Dev stubs stay small on disk even if the cue asks for minutes of audio.
_STUB_SECONDS_CAP = 10.0

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def synth_wav_bytes(
    duration_seconds: float,
    freq: float = 440.0,
    sample_rate: int = 22050,
) -> bytes:
    """Return a quiet sine-tone WAV - the dev-mode stand-in for real audio."""
    frames = int(duration_seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    int(0.2 * 32767 * math.sin(2 * math.pi * freq * i / sample_rate)),
                )
                for i in range(frames)
            )
        )
    return buf.getvalue()


class ElevenLabsClient:
    """Text-to-SFX and text-to-music calls with retries and dev fallback."""

    def __init__(
        self,
        settings: Settings,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self.settings = settings
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._http = httpx.Client(timeout=300.0)
        register_secrets(settings.configured_secrets())

    @property
    def _offline(self) -> bool:
        return self.settings.dev_mode or self.settings.safe_elevenlabs_key is None

    def _check_cap(self) -> None:
        """Abort before a paid call when the ledger is already over cap."""
        ledger = active_ledger()
        if ledger is not None:
            ledger.check_cap()

    @staticmethod
    def _record(kind: str, seconds: float, stub: bool = False) -> None:
        ledger = active_ledger()
        if ledger is not None:
            ledger.record_elevenlabs(kind=kind, seconds=seconds, stub=stub)

    def generate_sfx(self, text: str, duration_seconds: float) -> tuple[bytes, str]:
        """Return (audio_bytes, extension) for a generated sound effect."""
        duration = min(max(0.5, duration_seconds), MAX_SFX_SECONDS)
        if self._offline:
            logger.info("Dev/offline mode: synthesizing SFX stub (%.1fs)", duration)
            self._record("sfx", duration, stub=True)
            return synth_wav_bytes(min(duration, _STUB_SECONDS_CAP)), "wav"

        self._check_cap()
        payload = {"text": text, "duration_seconds": duration}
        blob = self._post(SFX_URL, payload, params={"output_format": "mp3_44100_128"})
        self._record("sfx", duration)
        return blob, "mp3"

    def generate_music(self, prompt: str, length_ms: int) -> tuple[bytes, str]:
        """Return (audio_bytes, extension) for a generated music bed."""
        length_ms = min(max(MIN_MUSIC_MS, length_ms), MAX_MUSIC_MS)
        if self._offline:
            logger.info(
                "Dev/offline mode: synthesizing music stub (%.1fs)", length_ms / 1000
            )
            self._record("music", length_ms / 1000, stub=True)
            return (
                synth_wav_bytes(min(length_ms / 1000, _STUB_SECONDS_CAP), freq=220.0),
                "wav",
            )

        self._check_cap()
        payload = {
            "prompt": prompt,
            "music_length_ms": length_ms,
            "model_id": self.settings.elevenlabs_music_model,
        }
        blob = self._post(MUSIC_URL, payload)
        self._record("music", length_ms / 1000)
        return blob, "mp3"

    def text_to_speech(
        self, text: str, voice_id: str | None = None
    ) -> tuple[bytes, str]:
        """Return (audio_bytes, extension) for a spoken pickup line.

        Voice comes from `ELEVENLABS_VOICE_ID` unless overridden - used to
        patch a flubbed or missing line without re-shooting. Billed per
        character; metered as seconds of requested speech (~15 chars/s).
        """
        voice = voice_id or self.settings.elevenlabs_voice_id
        if not voice:
            raise ElevenLabsError(
                "No ElevenLabs voice configured - set ELEVENLABS_VOICE_ID"
            )
        if self._offline:
            duration = min(max(len(text) / 15.0, 1.0), _STUB_SECONDS_CAP)
            logger.info("Dev/offline mode: synthesizing TTS stub (%.1fs)", duration)
            self._record("tts", duration, stub=True)
            return synth_wav_bytes(duration, freq=330.0), "wav"

        self._check_cap()
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        blob = self._post(
            f"{TTS_URL}/{voice}",
            payload,
            params={"output_format": "mp3_44100_128"},
        )
        self._record("tts", len(text) / 15.0)
        return blob, "mp3"

    def speech_to_text(self, audio_path: Path) -> dict:
        """Transcribe `audio_path` via Scribe; returns the parsed JSON body.

        The response carries `words[]` with start/end/type entries
        (word|spacing|audio_event) - the caller groups them into nodes.
        Multipart upload; files up to 5GB are accepted so long captures
        (e.g. a 2h stream) go up whole.
        """
        if self._offline:
            raise ElevenLabsError(
                "ElevenLabs STT unavailable: dev mode or no ELEVENLABS_API_KEY"
            )
        self._check_cap()
        key = self.settings.safe_elevenlabs_key or ""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with audio_path.open("rb") as fh:
                    resp = self._http.post(
                        STT_URL,
                        headers={"xi-api-key": key},
                        data={
                            "model_id": self.settings.elevenlabs_stt_model,
                            "timestamps_granularity": "word",
                        },
                        files={"file": (audio_path.name, fh, "audio/mpeg")},
                    )
                resp.raise_for_status()
                # Scribe bills per audio minute - meter the probed length,
                # not wall time (a retried upload shouldn't double-count).
                self._record("stt", _probe_duration(audio_path))
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            except (httpx.TransportError, OSError) as exc:
                last_exc = exc
            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.warning(
                    "ElevenLabs STT failed (attempt %d/%d): %s - retrying in %.1fs",
                    attempt + 1,
                    self.max_retries,
                    last_exc,
                    delay,
                )
                time.sleep(delay)
        raise ElevenLabsError(
            f"ElevenLabs STT failed after {self.max_retries} attempt(s): {last_exc}"
        )

    def _post(
        self,
        url: str,
        payload: dict,
        params: dict | None = None,
    ) -> bytes:
        key = self.settings.safe_elevenlabs_key
        headers = {"xi-api-key": key or "", "Accept": "audio/mpeg"}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._http.post(url, json=payload, params=params, headers=headers)
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            except httpx.TransportError as exc:
                last_exc = exc

            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.warning(
                    "ElevenLabs call failed (attempt %d/%d): %s - retrying in %.1fs",
                    attempt + 1,
                    self.max_retries,
                    last_exc,
                    delay,
                )
                time.sleep(delay)

        logger.error(
            "ElevenLabs call failed after %d attempt(s): %s", self.max_retries, last_exc
        )
        raise ElevenLabsError(
            f"ElevenLabs call failed after {self.max_retries} attempt(s): {last_exc}"
        )
