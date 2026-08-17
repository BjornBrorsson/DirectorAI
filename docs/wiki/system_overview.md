# System Overview

> Status: Draft | Last verified: 2026-08-17

DirectorCut-AI is a local-first, cloud-intelligent rough-cut editor. It transforms raw gameplay capture and voiceover into an NLE-ready timeline (FCPXML) using a multi-pass Gemini 3.7 Flash pipeline.

## Architecture

```
[ Local Client App ]
   │
   ├── 1. Ingestion Engine ──────> /ingest folder (full-res assets + scripts)
   │
   ├── 2. Proxy Pipeline ────────> FFmpeg: audio stripping + 1-FPS downsampled visuals
   │
   ├── 3. API Choreographer ─────> Gemini Cloud multi-pass analysis
   │
   ├── 4. Conformer/Compiler ────> Assembly Blueprint JSON -> FCPXML
   │
   └── 5. Web Frontend (optional) -> Project upload, progress, cut review, download
```

## Cloud Passes

| Pass | Input | Output |
|---|---|---|
| Pass 1: Narrative Engine | Script text + voiceover audio | Narrative Node JSON (spoken lines with tags) |
| Pass 2: Visual Event Logger | 1-FPS visual proxy | Visual State Vector JSON (UI/action/death/tutorial events) |
| Pass 3: Assembly Director | Narrative + Visual JSONs | Assembly Blueprint JSON (final cut list) |

## Local Compiler

The conformer maps the blueprint onto a DaVinci Resolve-compatible FCPXML 1.8 timeline, using microsecond frame durations and referencing original high-res source files.

## Key Constraints

* Fit ~2 hours of source assets into Gemini's 1,000,000 token context.
* Keep API cost below ~$1.50 per rough cut.
* Require only pacing/audio polish after import into the NLE.
