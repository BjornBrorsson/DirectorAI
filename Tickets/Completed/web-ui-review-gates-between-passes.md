# Web UI review gates between passes

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Feature / Spec needed |
| Created | 2026-09-22 |

## Summary

The FastAPI + frontend exist (browse, projects, WS progress).
For paying users the trust problem is 'run finished, cut is wrong'.
Add per-stage review: after Pass 1 show/edit the transcript+tags, after
Pass 2 the visual log, after Pass 3 a timeline preview (blueprint
listing like v1_listing.txt) — approve/edit/rerun-that-pass before
continuing. Resume/checkpointing ticket is a prerequisite.

## Acceptance Criteria

- [ ] Pause points configurable (auto | gate at each pass)
- [ ] UI shows narrative/visual/blueprint and accepts edits persisted to the JSONs
- [ ] Reject+rerun a single pass without redoing earlier ones

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: v1 gates implemented. `run_pipeline` gained a `gate(stage)`
  hook (`pipeline.py::_run_gate`) fired after each pass checkpoint JSON
  lands; the API (`api/main.py::_make_gate`) blocks the pipeline thread on a
  per-gate `threading.Event`, emits `awaiting_gate` on the event log/WS, and
  the pipeline reloads the artifact after release so POSTed edits take
  effect. `POST /projects/{name}/run` accepts `gate_mode`
  (auto|pass1|pass2|pass3|all) + `fresh`; `GET /projects/{name}/stage/{pass}`
  returns the checkpoint artifact (+ `cut list`-style listing for pass3);
  `POST .../stage/{pass}/resume` applies optional schema-validated JSON edits
  (replaces the artifact file) then releases the gate. Frontend: UploadForm
  gate selector → `?gate=` on the progress route → WS run message;
  `GatePanel` in Progress.tsx = banner + JSON textarea + approve / apply-edits
  buttons. Tests: tests/test_api_gates.py (4, real pipeline + stubbed stages).
  Not in v1: reject+rerun a single pass, ingest-stage gate, structured
  editing UI — see docs/wiki/web_frontend.md.
