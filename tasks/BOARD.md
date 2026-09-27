# DirectorCut-AI — Ticket Board

> Markdown-file Kanban. Each ticket is a file in `./tasks/`; this board aggregates status. Update a ticket's status in its own file, then refresh the summary here.

## Quick Stats

* **Total:** 40
* **Open:** 1
* **In Progress:** 0
* **Blocked:** 0
* **Done:** 39

---

## Kanban

### Backlog / Open

| ID | Title | Phase | Priority |
|---|---|---|---|
| [FCP-024](./FCP-024.md) | Resolve import smoke test | Conformer | Medium |

### In Progress

*None.*

### Blocked

*None.*

### Done

| ID | Title | Phase | Priority |
|---|---|---|---|
| [WBS-000](./WBS-000.md) | Agentic workspace skeleton | Bootstrap | High |
| [WBS-001](./WBS-001.md) | JSON schemas & data contracts | Bootstrap | High |
| [FND-002](./FND-002.md) | Python project skeleton | Foundation | High |
| [FND-003](./FND-003.md) | FFmpeg discovery & validation | Foundation | High |
| [FND-004](./FND-004.md) | Configuration & secrets | Foundation | High |
| [FND-005](./FND-005.md) | Logging & error scaffolding | Foundation | Medium |
| [PRX-006](./PRX-006.md) | Ingestion engine | Proxy Engine | High |
| [PRX-007](./PRX-007.md) | Audio extraction proxy | Proxy Engine | High |
| [PRX-008](./PRX-008.md) | Visual downsample proxy | Proxy Engine | High |
| [PRX-009](./PRX-009.md) | Token budget estimator & chunker | Proxy Engine | High |
| [NAR-010](./NAR-010.md) | Script & voiceover ingestion | Pass 1 | High |
| [NAR-011](./NAR-011.md) | Transcript-to-timestamp mapping | Pass 1 | High |
| [NAR-012](./NAR-012.md) | Thematic tagger | Pass 1 | High |
| [NAR-013](./NAR-013.md) | Narrative Node JSON validation | Pass 1 | Medium |
| [VIS-014](./VIS-014.md) | Visual proxy ingestion | Pass 2 | High |
| [VIS-015](./VIS-015.md) | Visual state classification prompt | Pass 2 | High |
| [VIS-016](./VIS-016.md) | Visual State Event validation | Pass 2 | Medium |
| [VIS-017](./VIS-017.md) | Chunked analysis for long assets | Pass 2 | Medium |
| [ASM-018](./ASM-018.md) | Cross-reference logic | Pass 3 | High |
| [ASM-019](./ASM-019.md) | Cut-list generation rules | Pass 3 | High |
| [ASM-020](./ASM-020.md) | Assembly Blueprint validation | Pass 3 | Medium |
| [FCP-021](./FCP-021.md) | FCPXML 1.8 template builder | Conformer | High |
| [FCP-022](./FCP-022.md) | Track mapping | Conformer | High |
| [FCP-023](./FCP-023.md) | Timecode & frame-duration math | Conformer | High |
| [CLI-025](./CLI-025.md) | argparse CLI | Client | High |
| [CLI-026](./CLI-026.md) | End-to-end happy path | Client | High |
| [CLI-027](./CLI-027.md) | Progress reporting & dry-run | Client | Medium |
| [SFT-028](./SFT-028.md) | Token threshold monitor | Safety | High |
| [SFT-029](./SFT-029.md) | Cost estimator | Safety | Medium |
| [SFT-030](./SFT-030.md) | Structured output enforcement | Safety | High |
| [POL-031](./POL-031.md) | Docs, tests, lint, README | Polish | Medium |
| [WEB-032](./WEB-032.md) | Frontend stack & scaffold | Web Frontend | High |
| [WEB-033](./WEB-033.md) | Backend API service | Web Frontend | High |
| [WEB-034](./WEB-034.md) | Project upload / ingest UI | Web Frontend | High |
| [WEB-035](./WEB-035.md) | Pipeline progress dashboard | Web Frontend | High |
| [WEB-036](./WEB-036.md) | Timeline / cut review UI | Web Frontend | Medium |
| [WEB-037](./WEB-037.md) | Results download | Web Frontend | Medium |
| [WEB-038](./WEB-038.md) | Settings UI | Web Frontend | Medium |

---

## How to update

1. Edit the ticket file (`tasks/<ID>.md`).
2. Change its `Status:` front-matter field.
3. Move the row in the table above to the matching column.
4. Update Quick Stats.
