# Feature: CLI / Local Client

> Status: Draft | Last verified: 2026-09-23

## What it does

Provides a command-line interface to run the entire DirectorCut-AI pipeline end-to-end, with per-stage subcommands, dry-run cost estimation, a `--direction` flag for free-text creative direction, and checkpointed auto-resume (`--fresh` to force a full re-run).

## Where the code lives

* `directorcut.cli` — `ingest`, `proxy`, `run` (`-p`, `-y`, `--dry-run`, `-d/--direction`, `--fresh`), `cost`, `setup`, `demo`, `web`, `cut` (`src/directorcut/cli.py`)
* `directorcut setup` — first-run wizard: hidden key prompts + backend/model choices → `%APPDATA%/DirectorCut/config.env` / `~/.config/directorcut/config.env` (never the repo .env). See [packaging.md](./packaging.md).
* `directorcut demo` — fully offline ingest→FCPXML on a synthesized `ffmpeg -f lavfi` clip under a temp/`--workdir` dir; no keys needed (`src/directorcut/demo.py`).
* `directorcut.cutops` — post-assembly patch commands (`src/directorcut/cutops.py`):
  * `directorcut cut list <project>` — every blueprint cut; V1 rows numbered 1-based in spine order (the `drop` index) with a spoken-text preview from `narrative.json`.
  * `directorcut cut drop <project> <n> [<n>...]` — drops V1 cuts, `compact_timeline` re-lays the spine gaplessly (overlays on a dropped anchor go too), writes `blueprint.json`, re-runs `finalize_project` (FCPXML + mixdown beds on the `resolve` target).
  * `directorcut cut patch <project> "<instruction>"` — natural-language blueprint edit: one Gemini `task="interpret"` call (fast-model chain) maps the instruction onto the constrained `PatchOp` schema (`drop_cut`, `move_cut`, `swap_source`, `move_overlay`, `drop_overlay`), the planned diff prints for review, then applies with snapshot+re-finalize. `--dry-run` previews without writing. Dev/test mode regex-stubs the model call (`cutops._fake_patch`) so the whole path is exercised offline.
  * `directorcut cut undo <project>` — restores `blueprint.prev.json`. Every mutation snapshots first; snapshots swap, so undo is its own undo.
  * Ported from `tasks/multi-aroll-api-run/drop_cuts.py` (verified 2026-09-22 against the StS2 EA-review timeline).
* `directorcut.pipeline.run_pipeline(fresh=)` — stage checkpointing: `output/<project>/run_state.json` stores a sha256 fingerprint of every ingest file + the effective direction; `assets.json`, `narrative.json`, `visual.json`, `blueprint.json` are written at each stage boundary; Pass 2 additionally appends per-chunk JSONL under `visual_chunks/`.
* `directorcut.export_pack.write_export_pack()` — post-finalize creator pack (`pack/captions.srt|vtt`, `chapters.txt`, `thumbs/*.jpg`); runs inside `finalize_blueprint` so `cut` re-conforms refresh it too. See [export_pack.md](./export_pack.md).

## Keys & config

* All settings from env vars, `.env`/`--config`, or the per-user `config.env` written by `directorcut setup` — precedence: process env > repo .env > user config (`GEMINI_BACKEND`, `ELEVENLABS_API_KEY`, `DIRECTORCUT_STT_BACKEND`, …).
* `GEMINI_BACKEND`: `api` (HTTP + key vars below), `cli` (legacy `gemini` CLI — retired by Google for free-tier OAuth), `antigravity` (`agy` CLI headless; auth via one-time interactive `agy` login).
* Key routing (API backend only): `GEMINI_PAID_KEY_MAIN` (primary, wins the `gemini_api_key` alias over `GEMINI_API_KEY`/`GEMINI_PAID_KEY`), `GEMINI_PAID_KEY` (secondary failover), `GEMINI_FREE_KEY` (free tier). `Settings.key_chain(task)` orders them: `visual`/`interpret` burn FREE → PAID_MAIN → PAID so high-volume classification preserves paid quota; everything else runs PAID_MAIN → PAID → FREE.
* `GEMINI_MODEL_CHAIN_<TASK>` (optional, comma-separated): per-task ordered model failover. On persistent 429-quota / repeated 5xx `GeminiClient._call_api` falls through models on the same key first (sibling models have separate quota), then rotates keys; 401/402/403 skip straight to the next key. Unset → single `model_for(task)` model, unchanged behaviour.
* `GEMINI_MODEL_FAST` (optional): cheaper model for high-volume structured classification — Pass 2 visual chunks and asset interpretation via `Settings.model_for()` (`config.py` ~L98). Unset → `GEMINI_MODEL` everywhere. Per-call `model=` is API-backend only; the CLI/agy backends use their own model flags.
* HTTP API calls send `response_json_schema` (real JSON Schema); endpoints that 400 on it fall back to a sanitized `response_schema` (`_gemini_schema` inlines `$defs`/`$ref`, strips `title`/`pattern`/`default`/`additionalProperties`). Client timeout is 600s — Pass 3 prompts are large.
* Direction can come from `--direction` or `ingest/direction.txt`.
* `DIRECTORCUT_FCPXML_TARGET=resolve|fcpx|premiere` (default `resolve`) — see `fcpxml_conformer.md` + `nle_targets.md`.
* `DIRECTORCUT_COST_CAP_USD` (optional) — hard spend cap over the `output/<project>/costs.json` ledger; breaching it aborts the run with `BudgetExceeded` (checkpoints survive, raising the cap + re-running resumes). See `safety_budget.md`.
* `DIRECTORCUT_REFRAME_AROLL` (default `true`), `DIRECTORCUT_NORMALIZE_VOICE` (default `false`) — ingest-time A-roll media prep; see `ingestion_proxy.md`.
* `DIRECTORCUT_TARGET_LENGTH_S` (range "420-540"/"7:00-9:00" or single aim), `DIRECTORCUT_BROLL_COVERAGE_MIN` (0..1) — post-director hard targets, override direction parsing; see `pass3_assembly.md`.

## Gotchas

* Keep CLI thin; business logic belongs in stage modules.
* **Auto-resume is the default** (added 2026-09-22): a `run` whose ingest fingerprint matches `run_state.json` replays `assets.json`/`narrative.json`/`visual.json`/`blueprint.json` instead of redoing ingest, STT, tagging, visual analysis, and the director call. Changed ingest files or direction invalidate the stage JSONs (derived media is per-asset: only changed sources get re-proxied). `--fresh` wipes every derived artifact including the STT cache. Artifacts from pre-checkpoint runs (no `run_state.json`) are treated as untrusted — proxies are kept (exist-skip) but stage JSONs are dropped.
* The `run` cost estimate sums durations across *all* video assets, not just the primary.
* **agy prompts travel as `prompt.txt` in the workspace**, not inline `-p` args — Windows `CreateProcess` caps command lines at ~32K chars and the director prompt (100+ narrative nodes) exceeds it (WinError 206, seen 2026-09-18). The `-p` arg is a short "read prompt.txt and follow it" wrapper; the agent reads workspace files itself.
