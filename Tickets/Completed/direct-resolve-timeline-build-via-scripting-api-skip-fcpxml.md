# Direct Resolve timeline build via scripting API (skip FCPXML)

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Feature / Spec needed |
| Created | 2026-09-22 |

## Summary

Half the bugs this session were Resolve's FCPXML importer
reinterpreting things (tcStart, connected-clip offsets, <video> spine,
param volume, adjust-transform on rotated media). DaVinci Resolve has a
Lua/Python scripting API that can create timelines, place clips, set
transforms and audio levels natively — removing the interpretation layer
entirely.

Spec needed: prototype scope (resolve installed locally?), script to
recreate a blueprint timeline + baked beds, comparison vs FCPXML path,
fallback story for users without Resolve.

## Acceptance Criteria

- [ ] Feasibility spike documented (API coverage for clips/lanes/transforms/levels)
- [ ] Prototype builds the sts2 timeline natively and matches the verified-good v4 layout
- [ ] Decision record: FCPXML vs native per target NLE

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: feasibility spike + prototype generator landed. API research
  (official README + community docs + davinci-resolve-mcp measured notes)
  documented in `docs/wiki/resolve_native.md` incl. decision record. Key
  findings: Resolve must be RUNNING; external `scriptapp("Resolve")` is
  Studio-only (free Resolve must launch from Workspace ▸ Scripts — fragile
  on free 21.1 which moved .py scripting to Studio); `AppendToTimeline`
  clipInfo covers source in/out + `trackIndex` + `recordFrame` (exact
  placement, no importer reinterpretation); per-clip audio volume is only
  scriptable on Resolve >= 21.1 → baked beds stay the ducking strategy.
  `src/directorcut/conform_resolve.py::emit_resolve_script` writes a
  standalone `build_timeline.py` (Media Pool import → CreateEmptyTimeline →
  per-cut clipInfo placement, beds-as-single-clip mirroring the FCPXML
  resolve target). `directorcut conform <project>` CLI added.
  Unit-tested generated source only (8 tests in tests/test_conform_resolve.py)
  — **real Resolve execution NOT done tonight**; first live run must verify
  startFrame/endFrame frame base, audio `trackIndex` semantics, and the
  free-edition launch path on the installed build.
