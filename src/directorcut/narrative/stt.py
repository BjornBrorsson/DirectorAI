"""Speech-to-text transcription: ElevenLabs Scribe or local faster-whisper.

For unscripted dumps (livestreams, gameplay captures) there is no script to
map - an STT backend transcribes the voiceover/mic audio into word-level
timestamps, which we group into sentence-scale narrative nodes. Those carry
real spoken text, so the tagger and director finally have something to work
with instead of placeholder coverage markers.

Backends (Settings.resolved_stt_backend / DIRECTORCUT_STT_BACKEND):
- "elevenlabs": Scribe API - paid, needs key+network.
- "whisper": faster-whisper - free, offline, optional `local-stt` extra.
- "auto" (default): elevenlabs when a key is configured, whisper otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient
from directorcut.exceptions import DirectorCutError, ElevenLabsError
from directorcut.log import get_logger
from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag
from directorcut.utils import format_timecode

logger = get_logger(__name__)

# A node closes on sentence-ending punctuation, a pause, or once it runs too
# long - sentence-scale chunks tag better than unbounded paragraphs.
_SENTENCE_END_RE = re.compile(r"[.?!]['\")\]]*$")
_MAX_NODE_S = 15.0
_MAX_GAP_S = 1.5


def _words_to_nodes(words: list[dict[str, Any]]) -> list[NarrativeNode]:
    """Group Scribe `words[]` entries into narrative nodes.

    The list mixes `word`, `spacing`, and `audio_event` entries; node text is
    the raw `text` of every non-event entry joined as-is so original spacing
    survives. Boundaries fall on sentence punctuation, >1.5s pauses, or the
    15s node cap.
    """
    nodes: list[NarrativeNode] = []
    buf: list[dict[str, Any]] = []
    last_word_text = ""
    prev_end: float | None = None

    def flush() -> None:
        text = "".join(str(w.get("text", "")) for w in buf).strip()
        buf.clear()
        if not text:
            return
        i = len(nodes) + 1
        # NarrativeNode requires time_end > time_start; Scribe emits
        # zero-duration words on long captures (echoed re-takes land on the
        # same timestamp), so floor the span at one frame.
        end_tc = max(node_end, node_start + 0.04)
        nodes.append(
            NarrativeNode(
                node_id=f"N_{i:03d}",
                time_start=format_timecode(node_start),
                time_end=format_timecode(end_tc),
                spoken_text=text,
                thematic_tag=ThematicTag.EXPOSITION,
                energy_level=EnergyLevel.MEDIUM,
            )
        )

    node_start = 0.0
    node_end = 0.0
    for w in words:
        if w.get("type") == "audio_event":
            continue
        start = float(w.get("start") or 0.0)
        # Scribe can emit out-of-order or end-less entries on long captures;
        # clamp so a node never ends before it starts (model validator).
        end = max(float(w.get("end") or start), start)
        is_word = w.get("type") == "word"
        if buf and is_word:
            gap = prev_end is not None and start - prev_end > _MAX_GAP_S
            span = start - node_start > _MAX_NODE_S
            if gap or span or _SENTENCE_END_RE.search(last_word_text):
                flush()
        if not buf:
            node_start = start
            node_end = end
        else:
            node_end = max(node_end, end)
        buf.append(w)
        prev_end = max(prev_end, end) if prev_end is not None else end
        if is_word:
            last_word_text = str(w.get("text", "")).strip()
    flush()
    return nodes


def _audio_digest(audio_path: Path) -> str | None:
    """Content-hash the audio for the STT cache key; None when unreadable."""
    try:
        digest = hashlib.sha256()
        with audio_path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError as exc:
        logger.warning("Cannot hash %s for STT cache: %s", audio_path, exc)
        return None
    return digest.hexdigest()


def _stt_model_component(settings: Settings) -> str:
    """Cache-key model component: `<backend>_<model id>`.

    The backend name is baked in (`elevenlabs_scribe_v1`, `whisper_small`)
    so transcripts produced by different engines for the same audio can
    never collide on one cache entry.
    """
    backend = settings.resolved_stt_backend
    model = (
        settings.elevenlabs_stt_model
        if backend == "elevenlabs"
        else settings.whisper_model
    )
    return f"{backend}_{model}"


def _stt_cache_file(
    audio_path: Path, model: str, cache_dir: Path
) -> Path | None:
    """Return the cache path for this audio + STT model, or None if unhashable.

    The key is the audio's sha256 plus the backend-qualified model id:
    identical bytes transcribed under a different backend/model get their
    own entry, and a changed file can never hit a stale transcript.
    """
    digest = _audio_digest(audio_path)
    if digest is None:
        return None
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    return cache_dir / f"{digest}_{safe_model}.json"


def _stt_cache_lookup(
    audio_path: Path, model: str, cache_dir: Path
) -> Path | None:
    """Exact backend-keyed cache entry, else ANY backend's for this audio.

    The digest is content-addressed: for post-hoc word-timing recovery
    (edge air, subtitles) an elevenlabs transcript of the same bytes is
    still the right answer even when the current backend resolves to
    whisper - and vice versa.
    """
    digest = _audio_digest(audio_path)
    if digest is None:
        return None
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    exact = cache_dir / f"{digest}_{safe_model}.json"
    if exact.exists():
        return exact
    candidates = sorted(cache_dir.glob(f"{digest}_*.json"))
    return candidates[0] if candidates else None


# WhisperModel construction downloads/loads weights (~460MB for "small"), so
# a per-call instance would re-warm for every A-roll take - keep a per-
# process cache keyed on (model, compute_type).
_WHISPER_MODELS: dict[tuple[str, str], Any] = {}


def _whisper_model(settings: Settings) -> Any:
    """Return a cached faster-whisper WhisperModel (lazy optional import)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise DirectorCutError(
            "STT backend 'whisper' requires the optional faster-whisper "
            "package - install it with: pip install faster-whisper"
        ) from exc
    key = (settings.whisper_model, settings.whisper_compute_type)
    if key not in _WHISPER_MODELS:
        _WHISPER_MODELS[key] = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type=settings.whisper_compute_type,
        )
    return _WHISPER_MODELS[key]


def _whisper_response(audio_path: Path, settings: Settings) -> dict:
    """Transcribe via faster-whisper into a Scribe-shaped response dict.

    `word_timestamps=True` gives per-word start/end; faster-whisper keeps
    each word's leading space inside `word`, so entries map straight onto
    the Scribe `type="word"` shape and `_words_to_nodes` + the cached-words
    consumers work unchanged.
    """
    model = _whisper_model(settings)
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True)
    words: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for segment in segments:
        text_parts.append(str(getattr(segment, "text", "") or ""))
        for w in getattr(segment, "words", None) or []:
            if w.start is None or w.end is None:
                continue
            words.append(
                {
                    "text": w.word,
                    "start": float(w.start),
                    "end": float(w.end),
                    "type": "word",
                }
            )
    return {"text": "".join(text_parts), "words": words}


def _request_transcript(
    audio_path: Path, settings: Settings, backend: str
) -> dict | None:
    """One transcription call against the resolved backend; None on failure."""
    if backend == "whisper":
        try:
            return _whisper_response(audio_path, settings)
        except DirectorCutError:
            # Missing optional dep. An explicit DIRECTORCUT_STT_BACKEND=
            # whisper choice is a config error worth aborting on; `auto`
            # merely degrades to segment tiling (the old keyless behaviour).
            if settings.stt_backend == "whisper":
                raise
            logger.warning(
                "faster-whisper not installed - `pip install faster-whisper` "
                "or set ELEVENLABS_API_KEY; falling back to segments"
            )
            return None
        except Exception as exc:
            logger.warning("whisper STT failed (%s); falling back to segments", exc)
            return None
    try:
        return ElevenLabsClient(settings).speech_to_text(audio_path)
    except ElevenLabsError as exc:
        logger.warning("ElevenLabs STT failed (%s); falling back to segments", exc)
        return None


def load_cached_words(
    audio_path: Path, settings: Settings, cache_dir: Path
) -> list[dict] | None:
    """Return the raw `words[]` entries for a cached transcript.

    The STT cache stores the full backend response, which carries word-level
    `start`/`end` timing that the node grouping throws away. Pass 3's
    edge-air padding needs that resolution back - this re-reads the cache
    (content-hash keyed, so it works on resumes too) instead of paying for
    a second transcription. Looks up the resolved backend's entry first,
    then any other backend's entry for the same audio bytes.
    """
    cache_file = _stt_cache_lookup(
        audio_path, _stt_model_component(settings), Path(cache_dir)
    )
    if cache_file is None or not cache_file.exists():
        return None
    try:
        resp = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring corrupt STT cache %s: %s", cache_file, exc)
        return None
    words = resp.get("words")
    return words if isinstance(words, list) else None


def transcribe_audio(
    audio_path: Path, settings: Settings, cache_dir: Path | None = None
) -> list[NarrativeNode] | None:
    """Return narrative nodes from an STT transcript (Scribe or whisper).

    None when offline (dev mode / unusable backend) or when the call fails -
    the caller then falls back to unscripted segment tiling.

    With `cache_dir` set, the raw backend response is persisted keyed on the
    audio's content hash + backend/model, so a re-run (pipeline resume after
    a crash) never pays for the same transcription twice. Even an
    empty-words response is cached - it is the correct answer for silent
    audio.
    """
    if settings.dev_mode:
        return None

    backend = settings.resolved_stt_backend
    if backend == "elevenlabs" and settings.safe_elevenlabs_key is None:
        logger.warning(
            "DIRECTORCUT_STT_BACKEND=elevenlabs but ELEVENLABS_API_KEY is not "
            "set; falling back to segments"
        )
        return None

    cache_file: Path | None = None
    if cache_dir is not None:
        cache_file = _stt_cache_file(
            audio_path, _stt_model_component(settings), Path(cache_dir)
        )

    resp: dict | None = None
    if cache_file is not None and cache_file.exists():
        try:
            resp = json.loads(cache_file.read_text(encoding="utf-8"))
            logger.info("STT cache hit for %s (%s)", audio_path.name, cache_file.name)
        except (OSError, json.JSONDecodeError) as exc:
            # A torn/corrupt entry (crash mid-write) just means re-transcribe.
            logger.warning("Ignoring corrupt STT cache %s: %s", cache_file, exc)
            resp = None

    if resp is None:
        resp = _request_transcript(audio_path, settings, backend)
        if resp is None:
            return None
        if cache_file is not None:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                # Write-then-rename so a crash can't leave a half-written JSON.
                tmp = cache_file.with_name(cache_file.name + ".tmp")
                tmp.write_text(json.dumps(resp), encoding="utf-8")
                os.replace(tmp, cache_file)
            except OSError as exc:
                logger.warning("Could not write STT cache %s: %s", cache_file, exc)

    nodes = _words_to_nodes(resp.get("words") or [])
    if not nodes:
        logger.warning("%s STT returned no words; falling back to segments", backend)
        return None
    logger.info(
        "STT (%s) produced %d narrative node(s) from %s",
        backend,
        len(nodes),
        audio_path.name,
    )
    return nodes
