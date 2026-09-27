# Upload picker + multi-track audio — PROJECT_LOG

## Request

Manual testing surfaced three issues:

- `assets[]: file not found: "C:\Users\Björn\Videos\2026-04-10 00-00-39.mp4"`
  — the file exists; Windows "Copy as path" wraps it in literal quotes.
- Wants an actual file picker instead of typing paths.
- Wants support for multiple audio tracks embedded in a video file.

## What changed (verified 2025-…)

### Quoted paths — `api/main.py::_clean_path`

- Strips one matching outer pair of `"`/`'` after whitespace trim, then
  `expandvars` + `expanduser`. Applied to every ingest path before
  `Path(...).is_file()`.
- Verified live against the user's real file: quoted path ingests as
  `2026-04-10 00-00-39.mp4`.

### File picker — `GET /browse` + `FileBrowser` modal

- `GET /browse?path=` lists dirs + media files (VIDEO/AUDIO/IMAGE/SCRIPT
  exts) with sizes; hidden-dot dirs skipped; ACL/broken entries tolerated.
  Default root: `Path.home()`. Returns `{path, parent, dirs, files}`.
- Frontend `UploadForm.tsx`: per-row **Browse…** button opens a modal that
  navigates real server directories (parent `..`, dir names, file sizes)
  and fills the row's path on select. Path input remains pasteable.
- Note: a browser `<input type=file>` cannot expose local absolute paths;
  server-side browsing is the local-first equivalent (no multipart upload).

### Multi-track audio — probe → extract → classify

- `Asset.audio_stream_count` (models.py) — ffprobe counts `a` streams for
  every video at ingest (`ingest.py::_probe_audio_stream_count`, best-effort
  0 on failure).
- `proxy/audio.py::extract_audio_tracks` — one ffmpeg `-map 0:a:N` per
  stream → `<stem>_streamN.mp3` (48kHz stereo, timeline-quality). Named
  `_stream` not `_track` on purpose: "track" is a music keyword in the
  dev heuristic and would misclassify mic audio as a music bed.
- `pipeline.py` reordered: streams are extracted **before**
  `interpret_assets` and appended as AUTO assets with description
  "audio stream N embedded in X" so the AI can classify mic vs. gameplay.
  `assets.py` listing now surfaces `asset.description` to the classifier.
- `_audio_proxy()` picks the transcript source: classified voiceover
  (extracted stream used in place, no re-encode) → video stream0 →
  mixed video audio. Single-stream videos unchanged.
- Verified: user's real file has 3 aac streams; all extracted. E2e test
  generates a 2-stream file and asserts stream assets classify + appear
  in assets.json.

## Verification

- `pytest`: 94 passed (incl. new quoted-path, /browse, stream-count,
  extract_audio_tracks, and multi-stream e2e tests).
- ruff clean, mypy clean, frontend `tsc` build clean.
- Live API: `/browse` lists real dirs; quoted-path ingest succeeds.

## Test-isolation fix

- `.env` (GEMINI_BACKEND=antigravity, DIRECTORCUT_ENV=prod) leaked into
  `Settings()` constructions in tests. Fixed: `client` fixture pins
  `env="dev"` via model_copy; `live_settings` pins `gemini_backend="api"`.

## Follow-up: long-run progress + resilience (verified 2026-09-18)

The user's "annotest" run on a real ~1h42m OBS file exposed four issues:

1. **Event-loop freeze** — `run_pipeline` ran synchronously inside the
   async WS handler; the entire API froze for the run's duration.
   Fix: `_RunState` registry + `_start_run` daemon thread
   (`api/main.py` ~L274). WS + POST /run both attach to the shared run;
   `_stream_run_events` replays history then live-tails, so a reconnect
   reattaches instead of duplicating the run.
2. **No progress** — `run_pipeline(on_progress=...)` emits
   `{status:progress, stage, detail, elapsed_s}` at every phase boundary
   and per-chunk inside Pass 2 (`analyze_visual_proxy(on_chunk=...)`).
   `Progress.tsx` renders current stage + live log + ticking elapsed.
3. **Pass-1 validation crash** — `tag_narrative` sent only
   `node_id: text` but the schema demands timecodes, so agy fabricated
   `time_end == time_start` → ValidationError → run dead. Fixes:
   (a) prompt now includes each node's real `[start - end]` and tells the
   model to keep them; (b) `_call_agy` retries bad-response errors with
   `_AGY_CORRECTIVE_HINT` (covers all passes, not just tagger).
4. **Token-budget off-by-one** — `chunk_proxy` sized chunks with the
   truncated `int(276.15)` tokens/frame, so a max-capacity chunk estimated
   ~950,568 > 950,000 ceiling → `BudgetError` on a ~1h42m file. Now uses
   the untruncated float (`token_budget.py` ~L51).

Verification: 99 tests (new: WS reattach, chunk-at-capacity, agy
validation retry, non-transient fail-fast). ruff/mypy/tsc clean.
Backend restarted; annotest re-run is safe to launch.

## Follow-up 2: unscripted dumps produced a ~5s FCPXML (verified 2026-09-18)

User report: annotest FCPXML was "a 4 second clip of nothing happening".
Artifact diagnosis (`output/projects/annotest/`):

- `narrative.json` had ONE node, 00:00-00:05, "Voiceover without script."
  `transcribe()` emitted a single 5s placeholder whenever no script file
  existed, so the director could only cut a 5s window.
- `blueprint.json` placed `stream1.mp3` on V1_A1_PRIMARY and the mp4 on
  V2_OVERLAY_BROLL (audio as a "B-roll overlay"), all inside that 5s
  window -> video-free output.
- `visual.json`/`assets.json` were healthy: 9 real events across 1h42m,
  streams classified correctly (stream0=sfx, stream1=voiceover,
  stream2=music).

Fixes:

1. `transcribe(text, audio_path, duration_seconds=...)` tiles unscripted
   sources into 60s "Unscripted segment" nodes covering the full duration
   (`narrative/transcript.py` `_unscripted_segments`). Pipeline passes
   `manifest.duration_seconds` and skips `tag_narrative` when
   `nar_input.has_script` is false (placeholder text has nothing to tag;
   also saves an agy call).
2. `_plan_to_blueprint` now enforces `_TRACK_EXTS`: V1=video|image|audio,
   V2=video|image only, A2/A3=audio only. Type mismatches are dropped
   with a warning, same as hallucinated paths.
3. Director PROMPT documents unscripted-segment nodes (pick the segments
   that serve the direction, don't cover the whole runtime) and states
   audio can't go on the overlay lane.

Regression tests: `test_narrative.py` tiling tests (130s -> 3 nodes,
last ends 00:02:10; works without audio; placeholder preserved when
duration unknown) + `test_director.py` (mp3 on V2 dropped, mp4 on A2
dropped, audio still legal on V1). 105 tests pass, ruff/mypy clean.

## Follow-up 3: frozen stills + silent audio in Resolve; real STT (2026-09-18)

User report: the 9-clip timeline imported into Resolve as "9 clips of 20
seconds of a still image (frame 1) and a silent audio track".

Root causes in `conformer/fcpxml.py`:

1. Primary cuts emitted `<video>` elements - video-only by spec, so no
   embedded audio reached the timeline. Now `<asset-clip>` (carries
   audio+video). Audio-only primaries stay `<audio>`; overlays stay
   `<video>` (muted B-roll is intended).
2. `<asset>` elements lacked `duration`/`format` - Resolve could not map
   rational `start` times to frames and showed frame 0 for every clip.
   New `_probe_asset()` ffprobes each asset (duration + stream kinds) and
   emits `duration=` + `format=` on video assets; falls back to extension
   guesses when probing fails.

Also shipped: real transcription for unscripted dumps.

- `elevenlabs_client.speech_to_text()` - multipart POST to Scribe
  (`/v1/speech-to-text`, `ELEVENLABS_STT_MODEL` default scribe_v1),
  word-granularity timestamps, retries like the SFX/music paths.
- `narrative/stt.py` - `_words_to_nodes` groups word entries into
  sentence-scale nodes (breaks on .?!, >1.5s pauses, 15s cap; skips
  audio_event entries). `transcribe_audio()` returns None offline/failed
  so the pipeline falls back to segment tiling.
- Pipeline Pass-1 order: script -> tag; else STT -> tag; else tile.

Tests: 117 pass (+12: stt grouping/offline/error/empty, multipart POST,
asset-clip + probed asset attrs, audio asset attrs). ruff/mypy clean.


## 2026-09-18 - Resolve tcStart scatter (post-import fix)

User re-imported the STT-run FCPXML: ~1h of dead timeline, 2 stray SFX,
2 muted clips, then ~30 real cuts clustered at the end. Root cause:
<sequence> lacked tcStart/duration/tcFormat - Resolve defaults tcStart to
broadcast ~3600s, and spine offsets below that get re-anchored via start
(source in-point), scattering clips by source time.

Conformer now emits a Resolve-shaped document:

- sequence: duration, tcStart=0s, tcFormat=NDF, audioLayout=stereo,
  audioRate=48k
- assets: probed audioSources/audioChannels/audioRate (OBS file reports 3)
- clips: enabled=1, tcFormat=NDF, format on asset-clip
- <!DOCTYPE fcpxml> header

Reconformed annotest.fcpxml from the existing blueprint.json (no pipeline
re-run needed). Spine verified: 31 contiguous asset-clips 0->8217/60s,
music on lane -2 spanning 8217/60s, SFX on -3, overlays on +1.

Tests: 118 pass (+1 conformer attrs). ruff/mypy clean. Awaiting user
re-import to confirm placement.
