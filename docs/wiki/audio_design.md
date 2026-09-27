# Feature: Audio Design (ElevenLabs) & LLM Backends

> Status: Draft | Last verified: 2026-09-18

## What it does

Two integrations:

1. **ElevenLabs** — the Pass 3 director emits `audio_cues` (`{cue_id, kind, prompt, timeline_in, timeline_out}`); `audio_design.render_cues()` calls ElevenLabs and appends `A2_MUSIC`/`A3_SFX` cuts to the blueprint.
2. **CLI LLM backends** — `GEMINI_BACKEND=cli` routes structured calls through the legacy `gemini` CLI; `GEMINI_BACKEND=antigravity` routes them through the `agy` CLI headless mode (`-p` + `--output-format json`). Both use whatever auth the CLI is configured with instead of `GEMINI_API_KEY`.

## Where the code lives

* `directorcut.elevenlabs_client.ElevenLabsClient` — `generate_sfx` (`POST /v1/sound-generation`) + `generate_music` (`POST /v1/music`) (`src/directorcut/elevenlabs_client.py`)
* `directorcut.audio_design.render_cues()` — cue → file → `Cut` (`src/directorcut/audio_design.py`)
* `directorcut.conformer.mixdown.render_mixdown()` — Resolve-target bed bounce: timeline-placed `voice_guide.wav` + `sidechaincompress`-ducked `music_bed_ducked.wav` + flat `sfx_bed.wav` under `output/<project>/` (`src/directorcut/conformer/mixdown.py`, added 2026-09-22 — see `fcpxml_conformer.md`)
* `directorcut.gemini_client.GeminiClient._call_cli()` — gemini CLI backend (`src/directorcut/gemini_client.py` ~L220)
* `directorcut.gemini_client.GeminiClient._call_agy()` / `_parse_agy_response()` — agy backend (`src/directorcut/gemini_client.py` ~L300)

## Keys & config

* `ELEVENLABS_API_KEY`, `ELEVENLABS_MUSIC_MODEL` (default `music_v2`)
* `GEMINI_BACKEND=api|cli|antigravity`, `GEMINI_CLI_BIN`, `GEMINI_CLI_MODEL`
* `ANTIGRAVITY_CLI_BIN`, `ANTIGRAVITY_CLI_MODEL`, `ANTIGRAVITY_PRINT_TIMEOUT` (default `15m`), `ANTIGRAVITY_SKIP_PERMISSIONS` (default `true`)

## Gotchas

* SFX API caps `duration_seconds` at 22; music `music_length_ms` is 3,000–600,000 — both clamped client-side.
* ElevenLabs Music requires a paid tier. Without a key (or in dev/test) the client synthesizes a sine WAV via stdlib `wave` so the pipeline still runs.
* `gemini` CLI stdout can contain log lines before the JSON envelope — parse with `raw_decode` from the first `{`, then read `.response` (may be ```` ```json ```` fenced).
* `gemini` CLI free-tier OAuth is retired (IneligibleTierError → "migrate to Antigravity") — use the `antigravity` backend or a `GEMINI_API_KEY`.
* `agy` is agentic, not a raw model call: `--json-schema` captures tool-call args instead of the answer (schema goes in the prompt instead); media files must sit under the subprocess `cwd` so its workspace reads are auto-allowed; tool permission denials leave `status: SUCCESS` with an empty `response` + `denied_actions` — hence `--dangerously-skip-permissions` by default. See `docs/MASTER_MEMORIES.md`.
