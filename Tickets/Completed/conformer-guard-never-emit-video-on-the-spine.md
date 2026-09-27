# Conformer guard: never emit <video> on the spine

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Bug / Guardrail |
| Created | 2026-09-22 |

## Summary

Resolve renders spine `<video>` elements as frozen stills and
misplaces them (verified 2026-09-22, also recorded in
`test_primary_clip_is_asset_clip_with_probed_asset` and MASTER_MEMORIES).
`<video>` is only safe as a connected clip. Add a conform-time assertion:
any visual source on V1_A1_PRIMARY must emit `<asset-clip>`; `<video>` may
only appear nested with a lane. Fail loudly rather than produce a broken
timeline.

## Acceptance Criteria

- [ ] build_fcpxml raises ConformError if a spine visual would become <video>
- [ ] Unit test covering the assertion
- [ ] MASTER_MEMORIES cross-linked

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. `conformer/fcpxml.py::_spine_clip_tag` never emits
  `<video>` on the spine (audio-only -> `<audio>`, everything else ->
  `<asset-clip>`); `_validate_spine()` walks the built spine and raises
  `ConformError` on any top-level `<video>` (nested/lane-carrying connected
  `<video>` stays legal). Unit test in `tests/test_conformer.py`;
  MASTER_MEMORIES entry cross-linked (the 2026-09-22 spine-`<video>`/
  `<param>` note). Verified: `pytest -q` green.
