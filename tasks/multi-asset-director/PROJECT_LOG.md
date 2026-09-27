# Feature Log: multi-asset-director

> Scratchpad for the multi-asset ingest + AI director + ElevenLabs + ducking feature set.

## Scope

* Multi-file ingest with roles (a_roll/b_roll/voiceover/music/sfx/image/script/auto) via subdirs, filename prefixes, UI pickers, or AI interpretation.
* `direction.txt` / UI textarea / `--direction` flag → threaded into tagger, interpreter, and Pass 3 prompts.
* `GEMINI_BACKEND=cli` runs all structured calls through the `gemini` CLI (`-p` + `--output-format json`); `GEMINI_BACKEND=antigravity` does the same through the `agy` CLI headless mode (`-p` + `--output-format json` + `--dangerously-skip-permissions`), parsing JSON out of the agent's `response` text.
* ElevenLabs client: `/v1/sound-generation` (SFX) + `/v1/music` (music); dev mode synthesizes WAV stubs.
* Pass 3 is now a real Gemini call (`assembly/director.py`); deterministic `match_events`/`generate_cuts` remains as dev stub + fallback on hallucinated cuts.
* New timeline tracks `A2_MUSIC`, `A3_SFX` → connected `<audio>` lanes `-2`/`-3` with `volume` keyframeAnimation ducking under primary dialogue.

## Verified

* `pytest` 73 passed; `ruff check src tests` clean; `mypy src/directorcut` clean; frontend `npm run build` (tsc) clean.
* Dev-mode e2e: `directorcut run` over `smoketest_ingest/` (aroll/broll/music/images/scripts + direction.txt) produced FCPXML with lane=1 overlays (video + PNG), lane=-2 music clips (user + generated WAV) carrying -12dB duck keyframes.

## Antigravity (agy) backend notes — verified 2026-09-18

* `gemini` CLI is dead on this machine: Google retired Code Assist OAuth for free-tier individuals ("migrate to Antigravity"); upgrading 0.35.1 → 0.60.0 did NOT fix it. `agy` CLI installed to `%LOCALAPPDATA%\agy\bin` via the official `install.ps1` and worked immediately (cached creds carried over).
* `--json-schema` on `agy` is a trap: it captures whatever structured step finishes last (usually an internal `finish_task` tool call), NOT the final answer. We embed the schema in the prompt and parse JSON objects out of `response` (last validating object wins, so tool-call artifacts are skipped).
* Headless `agy` soft-denies tools it can't prompt for (`command` incl. media inspection) → `status: SUCCESS` with empty `response` + `denied_actions`. `--dangerously-skip-permissions` (setting `ANTIGRAVITY_SKIP_PERMISSIONS`, default on) is required for media calls.
* Agentic calls are slow (~3-10 min each) and token-hungry (~13k+ base context per call) vs the plain API; `gemini-3.8-flash-high` threw 503 "no capacity" once — `_call_agy` retries transient 429/503/UNAVAILABLE markers. Multi-frame media is tiled into ONE contact-sheet jpg (`_write_agy_media`) or Pass 2 exceeds the 15-min print-timeout; agents are also asked to write `result.json` since their last turn is often a tool call that leaves `response` empty.
* LIVE e2e via agy verified 2026-09-18: `GEMINI_BACKEND=antigravity` + `ANTIGRAVITY_CLI_MODEL=gemini-3.8-flash-medium` over `smoketest_ingest/` → director 9 cuts + 2 audio cues (music + SFX), FCPXML with lane=1 overlays, lane=-2/-3 audio, -12/-6dB duck keyframes at `smoketest_output/agy_live/`. ~47 min total for 5 agy calls.
* `--model` slugs already encode effort (`gemini-3.8-flash-medium`) — combining `--model ...-medium` with `--effort` is rejected as conflicting.

## Open / follow-ups

* `gemini` CLI on this machine is installed but not authenticated — `exited 1` with a `code_assist` setup error until the user auths (`gemini` interactive once). Code path is correct; error surfaces cleanly.
* `npm run lint` has no eslint config (pre-existing gap).
* ElevenLabs music endpoint is paid-tier only; SFX clamps at 22s, music at 600s.
* A-roll video is not auto-laid when a voiceover exists — deterministic fallback puts voiceover on V1 only (per TDD schema). The AI director decides properly in prod.
