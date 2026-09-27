# Per-source A-roll usage report + under-used-source nudge

| Field | Value |
|---|---|
| Status | Ready |
| Assignee | unassigned |
| Priority | Low |
| Type | Quality |
| Created | 2026-09-27 |

## Summary

sts2-review-v3: the 118-min stream contributed 1,730 of 2,102 narrative
nodes (82%) yet ZERO of 45 V1 cuts - the director preferred the tight
scripted takes for the whole spine. That may be correct take-selection
("use only good takes") or prompt-salience failure - today there's no
way to tell because source mix isn't surfaced anywhere.

## Acceptance criteria

- [ ] Run report prints per-source V1/V2 cut counts + seconds (already
      computable from blueprint.json).
- [ ] If an A-roll source gets 0 V1 cuts while holding >30% of nodes,
      log a warning and optionally feed a corrective note into the
      target-validation re-prompt ("consider whether <source> has better
      takes for beats X/Y").
- [ ] No forced usage - good-take selection stays primary; the signal
      is visibility, not quotas.

## Work log

- 2026-09-27: filed after reviewing v3 blueprint source mix.
