# Feature: Pass 3 — Assembly Director

> Status: Draft | Last verified: 2026-09-19

## What it does

Sends the narrative map, visual event log, asset inventory, and the editor's free-text direction to Gemini and returns a full assembly plan: timeline cuts across all four tracks plus `audio_cues` requesting ElevenLabs-generated music/SFX. Falls back to the deterministic `match_events`/`generate_cuts` heuristics in dev mode or when the model returns no usable cuts.

## Where the code lives

* `directorcut.assembly.director.run_director()` — AI pass entry point (`src/directorcut/assembly/director.py`)
* `directorcut.assembly.director._narrative_block()` (~L108) — renders the narrative map grouped by source file under `## Narrative from <path>` headers; the per-line `source_file` field only appears when more than one source exists
* `directorcut.assembly.director._plan_to_blueprint()` — validates `source_file` values against the manifest inventory; drops hallucinated paths, falls back when empty
* `directorcut.assembly.director._enforce_unique_source()` (~L363) — post-validation: V1/V2 cuts reusing a source range get **head/tail-trimmed** first (the in/out-point slides to the accepted range's boundary, timeline shifts with it - 2026-09-27; a 0.2s sliver no longer costs a 12s cut, and sub-tolerance slivers can't slip through); drops happen only on interior containment or post-trim residual > 0.25s on the same canonical file, and V1 cuts whose covered narrative text is a repeated take (difflib ratio > 0.85, ≥4 words); then re-lays kept V1 cuts contiguously from 0 and shifts V2/A2/A3 by their anchor V1's shift (overlays on dropped anchors die too; tails clamp to the new end). `pipeline.run()` re-invokes it after `pad_v1_cut_edges` because word-air padding can re-open reuse.
* `directorcut.assembly.crossref.match_events()` / `cuts.generate_cuts()` — deterministic fallback
* `directorcut.assembly.targets` (`src/directorcut/assembly/targets.py`, added 2026-09-22) — hard-target validation: `parse_length_window`/`parse_coverage_min` read "HARD LENGTH TARGET: between 7:00 and 9:00" / "at least 60% ... V2 overlay" out of the direction; `DIRECTORCUT_TARGET_LENGTH_S`/`DIRECTORCUT_BROLL_COVERAGE_MIN` override. `pipeline._director_with_target_check` measures the blueprint and re-prompts the director ONCE via `run_director(correction=...)` with the measured deltas appended; a `[targets]` report line always logs measured-vs-target.
* `directorcut.assembly.word_air.pad_v1_cut_edges()` (`src/directorcut/assembly/word_air.py`, added 2026-09-22) — enforces "sentence boundaries with ~0.2s air" using raw Scribe `words[]` timing re-read from the content-hashed STT cache (`narrative/stt.load_cached_words`). Edges extend into neighbouring silence >=0.15s (up to `AIR_S=0.2`), land on the word boundary in tighter gaps, never inside a word; shared-silence contention is midpoint-split and all extensions are clamped against every other cut's source range on the file. Recompacts via `compact_timeline` afterwards.

## Tracks

| Track | Content |
|---|---|
| `V1_A1_PRIMARY` | Contiguous storyline (voiceover and/or A-roll) |
| `V2_OVERLAY_BROLL` | B-roll video / still-image overlays |
| `A2_MUSIC` | Music beds (existing assets or generated) |
| `A3_SFX` | Sound effects / stingers |

## Gotchas

* `source_file` in the model's response is validated against the manifest asset list (exact path, then basename match) — anything else is dropped with a warning.
* **Multi-take / unique-source rules** (added 2026-09-19): the prompt tells the model to pick the best take per beat, never repeat a line across takes, and never reuse any source range — every second of a file may appear at most once across all tracks. `_enforce_unique_source` enforces it anyway because multi-take dumps still produce duplicates; each drop logs a WARNING.
* **Track/type enforcement** (`_TRACK_EXTS` in `_plan_to_blueprint`, ~L283): `V1_A1_PRIMARY` = video|image|audio, `V2_OVERLAY_BROLL` = video|image only, `A2_MUSIC`/`A3_SFX` = audio only. Type mismatches are dropped like hallucinated paths — prevents the real failure where an mp3 landed on the overlay lane and the video sat under it, producing a video-free timeline.
* **Unscripted footage**: `narrative/transcript.py` tiles scriptless dumps into 60s "Unscripted segment" nodes covering `manifest.duration_seconds` (verified 2026-09-18 — without it the director only saw a single 5s placeholder node and produced a 5s FCPXML). The prompt tells the model to pick the segments that serve the direction rather than cover the whole runtime. `tag_narrative` is skipped for these nodes (no speech to tag).
* The dev-mode stub (`_fake_plan`) lays down user-supplied music/SFX/image/B-roll assets and requests one full-length music cue so every conform lane is exercised offline.
* Direction like "fast and snappy" only takes effect through this pass — the deterministic fallback ignores it.
* **Targets are enforced, not wished for** (verified 2026-09-22): attempt 1 on the StS2 EA-review shipped 4:15 against a "7-9 minutes" direction with 10 overlays vs 60% coverage. `targets.py` measures and feeds the deltas back in exactly one corrective call — a second retry is deliberately skipped (a full director call costs as much again).
* **Word-air runs AFTER cue rendering but BEFORE the final blueprint.json write** — cue cuts (A2/A3) aren't affected, and the STT cache lookup (`_load_word_timings`) maps extracted-audio → cut-facing `source_file` so multi-A-roll projects get per-source word timings. Script-path projects have no word timing and are skipped.
