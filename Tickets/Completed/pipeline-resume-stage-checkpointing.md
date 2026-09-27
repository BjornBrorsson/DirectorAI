# Pipeline resume / stage checkpointing

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Reliability |
| Created | 2026-09-22 |

## Summary

The take-2 run restarted 5x against free-tier API failures; each
restart redid ingest, proxies, stream extraction, STT, tagging, and all
finished Pass-2 chunks (~1.5h duplicated work + paid STT calls).

Persist per-stage outputs to `output/<project>/` (proxies, extracted
streams, transcripts, tagged nodes, visual events, blueprint) and add
`--resume` (or auto-resume) so a rerun skips completed stages. Combined
with the STT-cache ticket this makes failures cheap.

## Acceptance Criteria

- [ ] Killing a run mid-Pass-2 and re-running resumes at Pass 2 with finished chunks reused
- [ ] Stale artifacts invalidated when ingest/direction changes (manifest hash)
- [ ] --fresh flag forces full re-run
- [ ] Resume documented in cli_client.md

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. `pipeline.py` writes `run_state.json` (sha256 fingerprint
  of every ingest file + effective direction) and checkpoints stage artifacts:
  `assets.json` after ingest+interpret (skips probing/stream-extraction/classifier),
  `narrative.json` BEFORE Pass 2 starts, `visual.json` + per-chunk JSONL under
  `visual_chunks/` during Pass 2 (crash loses only the in-flight chunk; torn last
  line re-analyzed), `blueprint.json` before cue render (generated-cue cuts are
  re-derived on resume to stay idempotent). Auto-resume is the default;
  `--fresh`/`fresh=True` wipes all derived artifacts incl. STT cache; changed
  inputs invalidate stage JSONs + only the changed assets' proxies; artifacts
  without run_state.json are untrusted (JSONs dropped, proxies kept via
  exist-skip). CLI `--fresh` flag added. Tests: `tests/test_resume.py` (resume,
  invalidation, fresh, chunk recovery, kill-after-Pass-1 regression). Docs:
  cli_client.md, pass2_visual.md.
