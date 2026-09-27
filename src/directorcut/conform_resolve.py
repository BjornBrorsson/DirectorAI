"""DaVinci Resolve native timeline build via the scripting API (skip FCPXML).

Prototype generator for the "direct Resolve timeline build" ticket: instead of
importing an FCPXML document (whose semantics Resolve re-interprets - tcStart,
connected-clip offsets, <video> spine, param volume, adjust-transform; see
docs/wiki/fcpxml_conformer.md), `emit_resolve_script` writes a standalone
Python script that talks to a RUNNING Resolve instance through its scripting
API and builds the timeline natively:

    MediaPool.ImportMedia          -> media pool items
    MediaPool.CreateEmptyTimeline  -> timeline named after the project
    MediaPool.AppendToTimeline     -> clipInfo dicts (mediaPoolItem,
                                      startFrame/endFrame = source in/out,
                                      trackIndex, recordFrame = timeline
                                      position, mediaType 1=video 2=audio)
    Timeline.AddTrack / SetTrackName, TimelineItem.SetProperty (video
    transforms; per-clip audio volume only exists on Resolve >= 21.1)

The generated file is script TEXT - it is never executed here. See
docs/wiki/resolve_native.md for the feasibility spike and the FCPXML-vs-native
decision record.
"""

from __future__ import annotations

import json
from pathlib import Path

from directorcut.conformer.mixdown import MUSIC_BED_NAME, SFX_BED_NAME
from directorcut.ingest import AUDIO_EXTS
from directorcut.models import AssemblyBlueprint
from directorcut.utils import parse_timecode

# Track assignment, mirroring the FCPXML layout: V1 carries the spine (with
# embedded audio on A1), V2 is the overlay lane, A2 music / A3 SFX.
_VIDEO_TRACK_V1 = 1
_VIDEO_TRACK_V2 = 2
_AUDIO_TRACK_MUSIC = 2
_AUDIO_TRACK_SFX = 3

# Flat trims applied to un-bedded music/SFX clips - the same trims the mixdown
# bakes into the rendered beds. Per-clip volume via SetProperty only exists on
# Resolve >= 21.1, so the generated script treats it as best-effort.
_MUSIC_LEVEL_DB = -8.0
_SFX_LEVEL_DB = -9.0


def _frames(timecode: str, fps: float) -> int:
    """Timeline/source position in whole frames (round to nearest)."""
    return int(round(parse_timecode(timecode) * fps))


def _placements(
    blueprint: AssemblyBlueprint,
    beds: dict[str, Path],
) -> list[dict]:
    """Flatten blueprint cuts into AppendToTimeline clipInfo rows.

    Each row is JSON-embedded into the generated script; the runtime half maps
    it onto `AppendToTimeline([{clipInfo}])` calls. `out=None` means "to the
    media's last frame" (used for the timeline-spanning rendered beds, whose
    length is only known inside Resolve via GetClipProperty("Frames")).
    `levelDb=None` skips the best-effort SetProperty volume call.
    """
    fps = blueprint.timeline_meta.target_fps
    rows: list[dict] = []
    for cut in blueprint.cuts:
        row = {
            "file": str(cut.source_file),
            "record": _frames(cut.timeline_in, fps),
            "in": _frames(cut.source_in, fps),
            "out": _frames(cut.source_out, fps),
            "label": cut.source_file.name,
            "levelDb": None,
        }
        if cut.track == "V1_A1_PRIMARY":
            if cut.source_file.suffix.lower() in AUDIO_EXTS:
                row.update(
                    {"trackType": "audio", "trackIndex": 1, "mediaType": 2}
                )
            else:
                # Spine clip carries video AND its embedded audio (A1).
                row.update(
                    {"trackType": "video", "trackIndex": _VIDEO_TRACK_V1, "mediaType": None}
                )
        elif cut.track == "V2_OVERLAY_BROLL":
            # Overlay is picture only - its audio never lands on the timeline.
            row.update(
                {"trackType": "video", "trackIndex": _VIDEO_TRACK_V2, "mediaType": 1}
            )
        elif cut.track in ("A2_MUSIC", "A3_SFX"):
            if cut.track in beds:
                # The rendered bed replaces the lane's per-cut clips, exactly
                # like the FCPXML resolve target (mixdown already baked the
                # ducking/trim into the wav).
                continue
            is_music = cut.track == "A2_MUSIC"
            row.update(
                {
                    "trackType": "audio",
                    "trackIndex": _AUDIO_TRACK_MUSIC if is_music else _AUDIO_TRACK_SFX,
                    "mediaType": 2,
                    "levelDb": _MUSIC_LEVEL_DB if is_music else _SFX_LEVEL_DB,
                }
            )
        else:  # pragma: no cover - track is a Literal, unreachable
            continue
        rows.append(row)

    # Rendered beds are timeline-aligned (position p in file == timeline
    # position p), so they always start at record frame 0 / source frame 0.
    for track, audio_index in (
        ("A2_MUSIC", _AUDIO_TRACK_MUSIC),
        ("A3_SFX", _AUDIO_TRACK_SFX),
    ):
        bed = beds.get(track)
        if bed is None:
            continue
        rows.append(
            {
                "file": str(bed),
                "record": 0,
                "in": 0,
                "out": None,
                "trackType": "audio",
                "trackIndex": audio_index,
                "mediaType": 2,
                "label": bed.name,
                # Trim + ducking are already baked into the wav - no level set.
                "levelDb": None,
            }
        )
    rows.sort(key=lambda r: (r["trackType"], r["trackIndex"], r["record"]))
    return rows


def _media_paths(blueprint: AssemblyBlueprint, assets: list[Path] | None) -> list[str]:
    """Every file the Media Pool needs: cut sources + extra assets + beds."""
    seen: dict[str, None] = {}
    for cut in blueprint.cuts:
        seen.setdefault(str(cut.source_file))
    for path in assets or []:
        seen.setdefault(str(path))
    return sorted(seen)


# The runtime half of the generated script. Kept as a plain constant so the
# emitted file is fully standalone - it must run inside Resolve's interpreter
# or any external Python 3.6+ with the DaVinciResolveScript module reachable.
_SCRIPT_BODY = '''

def _get_resolve():
    """Return the live `resolve` object, whichever host we run under.

    - Workspace > Scripts / the Fusion Console inject a `resolve` global -
      the only launch path on FREE Resolve (external scripting is Studio-only;
      on free 21.1 even the Scripts menu stopped listing .py files).
    - Studio + "External scripting using = Local|Network" (Preferences >
      General) lets a foreign process connect via DaVinciResolveScript.
    """
    try:
        return resolve  # noqa: F821 - injected when Resolve hosts the script
    except NameError:
        pass
    import os
    import sys

    candidates = [os.environ.get("RESOLVE_SCRIPT_API")]
    candidates += [
        # The developer Scripting/Modules folders Blackmagic installs.
        os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\\ProgramData"),
            "Blackmagic Design", "DaVinci Resolve",
            "Support", "Developer", "Scripting",
        ),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Developer/Scripting",
        "/opt/resolve/Developer/Scripting",
    ]
    for base in candidates:
        if not base:
            continue
        modules = os.path.join(base, "Modules")
        if modules not in sys.path:
            sys.path.append(modules)
    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        sys.exit(
            "Cannot import DaVinciResolveScript - set RESOLVE_SCRIPT_API/"
            "RESOLVE_SCRIPT_LIB/PYTHONPATH per the Resolve scripting README, "
            "or run this script from Resolve's Workspace > Scripts menu."
        )
    instance = dvr_script.scriptapp("Resolve")
    if instance is None:
        sys.exit(
            "scriptapp('Resolve') returned None - is Resolve running? On the "
            "FREE edition external scripting is refused (Studio-only): run "
            "from Workspace > Scripts instead."
        )
    return instance


def _media_frames(item):
    """Media length in frames for a MediaPoolItem ('to end of media')."""
    try:
        return int(float(item.GetClipProperty("Frames")))
    except Exception:
        return None


def _try_set_level(item, db):
    """Best-effort per-clip audio level (Resolve >= 21.1 only).

    SetProperty had no audio keys before 21.1 ('Volume'/'Level'/'Gain' all
    return False), so a False result just means the level stays at unity -
    the rendered beds already carry their baked trims.
    """
    if db is None or item is None:
        return
    for key in ("Volume", "AudioVolume", "Level"):
        try:
            if item.SetProperty(key, db):
                return
        except Exception:
            continue
    print("  note: clip volume not scriptable on this Resolve version "
          "(needs >= 21.1); level left at unity")


def main():
    resolve = _get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise SystemExit("No project open - open one in Resolve first.")
    project.SetSetting("timelineFrameRate", str(FPS))
    project.SetSetting("timelineResolutionWidth", "1920")
    project.SetSetting("timelineResolutionHeight", "1080")

    pool = project.GetMediaPool()
    items = {}
    for path in MEDIA:
        imported = pool.ImportMedia([path])
        if not imported:
            print("  WARNING: ImportMedia returned nothing for", path)
            continue
        items[path] = imported[0]
    print("Imported %d/%d media file(s)" % (len(items), len(MEDIA)))

    timeline = pool.CreateEmptyTimeline(TIMELINE_NAME)
    if timeline is None:
        raise SystemExit("CreateEmptyTimeline failed - name clash?")
    project.SetCurrentTimeline(timeline)
    timeline.SetStartTimecode("00:00:00:00")
    base = timeline.GetStartFrame()

    video_needed = max(
        [p["trackIndex"] for p in PLAN if p["trackType"] == "video"] or [0]
    )
    audio_needed = max(
        [p["trackIndex"] for p in PLAN if p["trackType"] == "audio"] or [0]
    )
    while timeline.GetTrackCount("video") < video_needed:
        timeline.AddTrack("video")
    while timeline.GetTrackCount("audio") < audio_needed:
        timeline.AddTrack("audio", "stereo")
    if video_needed >= 2:
        timeline.SetTrackName("video", 2, "V2 overlays")
    if audio_needed >= 2:
        timeline.SetTrackName("audio", 2, "Music")
    if audio_needed >= 3:
        timeline.SetTrackName("audio", 3, "SFX")

    placed = 0
    for p in PLAN:
        item = items.get(p["file"])
        if item is None:
            print("  skip %s - not imported" % p["label"])
            continue
        out = p["out"] if p["out"] is not None else _media_frames(item)
        clip_info = {
            "mediaPoolItem": item,
            "trackIndex": p["trackIndex"],
            "recordFrame": base + p["record"],
            "startFrame": p["in"],
            "endFrame": out,
        }
        if p["mediaType"] is not None:
            clip_info["mediaType"] = p["mediaType"]
        new_items = pool.AppendToTimeline([clip_info])
        if not new_items:
            print("  FAILED to place %s at frame %d" % (p["label"], clip_info["recordFrame"]))
            continue
        _try_set_level(new_items[0], p["levelDb"])
        placed += 1
    print("Placed %d/%d clip(s) on timeline '%s'" % (placed, len(PLAN), TIMELINE_NAME))


if __name__ == "__main__":
    main()
'''


def emit_resolve_script(
    blueprint: AssemblyBlueprint,
    assets: list[Path] | None,
    out_path: Path,
) -> Path:
    """Write `out_path` as a standalone Resolve-python timeline builder.

    `assets` are extra files to import into the Media Pool beyond the cut
    sources (e.g. the full ingest manifest so unused B-roll is at hand). The
    rendered Resolve beds (`music_bed_ducked.wav` / `sfx_bed.wav`) are picked
    up automatically when present next to `out_path` - i.e. in the project's
    output dir - and then replace that lane's per-cut audio exactly like the
    FCPXML resolve target does.
    """
    out_path = Path(out_path)
    project_dir = out_path.parent
    beds = {
        track: project_dir / name
        for track, name in (
            ("A2_MUSIC", MUSIC_BED_NAME),
            ("A3_SFX", SFX_BED_NAME),
        )
        if (project_dir / name).exists()
    }
    plan = _placements(blueprint, beds)
    media = _media_paths(blueprint, assets)
    for bed in beds.values():
        if str(bed) not in media:
            media.append(str(bed))
    media.sort()

    header = (
        '"""Generated by `directorcut conform` - build this project\'s timeline\n'
        "natively inside DaVinci Resolve (no FCPXML import).\n\n"
        "Run while Resolve is OPEN:\n"
        "  * Free Resolve: place this file under the Utility scripts folder and\n"
        "    run it from Workspace > Scripts (external scripting is Studio-only).\n"
        "  * Resolve Studio: enable Preferences > General > 'External scripting\n"
        "    using' = Local, then run `python build_timeline.py`.\n"
        '"""\n'
    )
    data = (
        f"TIMELINE_NAME = {json.dumps(blueprint.timeline_meta.project_name)}\n"
        f"FPS = {blueprint.timeline_meta.target_fps!r}\n"
        f"MEDIA = {json.dumps(media, indent=4)}\n"
        f"PLAN = {json.dumps(plan, indent=4)}\n"
    )
    out_path.write_text(header + data + _SCRIPT_BODY, encoding="utf-8")
    return out_path
