# Local STT fallback (faster-whisper) for keyless/offline use

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

ElevenLabs Scribe costs ~$0.40/hr and requires a key+network.
A faster-whisper backend (small/medium model, CPU OK for A-roll lengths)
makes the tool sellable to users without an EL subscription and removes
a recurring cost. Word-level timing is required (used for node split +
cut-air ticket).

## Acceptance Criteria

- [ ] stt backend flag: elevenlabs | whisper | auto
- [ ] Whisper path produces NarrativeNode-compatible timing within ~0.2s of Scribe on a fixture
- [ ] Offline test runs without any keys set

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: implemented. `DIRECTORCUT_STT_BACKEND` = elevenlabs|whisper|auto
  (auto = elevenlabs when `ELEVENLABS_API_KEY` set, else whisper) +
  `DIRECTORCUT_WHISPER_MODEL` (default `small`) +
  `DIRECTORCUT_WHISPER_COMPUTE_TYPE` (default `int8`) in
  `config.py` (`Settings.resolved_stt_backend`). Whisper path lives in
  `narrative/stt.py` (`_whisper_response`/`_whisper_model`) behind a lazy
  `import faster_whisper`; missing package raises `DirectorCutError` naming
  `pip install faster-whisper` when explicitly selected, warns + falls back
  to segments under `auto`. `word_timestamps=True`; words map onto the
  Scribe `words[]` shape (`type="word"`, leading space inside `text`) so
  `_words_to_nodes` + `load_cached_words` are unchanged. STT cache key is
  now `<sha256>_<backend>_<model>.json`; `load_cached_words` falls back to
  any backend's entry for the same audio bytes (keeps `export_pack`'s bare
  Settings working). Dev/test mode still returns `None` for both backends -
  no model downloads in tests. Optional extra:
  `[project.optional-dependencies] local-stt`.
  Verified real: `pip install faster-whisper` -> 1.2.1; transcribed a
  System.Speech-synthesized wav (`tasks/local-stt/spoken.wav`) into 3 nodes
  ("Hello there." 0.00-0.68, "This is a director cut whisper test."
  1.52-3.46, "We are checking word timestamps." 4.20-6.14); cache file
  `…_whisper_small.json` written. First call ~204s dominated by the ~460MB
  `small` model download from HF.
  NOT verified: ~0.2s timing parity vs Scribe on a shared fixture (needs an
  ElevenLabs-keyed run of the same clip to compare).
  Tests: `tests/test_whisper_stt.py` (sys.modules-stubbed faster_whisper,
  backend resolution, cache keying, cross-backend lookup). Docs:
  `docs/wiki/stt_whisper.md` (new), `pass1_narrative.md` updated.
