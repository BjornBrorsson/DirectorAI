# V1 cut edges: sentence boundaries with air

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Quality |
| Created | 2026-09-22 |

## Summary

Prompt-level "cut at sentence boundaries with ~0.2s air" mostly
worked but several take-2 cuts start/end tight on words. STT returns
word-level timing — use it: for each V1 cut, pad edges to the nearest
>=0.15s of silence before/after the covered words (clamped to keep cuts
contiguous or allow overlap-free trims).

## Acceptance Criteria

- [ ] No V1 cut boundary lands inside a word (verified vs transcript timing)
- [ ] Edges gain ~0.2s air when the source allows it
- [ ] Unit test with fabricated nodes/cuts

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented in `assembly/word_air.py::pad_v1_cut_edges`.
  `narrative/stt.load_cached_words` re-reads the content-addressed STT cache
  for raw Scribe `words[]` timing (no second transcription). Each V1 edge
  extends into neighbouring silence >=0.15s leaving up to 0.2s air; tighter
  gaps land on the word boundary; edges never end up inside a word, and
  shared-silence contention is midpoint-split then clamped against every
  other cut's source range. `compact_timeline` re-lays the spine so
  connected cuts shift with anchors. 11 fabricated-node/cut tests in
  `tests/test_word_air.py`; `pytest -q` green.
