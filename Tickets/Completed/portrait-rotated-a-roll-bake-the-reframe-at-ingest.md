# Portrait / rotated A-roll: bake the reframe at ingest

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

Rotated phone footage (rotation=90, 2160x3840 effective) pillarboxes
in a 16:9 timeline, and Resolve mis-applies FCPXML adjust-transform on it
(black frames, verified). Working recipe exists:
`crop=2160:1215:0:1024,scale=1920:1080` into `A-roll reframed.mp4`
(tasks/sts2-ea-review-run).

At ingest (or a dedicated reframe pass), detect stream rotation / non-target
aspect; offer an automatic reframe-to-sequence-AR bake (h264, no rotation
metadata) and point the blueprint at the reframed file. Treat FCPXML
transforms as placement-only for Resolve targets.

## Acceptance Criteria

- [ ] Portrait A-roll produces a 16:9 spine clip with no adjust-transform needed
- [ ] Reframe keeps audio (stream copy or normalized audio when normalize ticket lands)
- [ ] Rotation detection + unit test with a rotated fixture
- [ ] Direction/config opt-out keeps original file

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented in `media_prep.py` (`prepare_aroll_media`, runs
  after `interpret_assets`, before proxies/STT). `probe_geometry` reads
  `stream_side_data=rotation` + dims; `needs_reframe` triggers on any
  rotation or >2% off 16:9; `reframe_filter` emits a face-biased
  (~39%-down) `crop` + `scale=1920:1080:flags=lanczos` -> h264/yuv420p with
  audio copied and rotation dropped by autorotate. Output lands in
  `output/<project>/media/<stem>_reframed.mp4`; `Asset.path` re-points,
  `Asset.derived_from` keeps the original. Opt-outs:
  `DIRECTORCUT_REFRAME_AROLL=false` or direction text ("no reframe" /
  "keep portrait framing"). Stubbed-ffmpeg tests in `tests/test_media_prep.py`.
