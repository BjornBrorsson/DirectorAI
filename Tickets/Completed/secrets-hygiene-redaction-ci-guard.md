# Secrets hygiene: redaction + CI guard

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | Low |
| Type | Chore |
| Created | 2026-09-22 |

## Summary

.env is gitignored but logs/tests have needed manual sed-redaction
of keys during debugging, and `smoke_*.py` reads the raw key. Add a shared
`redact()` in log formatting (mask API-key-shaped strings + configured
secrets) and a CI/pre-commit check (gitleaks or a simple regex scan) so
keys can't land in repo files.

## Acceptance Criteria

- [ ] Log lines containing GEMINI_* key values print redacted
- [ ] Pre-commit or CI step fails on committed secrets
- [ ] No test depends on real .env contents (GEMINI_MODEL_FAST leak already fixed once)

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. `log.py` gained `redact()` + `register_secret(s)` —
  a `_RedactFilter` on `get_logger()` masks registered secret values AND
  key-shaped strings (`AIzaSy…`, `sk-…`) in record msg/args, so httpx errors
  carrying `?key=` URLs can't leak; the `setup_logging` formatter redacts the
  rendered line as a second layer (covers tracebacks). `Settings.configured_secrets()`
  feeds all Gemini + ElevenLabs keys; wired in `cli.py`, `api/main.py`, and both
  API clients (client-level `register_secrets` covers callers that skip
  setup_logging). Scanner: `directorcut.safety.secrets.scan_repo()` +
  `scripts/scan_secrets.py` (exit 1 on findings; masks matches; exempts
  `.env*`, `tasks/`, `output/`, venv/VCS dirs). Tests: `tests/test_secrets_hygiene.py`
  (redaction, caplog coverage, scanner exemptions, repo-scan-for-real-keys guard).
  Docs: `docs/wiki/safety_budget.md`. Full suite 154 passed, ruff clean.
