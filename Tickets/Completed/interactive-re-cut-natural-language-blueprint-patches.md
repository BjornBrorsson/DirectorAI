# Interactive re-cut: natural-language blueprint patches

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature / Spec needed |
| Created | 2026-09-22 |

## Summary

drop_cuts.py is the mechanical version. The sellable version:
"drop cut 54", "use the secondary take for the Defect bit", "move the
trailer overlay to the verdict" — small LLM call that edits
blueprint.json through `compact_timeline` + constrained patch ops
(drop/reorder/swap-source/move-overlay), then reconform + re-render beds.
Each patch produces an undoable snapshot.

## Acceptance Criteria

- [x] Patch op schema (4-6 ops) defined + validated — `cutops.PatchOp` /
  `PatchPlan` (pydantic): `drop_cut`, `move_cut`, `swap_source`,
  `move_overlay`, `drop_overlay`; indices/source files/timecodes validated
  against the CURRENT blueprint + assets.json inventory at apply time
- [x] NL -> ops via one small model call; dry-run shows diff before apply
  — `directorcut cut patch <project> "<instruction>"` runs one
  `task="interpret"` Gemini call (fast-model chain), prints the planned
  op list + resulting spine, applies only without `--dry-run`
- [x] Undo stack across a session — every apply snapshots
  blueprint.json -> blueprint.prev.json first; existing swap-semantics
  `cut undo` covers patches (toggle between last two states; a deeper
  multi-level undo stack is future work)

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: v1 implemented. `cutops.interpret_patch` builds a prompt
  from `cut list` rows + the assets.json inventory; ops compose
  sequentially against the evolving cut list (later indices see post-op
  numbering). `move_cut` re-lays the spine gaplessly via `_relayout` —
  the permutation sibling of `compact_timeline` — so overlays anchored to
  a moved cut ride along. The interpret call is metered into the
  project's costs.json ledger. Dev/test stub `_fake_patch` regex-parses
  "drop cut N" / "move cut N to M". Tests: `tests/test_patch.py` (schema
  validation, op semantics incl. overlay re-anchor, preview/apply/undo,
  fake-client interpret, CLI dry-run + error surfacing).
