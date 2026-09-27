# Feature: Pass 2 — Visual Structural Logger

> Status: Draft | Last verified: 2026-09-22

## What it does

Analyzes the 1-FPS visual proxy with Gemini to detect gameplay state events: `STATE_UI`, `STATE_ACTION`, `STATE_DEATH`, `STATE_TUTORIAL`.

## Where the code lives

* `directorcut.visual.chunked.analyze_visual_proxy()` — token-bounded chunking + merge; `checkpoint_path=` enables per-chunk JSONL resume (`output/<project>/visual_chunks/<stem>_<pathhash>.jsonl`, one `{"chunk_index", "events"}` line per finished chunk) so a crash loses only the in-flight chunk. Fully checkpointed assets never touch cv2 (lazy frame decode).
* `directorcut.visual.classifier.classify_visual()` — one Gemini structured call per chunk (`task="visual"`, i.e. the fast model chain).
* `directorcut.token_budget.chunk_proxy()` — sizes chunks to `SAFE_INPUT_TOKENS` (CLI/agy) or `_API_CHUNK_TOKENS` = 40k (HTTP API: ~20MB request body cap on generateContent).

## Keys & config

* `GEMINI_PAID_KEY_MAIN` / `GEMINI_API_KEY` / `GEMINI_PAID_KEY` / `GEMINI_FREE_KEY`, `GEMINI_MODEL` / `GEMINI_MODEL_FAST`, `GEMINI_MODEL_CHAIN_VISUAL`.
* Pass 2 is a "fast" task: `Settings.key_chain("visual")` orders FREE → PAID_MAIN → PAID so the high-volume chunks burn free-tier quota first (see `cli_client.md`).

## Gotchas

* Long videos must be chunked; events at chunk boundaries may need overlap handling (adjacent same-classification events merge via `time_end`).
* **Per-chunk JSONL checkpoints** (added 2026-09-22): a torn last line fails JSON parsing and is simply re-analyzed; a resumed run replays finished chunks from disk instead of re-calling the model. The checkpoint filename embeds an 8-char sha1 of the source path so two assets sharing a filename stem can't cross-wire events.

## agy frame cap

For `GEMINI_BACKEND=antigravity`, per-call media is capped at `ANTIGRAVITY_MEDIA_FRAME_CAP` (default 16): chunks on long videos carry thousands of 1fps frames, and agy tiles the whole set into ONE contact sheet. Over the cap, `_write_agy_media` samples evenly and reports the stride in the prompt so event times stay anchored (verified 2026-09-18). Trade-off: Pass-2 granularity on multi-hour files is ~stride-sized (~3-4 min for a 57-min chunk) — fine for rough-cut state detection; use the HTTP API backend for frame-dense analysis.

## Cost levers (2026-09-27)

* **`DIRECTORCUT_VISUAL_FPS`** (default 1.0, range 0-1): stride-samples the 1fps frame cache at classify time - `0.2` sends one frame per 5s (~5x fewer input tokens; frames dominate run spend at ~350 tok each). `classify_visual(sample_interval_s=)` tells the model the real interval so event times stay in seconds. Implemented in `chunked.analyze_visual_proxy` (~L93).
* **Canonical-twin dedupe**: the Pass-2 loop groups `video_assets` by `assembly.director._canonical_source_keys` (inode + `derived_from` chain). Only the first registration of a physical recording is proxied/analyzed; events are cloned onto sibling `source_file` paths so the conformer can still cut each role distinctly. On the v3 ingest this would have halved the stream's ~85 chunk calls and skipped its second ~25min proxy encode.
