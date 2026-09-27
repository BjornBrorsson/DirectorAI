# Feature: NLE target matrix (Resolve / FCPX / Premiere / CapCut)

> Status: Draft | Last verified: 2026-09-22 (Resolve); other targets unverified

`DIRECTORCUT_FCPXML_TARGET` (`Settings.fcpxml_target`, `config.py` ~L114)
selects which NLE the emitted FCPXML 1.8 is shaped for. Every target shares
the same document skeleton (`<sequence>` + `<spine>` + connected clips) —
the flag only toggles the pieces whose semantics differ per NLE.

## Compatibility matrix

| Target | Flag value | Import route | Ducking | Verified? |
|---|---|---|---|---|
| DaVinci Resolve | `resolve` (default) | File > Import > Timeline (FCPXML) | **Baked beds** — `render_mixdown` produces `music_bed_ducked.wav` / `sfx_bed.wav`; zero `<param>` emitted | Yes — repeated imports during the StS2 EA-review runs (2026-09-18..22) |
| Final Cut Pro | `fcpx` | File > Import > XML | `<param name="volume">` keyframeAnimation on each A2/A3 clip | No — legal FCPXML per spec, never imported in-house |
| Premiere Pro | `premiere` | File > Import (Premiere reads FCPXML) | Same as `fcpx` (params kept — see TODOs) | No — added 2026-09-23, unverified |
| CapCut | — | none | n/a | No FCPXML/XML import path; would need a different export format (future work, e.g. OTIO or a CapCut draft generator) |

## What the flag toggles

* **Ducking policy** — `resolve`: beds pre-rendered by `conformer/mixdown.py`
  (sidechain-ducked music, flat SFX) and attached as one `<audio>` clip per
  lane; the `build_fcpxml` guard raises `ConformError` if any `<param>` slips
  through. `fcpx`/`premiere`: per-cut `<param name="volume">` keyframe dips
  computed by `conformer/ducking.py`.
* **Transform policy** — *target-independent*: rotated/portrait or non-16:9
  A-roll is baked to flat 1920x1080 at ingest (`media_prep.prepare_aroll_media`,
  `DIRECTORCUT_REFRAME_AROLL`) because Resolve mis-applies `adjust-transform`
  on rotation side-data. If a future target proves to handle
  `adjust-transform` correctly this could become target-conditional.
* **Spine element policy** — *target-independent*: spine visuals are always
  `<asset-clip>` (a spine `<video>` is video-only and froze/misplaced in
  Resolve — `fcpxml._validate_spine` guards it), audio-only sources emit
  `<audio>`, overlays nest as connected `<video lane="1">`, A2/A3 as
  `<audio lane="-2"/"-3">`.
* **Sequence metadata** — *target-independent*: `tcStart="0s"`,
  `duration`, `tcFormat="NDF"`, `audioLayout`/`audioRate` on `<sequence>`,
  `duration`/`format`/`audioSources` on `<asset>` (all required by Resolve;
  harmless-to-helpful elsewhere).
* **Connected-clip offsets** — *target-independent*: emitted in the parent's
  timebase (`parent.start + local`) per the FCPXML spec; `<gap>` children
  keep local offsets.

## Known Premiere quirks (TODOs — verify on first real import)

* **TODO:** Premiere's FCPXML import historically reads `asset-clip`/`video`
  clips, `lane` stacking, and rational times, but support for
  `keyframeAnimation` volume dips is undocumented — if Premiere drops them,
  route `premiere` through the baked-bed path like `resolve`.
* **TODO:** Premiere ignores `tcStart`-style broadcast conventions less
  predictably than Resolve; confirm clips land at `offset` not `start`.
* **TODO:** `hasAudio`/`hasVideo`/`audioSources` attribute coverage —
  Premiere may probe media itself; keep emitting them (harmless).
* **TODO:** still images on V2 may need an explicit `format` reference;
  verify connected `<video>` children of `<asset-clip>` parents map to
  Premiere's track model.
* **TODO:** Premiere's FCPXML importer prefers version "1.8"/"1.9" round
  trips from FCP X exports; verify our hand-built document parses.

## Real-import verification status

Only **Resolve** has a documented real import (StS2 EA-review timeline,
annotest.fcpxml — see `docs/MASTER_MEMORIES.md`). Recording one import per
remaining target is still open work: import the same `output/<project>/
<project>.fcpxml`, note where clips/ducking/offsets land, and fold findings
into the matrix above.
