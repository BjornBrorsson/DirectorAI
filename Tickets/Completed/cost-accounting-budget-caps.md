# Cost accounting + budget caps

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

Dry-run shows an estimate (~$0.75) but nothing enforces it.
Track per-pass spend (Gemini tokens by model + ElevenLabs minutes),
persist to the project dir, add a hard cap config that pauses the run
for approval, and surface totals in the final report.

## Acceptance Criteria

- [x] Per-pass cost recorded in output/<project>/costs.json
- [ ] Cap breach pauses + asks before continuing — **v1 spec decision:
  abort-with-clear-message instead** (unattended runs can't answer a
  prompt); checkpoints + costs.json survive so raising the cap and
  re-running resumes where it stopped
- [x] Final log prints spend vs estimate (`cli run` prints
  `Recorded spend: $X (dry-run estimate: $Y)`; `result["costs"]` carries
  the totals dict)

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: v1 implemented. `safety/ledger.py` (`CostLedger` +
  `activate_cost_ledger`) appends every metered call to
  `output/<project>/costs.json` (atomic write-then-rename per call,
  merge-on-load across resumes and `cut patch` interpret calls). Gemini
  calls record task/model + estimated input/output tokens (len/4 text,
  `estimate_video_tokens` for media); ElevenLabs STT/music/SFX record
  billed seconds at placeholder per-second rates. Dev/test stubs record
  with `stub:true` at cost 0. `DIRECTORCUT_COST_CAP_USD` raises
  `BudgetExceeded` (a `BudgetError` subclass) both pre-flight and after
  the breaching call is recorded; `cli run` shows the message verbatim.
  ElevenLabs rates in `ELEVENLABS_USD_PER_SECOND` are order-of-magnitude
  placeholders — refine against real plan pricing. Tests:
  `tests/test_costs.py` (accumulation, cap breach ordering, stub zero-cost,
  CLI surfacing, pipeline wiring). Docs: `docs/wiki/safety_budget.md`.
