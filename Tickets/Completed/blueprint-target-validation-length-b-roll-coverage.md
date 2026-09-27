# Blueprint target validation (length / B-roll coverage)

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Quality |
| Created | 2026-09-22 |

## Summary

Attempt 1 ignored the 7-9min target (produced 4:15) and the 60%
B-roll direction (10 overlays) until an addendum spelled out hard numbers.
Don't rely on prompt wishes: configurable `target_length_s` +
`broll_coverage_min` (or parsed from direction), checked post-director;
on violation, re-prompt the director once with the measured violation,
then surface a warning in the run output either way.

## Acceptance Criteria

- [ ] Out-of-target blueprint triggers one corrective director call with the measured deltas
- [ ] Final blueprint report prints length + coverage vs target
- [ ] Defaults sensible; overridable via config/direction

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented in `assembly/targets.py` +
  `pipeline._director_with_target_check`. Length/coverage targets parse
  from direction ("HARD LENGTH TARGET: between 7:00 and 9:00", "at least
  60% ... V2 overlay") or config (`DIRECTORCUT_TARGET_LENGTH_S` accepts
  "420-540"/"7:00-9:00"/single aim +-10%, `DIRECTORCUT_BROLL_COVERAGE_MIN`
  0..1) - config wins per bound. Post-director `measure_blueprint` reports
  spine length + union-of-V2 over V1 runtime; on violation the director is
  re-prompted EXACTLY ONCE via `run_director(correction=...)` with the
  measured deltas appended, then the `[targets]` report line always logs.
  15 tests in `tests/test_targets.py` incl. the one-retry proof.
