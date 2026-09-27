# Creator export pack (captions, chapters, thumbnails)

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

Cheap wins on top of existing data: SRT/VTT captions from narrative
nodes (word timing is already there), chapter markers from
script/direction sections, thumbnail still extraction at EMOTIONAL_SPIKE
nodes. Bundle as `output/<project>/pack/` alongside the fcpxml.

## Acceptance Criteria

- [x] SRT validates + imports alongside the timeline (SRT+VTT written;
  timestamp/shape covered by `test_export_pack.py`; real-player import not
  yet exercised)
- [x] Chapter markers land on section transitions (V1 cuts inherit the
  first covered node's thematic_tag; same-tag runs merge, runs <60s fold
  into the previous chapter — heuristic documented in
  `docs/wiki/export_pack.md`)
- [x] 3-5 candidate thumbnails written to pack/ (HIGH-energy
  EMOTIONAL_SPIKE midpoints first, then lower spikes, then longest-cut
  midpoints until ≥3; capped at 5)

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: v1 implemented in `export_pack.py`, called from
  `finalize.finalize_blueprint` so every conform (incl. `cut` ops)
  refreshes `pack/`. Captions prefer cached Scribe word timing via
  `load_cached_words` (audio proxy content-hash lookup, never re-extracts);
  falls back to distributing covered node `spoken_text` over the cut.
  Pack generation is best-effort — failures log a warning and never sink
  the conform. Tests: `tests/test_export_pack.py` (fabricated blueprint/
  narrative/STT cache; ffmpeg mocked for thumbnails).
