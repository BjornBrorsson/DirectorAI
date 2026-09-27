# Feature: Ingestion & Proxy Engine

> Status: Draft | Last verified: 2026-09-18

## What it does

Recursively scans the `/ingest` directory, assigns each supported file a role (`a_roll`, `b_roll`, `voiceover`, `music`, `sfx`, `image`, `script`, or `auto`), probes durations + embedded audio-stream counts via FFprobe, reads `direction.txt`/`notes.txt` as free-text creative direction, extracts every embedded audio stream of multi-track videos as its own asset, and generates a 360p 1-FPS visual proxy per video asset plus a compact audio proxy.

## Where the code lives

* `directorcut.ingest.ingest()` — multi-asset scan + manifest build (`src/directorcut/ingest.py` ~L227)
* `directorcut.ingest.with_primary_fields()` — derives `video_path`/`audio_path`/etc. from `assets` (`src/directorcut/ingest.py` ~L167)
* `directorcut.ingest._probe_audio_stream_count()` — counts `a` streams per video → `Asset.audio_stream_count` (`~L132`)
* `directorcut.assets.interpret_assets()` — Gemini pass resolving `AUTO` roles + describing images (`src/directorcut/assets.py`)
* `directorcut.media_prep.prepare_aroll_media()` (`src/directorcut/media_prep.py`, added 2026-09-22) — post-interpret/pre-proxy A-roll bake step, see below
* `directorcut.proxy.visual.generate_visual_proxy()` / `proxy.audio.extract_audio()` / `proxy.audio.extract_audio_tracks()`
* `directorcut.pipeline._audio_proxy()` — picks the Pass-1 transcript source: classified voiceover → video stream0 → mixed audio

## A-roll media prep (media_prep.py)

After role interpretation and before proxies/STT, `prepare_aroll_media` may bake derived files into `output/<project>/media/` and re-point `Asset.path` at them (`Asset.derived_from` keeps the original). Downstream stages — transcripts, proxies, conform — only ever see the baked file.

* **Reframe** (`DIRECTORCUT_REFRAME_AROLL`, default `true`): `probe_geometry` reads `stream_side_data=rotation` + dimensions. Any non-zero display rotation or effective AR off 16:9 by >2% triggers a one-pass ffmpeg bake: `crop` (face-biased ~39% down the slack for too-tall frames; centered for too-wide) + `scale=1920:1080:flags=lanczos` → h264/yuv420p, audio copied, rotation metadata dropped by autorotate-on-decode. Verified against Resolve: `crop=2160:1215:0:1024,scale=1920:1080` on a 3840x2160 rot=-90 file is the fix for adjust-transform misapplication. Direction text can opt out ("no reframe", "keep portrait framing").
* **Voice normalize** (`DIRECTORCUT_NORMALIZE_VOICE`, default `false`): measures integrated loudness via `ebur128` first; files inside the -18..-15 LUFS band are skipped (double-normalizing pumps the noise floor). Otherwise a remux keeps the video stream (`-c:v copy`, rotation preserved) and re-encodes audio `highpass=80 -> compand -> loudnorm I=-16:TP=-1.5 -> aresample=48k -> aac 192k stereo`.
* Both are idempotent: an existing, newer derived file is reused and `changed=False`, so resume doesn't flip the ingest fingerprint.

## Multi-track audio

Videos with `audio_stream_count > 1` (e.g. OBS mic/game/desktop on separate streams) get each stream extracted via `ffmpeg -map 0:a:N` to `<stem>_streamN.mp3` (48kHz stereo) **before** `interpret_assets`, appended as AUTO assets described "audio stream N embedded in X" — so the classifier can tell mic from gameplay audio and Pass 3 can place streams independently. Files are named `_stream` not `_track` because "track" is a music keyword in the dev heuristic.

## Role resolution order

1. Ancestor subdir name under ingest root (`broll/`, `music/`, `sfx/`, `voiceover/`, `images/`, `scripts/`, `aroll/`).
2. Filename prefix (`broll_`, `sfx_`, `vo_`, `music_`, `img_`, `script.`).
3. `AUTO` — resolved by `interpret_assets()` (heuristic in dev mode).

Roles must be compatible with the file type — a `.mp4` can never take `music`; incompatible hints degrade to `AUTO`.

## Keys & config

* `INGEST_DIR` — source assets directory.
* `OUTPUT_DIR/proxies/` — generated proxy files (actually `<output>/<project>/`).

## Gotchas

* Token budget must be estimated before cloud upload (`safety.token_guard`).
* Visual proxy timecode must map exactly back to the original source; `VisualEvent.source_file` records which asset each event came from.
* Unsupported extensions are skipped with a log line, not an error.
* Direction files (`direction.txt`, `direction.md`, `notes.txt`) are never treated as script assets.
* Extracted stream assets live in `<output>/<project>/` and are resolved absolute — they can land on the FCPXML timeline as `file://` sources.
* `_audio_proxy()` detects already-extracted streams via `is_relative_to(output_dir)` to avoid re-encoding them for the transcript.
* **`_bake()` branches the stream map by codec type** (2026-09-27): video inputs keep `-map 0:v:0 -map 0:a?`; audio-only inputs (extracted `*_streamN.mp3` voiceovers) get `-map 0:a:0` and no video args - the video-first map used to fail `Stream map '0:v:0' matches no streams`.
