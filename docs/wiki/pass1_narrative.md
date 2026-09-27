# Feature: Pass 1 — Narrative Mapping Engine

> Status: Draft | Last verified: 2026-09-22

## What it does

Maps script/voiceover text to timestamps and tags each spoken segment with intent (`EXPOSITION`, `COMEDIC_SET`, `EMOTIONAL_SPIKE`, `CALLBACK_REQ`) and energy level. Unscripted multi-A-roll dumps are transcribed per source so Pass 3 can pick the best take.

## Where the code lives

* `directorcut.narrative.ingest.load_narrative_input()` — collects script text + the chosen voiceover audio (`src/directorcut/narrative/ingest.py`)
* `directorcut.narrative.transcript.transcribe()` — turns script text into 5s-per-sentence nodes; **unscripted** sources are tiled into 60s "Unscripted segment" nodes covering `duration_seconds` as the last-resort fallback
* `directorcut.narrative.stt.transcribe_audio()` — STT for unscripted audio (word-level timestamps → sentence-scale nodes) via ElevenLabs Scribe **or** local faster-whisper, dispatched on `DIRECTORCUT_STT_BACKEND` (`elevenlabs`|`whisper`|`auto`; auto = Scribe when keyed, else whisper — see [stt_whisper.md](./stt_whisper.md)); `None` when dev/backend-unusable/failed → falls back to segment tiling. With `cache_dir=` set (pipeline passes `output/<project>/stt_cache/`), the raw response is persisted content-addressed so a re-run never pays for the same transcription twice.
* `directorcut.narrative.stt._stt_cache_file()` (~L118) — cache key = `sha256(audio bytes) + "_" + <backend>_<model>` → `stt_cache/<hash>_elevenlabs_scribe_v1.json` / `<hash>_whisper_small.json`; a different backend or model re-transcribes into its own entry, a changed file can never hit a stale transcript. Writes are tmp+rename so a crash can't leave a torn entry (corrupt entries are just re-transcribed). Read-side word lookups (`load_cached_words` → `_stt_cache_lookup`) try the resolved backend's entry then ANY backend's entry for the same audio bytes.
* `directorcut.narrative.tagger.tag_narrative()` — Gemini tagger; runs on script *and* STT text (real speech tags are what let the director find "fun bits"), skipped only for segment-marker nodes. Rebuilt nodes preserve `node_id`, timecodes, `spoken_text`, and `source_file`. Response schema is the lenient `_TaggedNode` (id/tag/energy only, 2026-09-27): model-emitted timecodes are discarded anyway and a mangled one used to kill whole batches; invalid enum values fall back to the original node's values.
* `directorcut.pipeline._transcript_sources()` (~L53) — builds the `(audio_to_transcribe, source_file)` list for unscripted runs: every `A_ROLL` video (reusing its `_stream0` mp3 when multi-track splitting already ran, else a fresh `extract_audio` proxy) plus standalone `VOICEOVER` assets — minus extracted streams (under `output_dir` or `<aroll>-stem` prefixed) that would transcribe the same speech twice.
* `NarrativeNode.source_file` (`models.py` ~L72) — the *video* path for video-derived audio, so Pass 3 can cut it directly; `None` for script/legacy nodes.

## Keys & config

* `GEMINI_API_KEY` / `GEMINI_PAID_KEY` (alias), `GEMINI_MODEL` — model access.

## Gotchas

* **Unscripted dumps need full-duration coverage.** Without a script, `transcribe()` used to emit a single 5s placeholder node — the director could then only cut a ~5s window, which produced the "4 second clip of nothing" FCPXML on a 1h42m livestream (2026-09-18). Fallback chain: script → ElevenLabs STT → `_unscripted_segments()` tiling (60s markers) → single placeholder.
* **STT backend is selectable** (`DIRECTORCUT_STT_BACKEND`, default `auto`): `elevenlabs` needs `ELEVENLABS_API_KEY` + prod env (`ELEVENLABS_STT_MODEL`, default `scribe_v1`); `whisper` is local faster-whisper (`DIRECTORCUT_WHISPER_MODEL`, default `small`, int8 CPU; optional `local-stt` extra — see [stt_whisper.md](./stt_whisper.md)). Dev/test mode skips both (no model downloads in tests). Words are grouped into nodes on sentence-ending punctuation, >1.5s pauses, or a 15s cap; `audio_event` entries are skipped.
* **STT transcripts are cached across runs** (added 2026-09-22): `transcribe_audio(cache_dir=...)` stores the raw Scribe JSON under `output/<project>/stt_cache/<sha256>_<model>.json`. A pipeline restart after Pass 1 costs zero STT calls; `--fresh` wipes the cache. Even an empty-words response is cached (it is the correct answer for silent audio).
* **Multi-A-roll STT** (added 2026-09-19): each source's nodes get a per-source `S{i}_` node_id prefix (`S1_N_001`) and a stamped `source_file`; the combined list is tagged in ONE `tag_narrative` call. Voiceover files that are merely extracted streams of an A-roll video are skipped to avoid double-transcribing the same speech.
* Unscripted *segment* nodes skip `tag_narrative` — placeholder text has nothing to tag. Real STT text does get tagged (costs one model call) so the director can find jokes/opinions.
* **Scribe `words[]` are untrusted timestamps** (2026-09-27): on multi-hour captures Scribe emits zero-duration words (echoed re-takes share a timestamp) and `spacing` rows with `end == start`. `_words_to_nodes` floors every node span at one frame and accumulates `node_end` with `max()` so a malformed ordering can't fail `NarrativeNode` validation.
