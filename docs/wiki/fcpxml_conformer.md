# Feature: FCPXML Conformer

> Status: Draft | Last verified: 2026-09-22

## What it does

Maps the Assembly Blueprint onto an FCPXML 1.8 document in one of three flavours (`DIRECTORCUT_FCPXML_TARGET`, default `resolve` — full matrix + per-target quirks in [nle_targets.md](./nle_targets.md)):

* **`resolve`** — DaVinci Resolve compatible. Primary cuts become spine `<asset-clip>`s (visual) or `<audio>` (audio-only); B-roll/images connect on `lane="1"`. Music/SFX are **pre-rendered beds** — `conformer/mixdown.py` bounces `music_bed_ducked.wav` (sidechain-ducked against a timeline-placed voice guide) and `sfx_bed.wav` into `output/<project>/`, and the conformer attaches each as ONE clip on `lane="-2"`/`lane="-3"` with zero `<param>` keyframes (Resolve ignores them — verified 2026-09-22).
* **`fcpx`** — keeps the legacy per-cut audio clips with `<param name="volume">` keyframe ducking, legal for Final Cut Pro.
* **`premiere`** — same XML shape as `fcpx` (Premiere Pro imports FCPXML); its `<param>`/connected-clip handling is unverified — see the TODO list in [nle_targets.md](./nle_targets.md).

## Where the code lives

* `directorcut.finalize.finalize_blueprint()` — shared post-assembly step: mixdown (resolve target) then `build_fcpxml`, writes `<project>.fcpxml`; also used by `directorcut cut` re-conform (`src/directorcut/finalize.py`)
* `directorcut.conformer.fcpxml.build_fcpxml(blueprint, target, beds)` (`src/directorcut/conformer/fcpxml.py` ~L191)
* `directorcut.conformer.fcpxml._validate_spine()` (~L96) — conform-time guard: raises `ConformError` if any spine element would be `<video>`; nested/lane-carrying `<video>` stays legal
* `directorcut.conformer.mixdown.render_mixdown()` — voice guide + sidechain-ducked music bed + flat SFX bed WAVs (`src/directorcut/conformer/mixdown.py`)
* `directorcut.conformer.ducking` — automatic volume-keyframe ducking, `fcpx` target only (`src/directorcut/conformer/ducking.py`)

## Automatic ducking

**resolve target:** `render_mixdown` builds `voice_guide.wav` by placing each V1 cut's source audio at its `timeline_in` (multi-A-roll aware — groups by `source_file`), then renders `music_bed_ducked.wav` via `sidechaincompress` (threshold 0.015, ratio 12, attack 40ms, release 500ms) keyed off the guide, and `sfx_bed.wav` as a flat mix. Filter graphs go through `-filter_complex_script` because Windows CreateProcess caps command lines near 32K chars.

**fcpx target:** Music/SFX clips get a `volume` `keyframeAnimation` computed from the merged primary-track voice ranges: `0` dB at edges, ramping to `MUSIC_DUCK_DB=-12` / `SFX_DUCK_DB=-6` under dialogue with `DUCK_RAMP_S=0.4` smooth ramps. Dip regions are expanded by the ramp and merged so close dialogue lines don't pump.

## Gotchas

* **`<video>` is video-only** — it never brings the asset's audio, so primary clips must be `<asset-clip>` (verified 2026-09-18: Resolve imported 9 silent frozen clips). Overlays deliberately stay `<video>` (muted B-roll). `_validate_spine()` raises `ConformError` if a spine `<video>` ever slips through — a loud failure beats a timeline Resolve renders wrong (verified 2026-09-22: spine `<video>` froze and misplaced).
* **Resolve ignores `<param name="volume">` keyframes** (verified 2026-09-22) — ducking must be baked into WAV beds (`resolve` target) or the import plays music full-blast under dialogue.
* **`<asset>` needs `duration` + `format`** or Resolve can't map rational `start` values to frames and shows frame 0 forever. `_probe_asset()` runs ffprobe per asset at conform time (files are local) and falls back to extension guesses.
* **`<sequence>` needs `tcStart`, `duration`, `tcFormat`, `audioLayout`, `audioRate`** — without `tcStart` Resolve defaults to broadcast ~3600s; spine `offset`s below that get re-anchored via `start` (source time), scattering clips across the full source duration (verified 2026-09-18: 1h of dead timeline, clips clustered at the end). Emit `tcStart="0s"`.
* Spine clips and connected clips carry `enabled="1"` + `tcFormat="NDF"` (and `format` on `asset-clip`); assets carry `audioSources`/`audioChannels`/`audioRate` — mirrors Resolve's own export shape.
* Document starts with `<!DOCTYPE fcpxml>` like Resolve exports.
* A connected clip's `offset` is expressed in the parent's timebase: `parent.start + (child.timeline_in - parent.timeline_in)` (`attach_video`/`attach_audio`, verified 2026-09-21). A plain local offset lands children at `timeline_in - source_in` in Resolve — negative when trimming heavily. Children of `<gap>` anchors (no `start`) keep a local offset.
* Keyframe times inside `keyframeAnimation` are clip-local.
* Orphaned connected clips anchor to a synthetic `<gap>` at their timeline position so nothing is silently dropped.
* All `source_file` values must be absolute paths — ingest resolves them.
