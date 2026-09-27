# System Overview

> Status: Draft | Last verified: 2026-09-17

DirectorCut-AI is a local-first, cloud-intelligent rough-cut editor. It transforms a dumped folder of production assets (A-roll, B-roll, voiceover, music, SFX, images, scripts) plus optional free-text direction into an NLE-ready timeline (FCPXML) using a multi-pass Gemini pipeline (HTTP API **or** the `gemini` CLI).

## Architecture

```
[ Local Client App ]
   │
   ├── 1. Ingestion Engine ──────> /ingest folder (role subdirs / prefixes / AUTO)
   │      └── Asset interpreter -> Gemini classifies AUTO files by name+thumbnail
   │
   ├── 2. Proxy Pipeline ────────> FFmpeg: audio stripping + 1-FPS downsampled
   │                               visuals, one proxy per video asset
   │
   ├── 3. API Choreographer ─────> Gemini (API or CLI) multi-pass analysis
   │
   ├── 4. Audio Design ──────────> ElevenLabs: generates music/SFX the
   │                               director asked for (dev: synthesized WAV)
   │
   ├── 5. Conformer/Compiler ────> Assembly Blueprint JSON -> FCPXML
   │                               (incl. automatic volume-keyframe ducking)
   │
   └── 6. Web Frontend (optional) -> Multi-asset upload + direction box,
                                   progress, cut review, download
```

## Cloud Passes

| Pass | Input | Output |
|---|---|---|
| Interpret | File metadata + thumbnails for AUTO assets | Role + description per asset |
| Pass 1: Narrative Engine | Script text(s) + voiceover audio | Narrative Node JSON (spoken lines with tags) |
| Pass 2: Visual Event Logger | 1-FPS visual proxy per video | Visual State Vector JSON (tagged with `source_file`) |
| Pass 3: Assembly Director | Narrative + Visual JSONs + asset inventory + direction | Assembly Blueprint JSON (cuts + audio cues) |

## Local Compiler

The conformer maps the blueprint onto a DaVinci Resolve-compatible FCPXML 1.8 timeline, using microsecond frame durations and referencing original high-res source files. `A2_MUSIC`/`A3_SFX` clips land on connected audio lanes with automatic ducking keyframes under dialogue.

## Key Constraints

* Fit ~2 hours of source assets into Gemini's 1,000,000 token context.
* Keep API cost below ~$1.50 per rough cut (cost scales with total video footage).
* Require only pacing/audio polish after import into the NLE.
