# DirectorCut-AI — Documentation Index

> Use this to open the right folder directly instead of searching the whole repo.

## Folder Map

| Location | Purpose |
|---|---|
| `./src/` | Production Python source code. |
| `./scripts/` | Seed, run, and test helpers (FFmpeg checks, sample runs). |
| `./tasks/` | Per-feature scratchpads + ticket board. |
| `./docs/` | This index, master memories, schemas, and feature wiki. |
| `./docs/schemas/` | JSON schemas for runtime data contracts. |
| `./ingest/` | Runtime input asset folder (created on first run, gitignored). |

## Feature Wiki

* [System overview](./wiki/system_overview.md)
* [Ingestion & proxy engine](./wiki/ingestion_proxy.md)
* [Pass 1: Narrative mapping engine](./wiki/pass1_narrative.md)
* [Pass 2: Visual structural logger](./wiki/pass2_visual.md)
* [Pass 3: Assembly director](./wiki/pass3_assembly.md)
* [FCPXML conformer](./wiki/fcpxml_conformer.md)
* [NLE target matrix](./wiki/nle_targets.md) — Resolve/FCPX/Premiere/CapCut compat
* [Resolve native conform](./wiki/resolve_native.md) — DaVinciResolveScript spike: build timelines via the scripting API instead of FCPXML import
* [Creator export pack](./wiki/export_pack.md) — captions, chapters, thumbnails under `pack/`
* [Whisper STT](./wiki/stt_whisper.md) — local faster-whisper fallback for ElevenLabs
* [Packaging & onboarding](./wiki/packaging.md) — PyInstaller binary, `directorcut setup`/`demo`, user config dir
* [Audio design & LLM backends](./wiki/audio_design.md)
* [CLI / local client](./wiki/cli_client.md)
* [Safety & budget controls](./wiki/safety_budget.md)
* [Web frontend](./wiki/web_frontend.md)

## Master References

* [Master memories](./MASTER_MEMORIES.md) — reusable quirks and gotchas.
* [Agentic rules](./agentic_rules_blueprint.md) — runtime rules for agents.
* [Tech design doc](../DirectorCut-AI-TechDoc.md) — high-level architecture and KPIs.
* [Roadmap](./ROADMAP.md) — phased implementation plan.
* [Ticket board](../tasks/BOARD.md) — current work and priorities.
