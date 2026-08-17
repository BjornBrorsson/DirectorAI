# DirectorCut-AI — Implementation Roadmap

> Derived from `DirectorCut-AI-TechDoc.md`. The board of record lives in [`tasks/BOARD.md`](../tasks/BOARD.md).

---

## Guiding KPIs

1. **Context Optimization**: Fit ~2 hours of source assets into Gemini's 1,000,000 token context.
2. **Economic Viability**: Cost less than ~$1.50 in API usage per final rough cut.
3. **Structural Fidelity**: Produce a DaVinci Resolve timeline that only needs pacing/audio polish.

---

## Phase 0 — Workspace Bootstrap

*Goal: Set up the self-improving workspace, schemas, and rules so future agents can orient quickly.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [WBS-000](../tasks/WBS-000.md) | Agentic workspace skeleton | Rules, docs index, master memories, wiki stub, board created. |
| [WBS-001](../tasks/WBS-001.md) | JSON schemas & data contracts | `narrative_node`, `visual_event`, and `assembly_blueprint` JSON schemas with validation harness. |

---

## Phase 1 — Foundation

*Goal: A runnable Python project with FFmpeg discovery, config, logging, and safe API handling.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [FND-002](../tasks/FND-002.md) | Python project skeleton | `pyproject.toml`, pytest, ruff/black, `.env.example`, README quickstart. |
| [FND-003](../tasks/FND-003.md) | FFmpeg discovery & validation | Cross-platform FFmpeg/FFprobe locator, version check, health command. |
| [FND-004](../tasks/FND-004.md) | Configuration & secrets | Pydantic settings, `.env` loading, API key placeholder, dev/test/prod modes. |
| [FND-005](../tasks/FND-005.md) | Logging & error scaffolding | Structured logging, try/catch wrappers, temp-file cleanup helpers. |

---

## Phase 2 — Ingestion & Proxy Engine

*Goal: Convert raw 4K/60 assets into API-ready audio and 1-FPS visual proxies while monitoring token budget.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [PRX-006](../tasks/PRX-006.md) | Ingestion engine | Scan `/ingest`, validate required files, report missing inputs, create run manifest. |
| [PRX-007](../tasks/PRX-007.md) | Audio extraction proxy | Extract mp3/aac audio from source; preserve timing; verify output. |
| [PRX-008](../tasks/PRX-008.md) | Visual downsample proxy | 360p, 1-FPS visual proxy with constant keyframes; token target verification. |
| [PRX-009](../tasks/PRX-009.md) | Token budget estimator & chunker | Estimate tokens from proxy specs; split into chunks if >1M tokens. |

---

## Phase 3 — Pass 1: Narrative Mapping Engine

*Goal: Map script/voiceover to timestamped narrative nodes tagged with intent.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [NAR-010](../tasks/NAR-010.md) | Script & voiceover ingestion | Read `.txt` script and audio; prepare transcript input. |
| [NAR-011](../tasks/NAR-011.md) | Transcript-to-timestamp mapping | Generate spoken-line array with accurate `time_start` / `time_end`. |
| [NAR-012](../tasks/NAR-012.md) | Thematic tagger | Tag nodes: `EXPOSITION`, `COMEDIC_SET`, `EMOTIONAL_SPIKE`, `CALLBACK_REQ`. |
| [NAR-013](../tasks/NAR-013.md) | Narrative Node JSON validation | Output matches `narrative_node.schema.json`; unit tests pass. |

---

## Phase 4 — Pass 2: Visual Structural Logger

*Goal: Identify gameplay state events from the low-res visual proxy.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [VIS-014](../tasks/VIS-014.md) | Visual proxy ingestion | Read 1-FPS frame sequence or video into a generator. |
| [VIS-015](../tasks/VIS-015.md) | Visual state classification prompt | Gemini prompt that returns `STATE_UI`, `STATE_ACTION`, `STATE_DEATH`, `STATE_TUTORIAL`. |
| [VIS-016](../tasks/VIS-016.md) | Visual State Event validation | Output matches `visual_event.schema.json`; integration test on sample frames. |
| [VIS-017](../tasks/VIS-017.md) | Chunked analysis for long assets | Split >1M token proxies; merge event logs preserving timeline order. |

---

## Phase 5 — Pass 3: Assembly Director

*Goal: Cross-reference narrative and visual data into a cut-list blueprint.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [ASM-018](../tasks/ASM-018.md) | Cross-reference logic | Match `CALLBACK_REQ` to nearest preceding/following visual event, handle comedic irony and spikes. |
| [ASM-019](../tasks/ASM-019.md) | Cut-list generation rules | Determine J-cuts, L-cuts, B-roll inserts, and primary track selections. |
| [ASM-020](../tasks/ASM-020.md) | Assembly Blueprint validation | Output matches `assembly_blueprint.schema.json`; sample blueprint round-trips. |

---

## Phase 6 — FCPXML Conformer

*Goal: Convert the blueprint into a DaVinci Resolve-compatible FCPXML 1.8 timeline.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [FCP-021](../tasks/FCP-021.md) | FCPXML 1.8 template builder | Generate valid `fcpxml` with resources, library, event, and sequence. |
| [FCP-022](../tasks/FCP-022.md) | Track mapping | Map `V1_A1_PRIMARY` and `V2_OVERLAY_BROLL` to correct video/audio tracks. |
| [FCP-023](../tasks/FCP-023.md) | Timecode & frame-duration math | Convert `HH:MM:SS.mmm` to FCPXML rational time for target fps. |
| [FCP-024](../tasks/FCP-024.md) | Resolve import smoke test | Generated XML imports cleanly into DaVinci Resolve with no offline clips. |

---

## Phase 7 — CLI & Local Client

*Goal: A single command that runs the entire pipeline end-to-end.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [CLI-025](../tasks/CLI-025.md) | argparse CLI | Subcommands: `ingest`, `proxy`, `analyze`, `assemble`, `conform`, `run` (all). |
| [CLI-026](../tasks/CLI-026.md) | End-to-end happy path | One command produces a Resolve-ready timeline from a sample project. |
| [CLI-027](../tasks/CLI-027.md) | Progress reporting & dry-run | Human-readable progress, `--dry-run` mode, cost preview. |

---

## Phase 8 — Safety, Budget, & Polish

*Goal: Guard token/cost limits, enforce schemas, and ship a stable v0.1.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [SFT-028](../tasks/SFT-028.md) | Token threshold monitor | Pre-flight check refuses oversized payloads; auto-chunker engaged. |
| [SFT-029](../tasks/SFT-029.md) | Cost estimator | Per-run estimate based on token counts and model pricing. |
| [SFT-030](../tasks/SFT-030.md) | Structured output enforcement | Every Gemini call uses a strict JSON schema; parse failures logged. |
| [POL-031](../tasks/POL-031.md) | Docs, tests, lint, README | Full test suite green, lint clean, README with install/run examples. |

---

## Phase 9 — Web Frontend

*Goal: A browser-based UI for project upload, pipeline monitoring, cut review, and FCPXML download.*

| Ticket | Deliverable | Definition of Done |
|---|---|---|
| [WEB-032](../tasks/WEB-032.md) | Frontend stack & scaffold | Framework chosen, project scaffolded, dev server runs, lint passes. |
| [WEB-033](../tasks/WEB-033.md) | Backend API service | FastAPI wrapper around pipeline; HTTP + WebSocket/SSE endpoints. |
| [WEB-034](../tasks/WEB-034.md) | Project upload / ingest UI | Drag-and-drop ingest, validation feedback, project list view. |
| [WEB-035](../tasks/WEB-035.md) | Pipeline progress dashboard | Live/refreshing status per stage, logs, error display. |
| [WEB-036](../tasks/WEB-036.md) | Timeline / cut review UI | Display narrative nodes, visual events, and generated cuts; allow basic approve/reject. |
| [WEB-037](../tasks/WEB-037.md) | Results download | Download FCPXML + intermediate JSONs + run report. |
| [WEB-038](../tasks/WEB-038.md) | Settings UI | API key, model, FFmpeg path, output dir, dev/test mode toggles. |

---

## Milestones

| Milestone | Tickets | Target Outcome |
|---|---|---|
| **M0: Workspace Ready** | WBS-000, WBS-001 | Any agent can open docs and understand structure. |
| **M1: Local Media Pipeline** | FND-002 → PRX-009 | Raw assets → validated proxies with token budget known. |
| **M2: Cloud Analysis Working** | NAR-010 → VIS-017 | Narrative + visual JSONs generated for sample footage. |
| **M3: Cut List & Timeline** | ASM-018 → FCP-024 | FCPXML produced and imports into Resolve. |
| **M4: Shippable v0.1** | CLI-025 → POL-031 | One-command rough-cut generator with docs and tests. |
| **M5: Web UI** | WEB-032 → WEB-038 | Browser-based project dashboard, upload, progress, review, and download. |

---

## Next Step

Review and approve this roadmap. The first open tickets are `WBS-001` (JSON schemas) and `FND-002` (project skeleton). If you want the frontend first, we can also spike `WEB-032` early in parallel once the core API contract is clear.
