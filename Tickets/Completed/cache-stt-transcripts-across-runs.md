# Cache STT transcripts across runs

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Bug / Cost |
| Created | 2026-09-22 |

## Summary

Every pipeline restart re-runs ElevenLabs Scribe on the same audio.
During the take-2 run, the pipeline restarted five times fighting API 503s
and each restart re-transcribed both A-roll takes (~$0.10/file/run).

Cache transcripts keyed on a content hash of the audio file (+ model id) in
`output/<project>/stt_cache/` so `narrative/stt.py::transcribe_audio` is
called at most once per file per project.

## Acceptance Criteria

- [ ] Re-running a project that already transcribed a file performs zero STT API calls for it
- [ ] Cache key covers audio content hash + STT model; different model re-transcribes
- [ ] Unit test: fake STT client called once across two _transcript_sources-driven Pass-1 runs
- [ ] Cache path documented in docs/wiki/pass1_narrative.md

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. `narrative/stt.py::transcribe_audio(cache_dir=)` persists
  the raw Scribe response to `output/<project>/stt_cache/<sha256(audio)>_<model>.json`
  (tmp+rename atomic write; corrupt/torn entries re-transcribe). Different
  `ELEVENLABS_STT_MODEL` → different cache file; identical bytes under a
  different model get their own entry. Pipeline passes `output/<project>/stt_cache/`
  per source; `--fresh` wipes it. Cache-hit short-circuits before any client
  construction. Tests in `tests/test_resume.py`: fake client called once across
  two runs, model-sensitive keys, empty-words cached. Docs: pass1_narrative.md.
