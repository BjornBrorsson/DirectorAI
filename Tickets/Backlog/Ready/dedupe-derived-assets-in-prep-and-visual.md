# Dedupe derived/duplicate assets in media prep + visual proxy

| Field | Value |
|---|---|
| Status | Ready |
| Assignee | unassigned |
| Priority | High |
| Type | Cost/Efficiency |
| Created | 2026-09-27 |

## Summary

sts2-review-v3 cost ~45min of redundant encodes and ~85 duplicate
visual-classifier calls because the same underlying media was processed
twice under different paths:

- `A and B Roll.mp4` hardlinked into aroll/ + broll/ -> two 118-min
  visual proxy encodes (`_voicenorm_visual.mp4` then `_visual.mp4`),
  then ~85 Pass-2 chunks classified twice on identical frames.
- The stream's extracted mp3 streams were re-measured/re-normalized
  once per registered asset (aroll pass + broll pass).
- voicenorm bake: `A and B Roll_voicenorm.mp4` (~8min encode) plus
  `_stream0_voicenorm.m4a` re-bakes.

`_canonical_source_keys()` in assembly/director.py already builds the
inode/derived_from canonical identity for uniqueness enforcement -
media_prep and proxy/visual should use the same map to skip duplicate
work while still registering distinct manifest assets (events must keep
per-path source_file so the conformer can cut each role).

## Acceptance criteria

- [ ] Media prep measures/bakes each canonical source once; sibling
      assets reuse the artifact (hardlink or path alias).
- [x] Visual proxy: one encode per canonical video; Pass-2 events
      generated once, then cloned with `source_file` rewritten to each
      registered path so V1/V2 cuts stay distinguishable.
      DONE 2026-09-27 - `pipeline.run` Pass-2 loop dedupes via
      `_canonical_source_keys` (inode/derived_from); tested by
      `test_pass2_dedupes_canonical_twin_assets`.
- [ ] Log line shows "reusing analysis for <canonical> on <alias>".
- [ ] Verified: sts2-style ingest (dual-role stream) does 1 visual
      encode, not 2.

## Work log

- 2026-09-27: filed from sts2-review-v3 run; ~35min redundant encode
  + ~85 redundant chunk calls measured.
- 2026-09-27: Pass-2 dedupe implemented (canonical-twin clone). Remaining:
  media_prep per-asset re-measure/re-bake loop still processes
  duplicates - scope narrowed to media_prep + proxy generation only.
