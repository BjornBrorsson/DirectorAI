# Productize blueprint patch commands (drop/move cuts)

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Tooling |
| Created | 2026-09-22 |

## Summary

`tasks/multi-aroll-api-run/drop_cuts.py` drops V1 cuts by index,
re-compacts via `compact_timeline`, rebuilds FCPXML + beds. That's the
start of a post-assemble patch CLI every user will want after reviewing
a rough cut.

Promote to `directorcut cut drop <project> <n>...` plus a couple of
neighbours (`list`, `undo` via blueprint snapshots). Beds + FCPXML must
re-render after each patch (drop_cuts already chains render_audio +
finalize).

## Acceptance Criteria

- [ ] CLI subcommand works on any project dir with blueprint.json
- [ ] Drops produce a compact, gapless spine + re-rendered beds
- [ ] Undo restores the previous blueprint snapshot
- [ ] docs/wiki/cli_client.md updated

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented in `cutops.py` + `cli.py`. `directorcut cut
  list|drop|undo <project>` operate on `output/<project>/blueprint.json`
  (port of `tasks/multi-aroll-api-run/drop_cuts.py`): 1-based V1 spine
  indices, snapshot to `blueprint.prev.json` before mutation,
  `compact_timeline` gapless re-lay, then `finalize_project` re-renders
  FCPXML + Resolve mixdown beds. Undo swaps the snapshot so it toggles.
  `docs/wiki/cli_client.md` updated. Tests in `tests/test_cutops.py`
  (drop/undo/compact + CLI via CliRunner).
