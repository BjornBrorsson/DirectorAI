# Feature: Safety & Budget Controls

> Status: Draft | Last verified: 2026-09-23

## What it does

Monitors token thresholds, estimates API cost, enforces structured output schemas on every Gemini call (API and CLI backends alike), records per-run spend into `costs.json` with a hard USD cap, ensures dev mode never sends live requests — including stubbing ElevenLabs audio generation with synthesized WAVs — and keeps configured credentials out of logs and the repo.

## Where the code lives

* `directorcut.safety.token_guard.check_token_budget()` — per-chunk ceiling assert
* `directorcut.safety.cost.estimate_cost()` — duration-driven cost estimate (summed over all video assets at the CLI layer)
* `directorcut.safety.ledger.CostLedger` / `activate_cost_ledger()` (`src/directorcut/safety/ledger.py`) — the costs.json ledger behind `DIRECTORCUT_COST_CAP_USD`
* `directorcut.exceptions.BudgetExceeded` — raised on cap breach; subclasses `BudgetError` so existing handlers keep catching it
* `directorcut.gemini_client.GeminiClient.call()` — dev-mode stub + schema validation for both backends; meters every call into the active ledger (`_record_call`, incl. CLI/agy backends and dev stubs at cost 0)
* `directorcut.elevenlabs_client.ElevenLabsClient` — meters STT (audio seconds via `_probe_duration`), music and SFX renders into the ledger
* `directorcut.log.redact()` / `register_secret(s)` — logger filter masks registered secret values *and* key-shaped strings (`AIzaSy…`, `sk-…`) in every record's msg/args, so `httpx` errors carrying `?key=` URLs can't leak. `Settings.configured_secrets()` feeds it; wired in `cli.py`, `api/main.py`, and both API clients.
* `directorcut.safety.secrets.scan_repo()` + `scripts/scan_secrets.py` — regex scan for committed-looking secrets (Google/OpenAI/GitHub key shapes, private-key blocks); exits 1 on findings for CI/pre-commit. `.env*`, `tasks/`, `output/`, venv/VCS dirs are exempt; findings print masked matches only.

## Cost accounting (costs.json + DIRECTORCUT_COST_CAP_USD)

`run_pipeline` wraps the whole run in `activate_cost_ledger(output/<project>)`; `directorcut cut patch` opens the same ledger for its interpret call, so post-run edits accrue onto the project's spend. Every metered call appends one entry to `output/<project>/costs.json` and flushes it atomically (write-then-rename) — a crash loses at most the in-flight call, and a resume *accumulates* into the same file rather than starting over.

* **Gemini entries** — `{kind: "gemini", task, model, input_tokens, output_tokens, cost_usd}`. Tokens are tiktoken-free estimates: `len/4` for text, `token_budget.estimate_video_tokens` for media frames; priced at `safety.cost.PRICE_INPUT_PER_1M`/`PRICE_OUTPUT_PER_1M`.
* **ElevenLabs entries** — `{kind: "elevenlabs_stt|music|sfx", seconds, cost_usd}` with rough per-second rates in `ELEVENLABS_USD_PER_SECOND` (order-of-magnitude placeholders; real pricing is plan-dependent).
* **Dev/test stubs** are recorded too, with `"stub": true` and `cost_usd: 0` — a dev run's costs.json reads as "what this would have spent" and can never breach the cap.
* **Cap enforcement** is twice per call: a ledger already over cap aborts BEFORE the next paid call, and the call that pushes spend over is still recorded, then `BudgetExceeded` raises. `cli run` surfaces the message verbatim (checkpoints stay intact — raise the cap and re-run to resume). v1 is abort-only by spec; no mid-run approval prompt.
* **Final report** — `cli run` prints `Recorded spend: $X (dry-run estimate: $Y)`; `result["costs"]` carries the totals dict (surfaced as `cost_usd` on the API's run-done event).

## Keys & config

* `GEMINI_API_KEY` — required for live API calls (not needed for `GEMINI_BACKEND=cli`, which uses CLI auth).
* `ELEVENLABS_API_KEY` — required for live audio generation; synthesized stubs otherwise.
* `DIRECTORCUT_ENV` — `dev`/`test` disables all outbound calls.

## Gotchas

* Always leave a safety margin below the 1M token context ceiling (`SAFE_INPUT_TOKENS` = 0.95×).
* The `cli run` cost check sums durations of every video asset — multi-asset dumps cost proportionally more.
* `chunk_proxy` must size `frames_per_chunk` with the *untruncated* `TOKENS_PER_360P_FRAME * VIDEO_TOKEN_OVERHEAD` product — truncating to `int()` first let a max-capacity chunk estimate ~950,568 > 950,000 and die on ~1h42m files (verified 2026-09-18).
* `agy` replies that fail schema validation are retried with `_AGY_CORRECTIVE_HINT` (`gemini_client._call_agy`) — a single malformed node shouldn't kill a run that already cost tens of minutes. Transient 429/503 markers use the same retry loop.
