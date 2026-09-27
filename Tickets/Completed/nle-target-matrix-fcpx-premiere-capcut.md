# NLE target matrix (FCPX / Premiere / CapCut)

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature / Spec needed |
| Created | 2026-09-22 |

## Summary

FCPXML semantics are FCP-native; we've only ever verified Resolve.
For a sellable tool, define target matrix: DaVinci Resolve (current),
Final Cut Pro (native FCPXML - transforms + param volume may actually
work there), Premiere XML/AAF, CapCut. Per-target quirks doc + conform
options (ducking baked vs keyframed is already target-dependent).

## Acceptance Criteria

- [ ] One real import test per NLE documented — **partially met**: Resolve
  verified (StS2 EA-review, 2026-09-18..22); Premiere/FCPX/CapCut still
  lack real imports — `docs/wiki/nle_targets.md` records the status +
  Premiere TODOs to check on first import
- [x] Conform target flag routes track/ducking/transform choices —
  `DIRECTORCUT_FCPXML_TARGET` accepts `resolve|fcpx|premiere`;
  `premiere` shares the `fcpx` document shape for v1
  (`emit_ducking = target in ("fcpx", "premiere")`)
- [x] docs/wiki/fcpxml_conformer.md gains a compatibility table — the
  matrix lives in `docs/wiki/nle_targets.md` (linked from
  fcpxml_conformer.md + DOCUMENTATION_INDEX.md); covers baked beds vs
  keyframes, transform policy, spine element policy per target

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: `premiere` target added to `Settings.fcpxml_target` +
  `build_fcpxml(target=)`; same XML as fcpx since Premiere imports
  FCPXML — its `<param>`/connected-clip handling is unverified and listed
  as TODOs in nle_targets.md. CapCut documented as "no FCPXML import
  path" (future: OTIO/draft generator). Tests:
  `tests/test_conformer.py::test_premiere_target_shares_fcpx_ducking_shape`
  + `test_premiere_is_an_accepted_config_target`.
