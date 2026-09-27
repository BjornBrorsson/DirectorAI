# Feature: Local STT backend (faster-whisper)

> Status: Draft | Last verified: 2026-09-23

## What it does

Adds a free, offline speech-to-text backend for unscripted footage so the
pipeline works without an ElevenLabs subscription. ElevenLabs Scribe costs
~$0.40/audio-hour and needs key+network; faster-whisper runs a local
CTranslate2 Whisper model on CPU and returns the same word-level timing the
node grouping, edge-air padding, and subtitle export rely on.

## Where the code lives

* `directorcut.narrative.stt.transcribe_audio()` — dispatches on
  `Settings.resolved_stt_backend`; both backends produce Scribe-shaped
  `{"words": [{"text","start","end","type":"word"}]}` so
  `_words_to_nodes()`, `load_cached_words()`, and
  `words_from_scribe()` work unchanged (`src/directorcut/narrative/stt.py`).
* `directorcut.narrative.stt._whisper_response()` (~L184) — runs
  `WhisperModel.transcribe(word_timestamps=True)`; faster-whisper keeps each
  word's leading space inside `word`, so emitted `type="word"` entries join
  into sentences exactly like Scribe word+spacing pairs.
* `directorcut.narrative.stt._whisper_model()` (~L168) — lazy
  `import faster_whisper`; missing package raises `DirectorCutError` naming
  `pip install faster-whisper`. Loaded models are cached per-process in
  `_WHISPER_MODELS` keyed on `(model, compute_type)` - first construction
  downloads the weights from HF (~460MB for `small`), which must not repeat
  per A-roll take.
* `directorcut.narrative.stt._stt_model_component()` (~L100) — cache model
  component is `<backend>_<model>` (`elevenlabs_scribe_v1`,
  `whisper_small`), so engines never collide on one cache entry.
* `directorcut.narrative.stt._stt_cache_lookup()` (~L139) — word-timing
  lookups try the resolved backend's entry first, then ANY `<sha256>_*.json`
  for the same audio bytes (a Scribe transcript is still the right answer
  when the current backend is whisper - e.g. `export_pack`'s bare
  `Settings(_env_file=None)`).

## Keys & config

* `DIRECTORCUT_STT_BACKEND` = `elevenlabs` | `whisper` | `auto` (default
  `auto`): auto = elevenlabs when `ELEVENLABS_API_KEY` is set, whisper
  otherwise.
* `DIRECTORCUT_WHISPER_MODEL` (default `small`), `DIRECTORCUT_WHISPER_COMPUTE_TYPE`
  (default `int8`) - CPU-friendly defaults; any faster-whisper size or HF
  model id works.
* Install: `pip install faster-whisper` or `pip install directorcut-ai[local-stt]`
  (pyproject `[project.optional-dependencies] local-stt`).

## Gotchas

* **Dev/test mode never runs whisper.** `transcribe_audio()` returns `None`
  on `settings.dev_mode` for BOTH backends - tests must not download models
  or touch the network. Whisper tests stub `sys.modules["faster_whisper"]`
  (see `tests/test_whisper_stt.py`).
* **Explicit `whisper` + missing package aborts** (DirectorCutError naming
  `pip install faster-whisper`) - the user asked for word timing, silently
  tiling segments would ship a worse video. Under `auto` it warns and falls
  back to segment tiling instead (the old keyless behaviour).
* **First whisper call is slow**: `WhisperModel("small")` downloads ~460MB
  from Hugging Face once (cached under `~/.cache/huggingface`). A ~7s SAPI
  clip then transcribed in seconds on CPU (verified 2026-09-23).
* Verified real output (2026-09-23, `tasks/local-stt/`): a
  System.Speech-synthesized wav produced 3 nodes - "Hello there."
  (0.00-0.68), "This is a director cut whisper test." (1.52-3.46), "We are
  checking word timestamps." (4.20-6.14) - with Scribe-shaped words cached
  as `<sha256>_whisper_small.json`.
* ~0.2s-vs-Scribe timing parity on a shared fixture is NOT yet verified -
  needs the same clip through both backends.
