# Backend fallback: API key exhaustion -> Antigravity CLI

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Medium |
| Type | Reliability |
| Created | 2026-09-23 |

## Summary

Per the key-routing ticket, API calls fail over across keys x models.
Add one more rung: when the ENTIRE api key chain is exhausted for a call
(persistent 429/503/402 across all configured keys + model chain), fall
back to the `antigravity` (agy CLI) backend for that call — the agy CLI
carries its own auth and survived the free-tier storms.

Config: `DIRECTORCUT_BACKEND_FALLBACK=antigravity` (empty = disabled).
Only meaningful when `GEMINI_BACKEND=api`. Non-api backends never
fallback (agy->api would require keys that were just proven dead).

## Acceptance Criteria

- [x] `GeminiClient.call` on api backend: after all keys/models fail, one
  attempt via `_call_agy` with the same prompt/response_model; warning
  logged once per call-site.
- [x] Per-call model override `model=` is ignored on the agy fallback
  (agy uses ANTIGRAVITY_CLI_MODEL) — debug-logged.
- [x] Tests: respx-failing API + stubbed agy subprocess -> call succeeds
  via agy; fallback disabled by default (existing tests unchanged).
- [x] `.env.example` + cli_client.md document the last-resort rung.

## Work Log

- 2026-09-23: created — user policy "LAST fallback: Antigravity CLI" for
  the overnight StS2 run while paid-key billing is being set up.
- 2026-09-23: implemented. `_ChainExhaustedError` marks total plan
  exhaustion in `gemini_client._call_api`; `call()` catches it and routes
  through `_call_agy` when `DIRECTORCUT_BACKEND_FALLBACK=antigravity`.
  Request bugs (400 aborts) and non-configured fallback skip it. Tests:
  exhaustion->agy, disabled-by-default, request-bug-skip.
