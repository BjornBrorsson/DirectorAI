# Fix IngestManifest JSON round-trip

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Bug |
| Created | 2026-09-22 |

## Summary

`IngestManifest.model_validate_json` fails on the pipeline's own
`manifest.model_dump_json()` output: the `before`-validator coerces str→Path,
then strict JSON-mode Path validation rejects the Path objects. Worked
around in `tasks/multi-aroll-api-run/rerun_pass3.py` with
`model_validate(json.loads(...))` — the durable fix belongs in
`models.py` (coerce only str/Path, or validator mode).

## Acceptance Criteria

- [ ] `IngestManifest.model_validate_json(m.model_dump_json())` round-trips
- [ ] Same for NarrativeNode/VisualEvent/Cut Path fields if affected
- [ ] Regression test in tests/

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: verified already applied — `models.py::_coerce_path` (and the
  `Settings._coerce_path` mirror in config.py) keep `str`/`Path` values for
  pydantic's own coercion, so `model_validate_json(m.model_dump_json())`
  round-trips. Confirmed green in the full suite (154 passed) including the
  resume tests that reload `assets.json`/`narrative.json`/`visual.json`/
  `blueprint.json` through the same validators. No code change needed.
