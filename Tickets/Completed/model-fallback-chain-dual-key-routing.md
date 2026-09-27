# Model fallback chain + dual-key routing

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Reliability |
| Created | 2026-09-22 |

## Summary

Free-tier reality (2026-09-22): 3.8-flash has a 20 req/day cap,
full-size models 503 on large prompts, flash-lite survives everything.
The run only finished because a human watched .env get hot-swapped
mid-flight.

Config: ordered model list per task (e.g. GEMINI_MODEL_CHAIN) so persistent
429-quota/503-on-large-prompt falls to the next model with a warning.
Optional second key (GEMINI_FREE_KEY) routed to `model_for("fast")` calls
to keep paid quota for heavy prompts.

## Acceptance Criteria

- [ ] Simulated persistent-429 on model 1 falls through to model 2 mid-run
- [ ] Fallback logged with which pass/task switched
- [ ] Optional fast-key env wired into model_for routing
- [ ] .env.example documents the chain format

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. Config: `GEMINI_PAID_KEY_MAIN`/`GEMINI_PAID_KEY`/
  `GEMINI_FREE_KEY` fields (`repr=False`); `gemini_api_key` AliasChoices now leads
  with PAID_KEY_MAIN (legacy vars still work). `Settings.key_chain(task)` orders
  keys — visual/interpret: FREE→PAID_MAIN→PAID; heavy tasks: PAID_MAIN→PAID→FREE.
  `Settings.model_chain(task)` reads `GEMINI_MODEL_CHAIN_<TASK>` (comma-separated;
  unset → single `model_for(task)` model, unchanged). `GeminiClient._call_api`
  fails over keys-outer/models-inner: persistent 429/5xx walks the model chain on
  the same key first, 401/402/403 skip straight to the next key, 400 aborts;
  each hop logs a warning naming the task + target. Clear no-key GeminiError.
  Call sites pass task= (director/interpret/narrative/visual). `.env.example`
  documents all vars. Tests: `tests/test_gemini_client.py` (46) — persistent-429
  model fallback, dead-key rotation, fast-task free-key-first, ordering, no-key
  error, chain env parsing. Docs: cli_client.md, MASTER_MEMORIES (alias quirk).
