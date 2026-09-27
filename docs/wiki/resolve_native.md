# Feature: Native Resolve timeline build via scripting API (skip FCPXML)

> Status: Prototype (script generator only — real Resolve execution unverified) | Last verified: 2026-09-23 (API docs research; no live Resolve run yet)

Feasibility spike + prototype for `Tickets/Backlog/Needs further specification/direct-resolve-timeline-build-via-scripting-api-skip-fcpxml.md`. Motivation: half the conform bugs in the StS2 EA-review runs were Resolve's FCPXML importer reinterpreting semantics (tcStart, connected-clip offsets, `<video>` spine, `<param>` volume, `adjust-transform` on rotated media — see `docs/MASTER_MEMORIES.md`). The scripting API creates timelines and places clips natively, removing the interpretation layer.

## API surface (researched 2026-09-23)

Sources: official Resolve scripting README (`Developer/Scripting` inside the install), the community mirrors `wiki.dvresolve.com/developer-docs/scripting-api`, `deric.github.io/DaVinciResolve-API-Docs`, `wheheohu.github.io/bmd_doc/ResolveAPI`, plus the `davinci-resolve-mcp` measured-limitations notes.

### Connecting

* Module: `import DaVinciResolveScript as dvr_script; resolve = dvr_script.scriptapp("Resolve")` (the module wraps `fusionscript` — `fusionscript.dll`/`.so`, not imported directly).
* Module location (append `<…>/Modules` to `PYTHONPATH`/`sys.path`, or set `RESOLVE_SCRIPT_API` + `RESOLVE_SCRIPT_LIB`):
  * Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules`, lib `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll`
  * macOS: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules`
  * Linux: `/opt/resolve/Developer/Scripting/Modules`
* **Resolve must be running** — the API is an IPC bridge into the live app.
* Python ≥ 3.6 64-bit (Lua also supported).

### Free vs Studio — the big licensing caveat

* **External scripting** (a foreign python process calling `scriptapp`) requires **Preferences > General > "External scripting using" = Local/Network** — a **Studio-only** preference. On free Resolve `scriptapp("Resolve")` returns `None` / refuses the connection (measured by `davinci-resolve-mcp` on free 21.0.x).
* **Free-edition path:** scripts launched from inside Resolve — Workspace ▸ Scripts menu or the Fusion-page Console — are handed the live `resolve` object directly. Caveat: **Resolve 21.1 moved even menu-launched Python scripting behind Studio** (free 21.1's Scripts menu no longer lists `.py` files per the MCP changelog notes). Treat free-edition support as version-fragile; verify on the user's build.
* The generated `build_timeline.py` handles both: uses the injected `resolve` global when Resolve hosts the script, else imports `DaVinciResolveScript` and calls `scriptapp("Resolve")` after seeding `sys.path` with `RESOLVE_SCRIPT_API` + the platform defaults.

### Coverage vs our needs

| Need | API | Status |
|---|---|---|
| Media pool import | `MediaPool.ImportMedia([paths])` → `[MediaPoolItem]` | ✅ covered |
| Timeline creation | `MediaPool.CreateEmptyTimeline(name)`; `project.SetCurrentTimeline(t)`; `timeline.SetStartTimecode("00:00:00:00")` (kills the ~1h tcStart bug natively); `project.SetSetting("timelineFrameRate", str(fps))` | ✅ covered |
| Clip placement with source in/out | `MediaPool.AppendToTimeline([{clipInfo}])` — `mediaPoolItem`, `startFrame`/`endFrame` (source frames), `recordFrame` (timeline position — exact placement, no append-order dependence), `trackIndex`, `mediaType` (1=video only, 2=audio only). Returns placed `TimelineItem`s | ✅ covered — `recordFrame` is the key win vs FCPXML (exact placement incl. gaps). ⚠️ `startFrame`/`endFrame` base (0- vs 1-based, inclusive/exclusive) is inconsistent in community reports — verify on first live run |
| Extra tracks | `timeline.AddTrack("video")`, `AddTrack("audio", "stereo")`, `SetTrackName`, `GetTrackCount` | ✅ covered |
| Clip transforms | `TimelineItem.SetProperty` video keys: `Pan`, `Tilt`, `ZoomX/Y`, `RotationAngle`, `Crop*`, `AnchorPoint*`, … | ✅ covered (unused in v1 — media prep already bakes reframes) |
| **Clip audio volume** | `TimelineItem.SetProperty("Volume"|"Level"|"Gain"|"AudioVolume")` all return `False` on ≤ 21.0 (measured by davinci-resolve-mcp); **21.1 added "native audio properties" to Get/SetProperty** — version-gated, unverified | ⚠️ partial — keep baked beds; v1 sets levels best-effort |
| Whole-mix alternative | `project.ApplyFairlightPresetToCurrentTimeline(name)` (20.2.2+) | fallback noted |
| Not covered | no API for per-track/clip volume keyframes pre-21.1, no insert-into-track-index for `Insert*IntoTimeline` (patch-panel only) — irrelevant since `AppendToTimeline` clipInfo has `trackIndex`+`recordFrame` | — |

Bonus found while researching: `MediaPool.ImportTimelineFromFile(path, {importOptions})` can import AAF/EDL/XML/**FCPXML** natively — a hybrid "let Resolve read our existing FCPXML inside the app" option worth remembering, though it re-introduces the importer semantics this ticket exists to bypass.

## Prototype

* `directorcut.conform_resolve.emit_resolve_script(blueprint, assets, out_path)` — writes `out_path` (the CLI uses `output/<project>/build_timeline.py`) as a standalone script: embedded `MEDIA` + `PLAN` JSON (record/start/end frames precomputed at `timeline_meta.target_fps`), then a fixed runtime that connects, imports media, creates the timeline, ensures tracks, and places every clip via `AppendToTimeline` clipInfo.
* Track layout mirrors the FCPXML conform: V1 spine video+embedded audio, V2 overlays `mediaType=1`, music→audio track 2, SFX→audio track 3 (`mediaType=2`). Audio-only V1 sources land on A1.
* Rendered beds (`music_bed_ducked.wav`, `sfx_bed.wav` next to the script) replace that lane's per-cut audio exactly like `build_fcpxml(target="resolve")` — one timeline-aligned clip at record 0. `voice_guide.wav` is never placed (sidechain key only).
* Audio levels: `SetProperty` best-effort on un-bedded music (−8 dB) / SFX (−9 dB) clips; skipped silently on Resolve < 21.1 (beds already carry baked trims).
* **Unverified:** never executed against a live Resolve tonight — generated source is unit-tested for the expected API call sequence only. First live run must verify: `startFrame`/`endFrame` frame base, `trackIndex` semantics for audio `mediaType=2`, `recordFrame` vs `GetStartFrame()` offset, free-edition launch path on the installed build.

## Decision record — FCPXML vs native per target NLE

* **Resolve:** prefer **native scripting build** once verified on real Resolve; FCPXML remains the fallback for headless/batch use and for users who can't run scripts (free 21.1+). The native path eliminates the whole class of importer-reinterpretation bugs and gains exact `recordFrame` placement. Ducking strategy is unchanged — baked beds still win because per-clip volume is only scriptable on ≥ 21.1 and keyframed volume isn't scriptable at all.
* **FCPX / Premiere:** keep FCPXML — no equivalent scripting surface; their FCPXML importers are the only route anyway (`nle_targets.md`).
* **CLI UX:** `directorcut conform <project>` emits the script; importing remains a user action inside Resolve. Keep `run` producing FCPXML as today — the native path is additive, not a replacement, until live-verified.
