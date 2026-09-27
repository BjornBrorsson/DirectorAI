"""FCPXML 1.8 builder."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from directorcut.conformer.ducking import AUDIO_LANES, duck_keyframes, voice_ranges
from directorcut.conformer.timecode import frame_duration, to_rational
from directorcut.exceptions import ConformError
from directorcut.ffmpeg import run_ffprobe
from directorcut.ingest import AUDIO_EXTS, IMAGE_EXTS
from directorcut.log import get_logger
from directorcut.models import AssemblyBlueprint, Cut
from directorcut.utils import parse_timecode

logger = get_logger(__name__)


def _is_audio_only(path: Path) -> bool:
    """Return True if the path's extension indicates an audio-only file."""
    return path.suffix.lower() in AUDIO_EXTS


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _probe_asset(path: Path) -> dict:
    """Probe a media file for the attributes Resolve wants on <asset>.

    Returns duration_seconds, has_audio, has_video, audio_sources (stream
    count), audio_channels and audio_rate. Without `duration`/`format` on
    the asset Resolve can't map a clip's rational `start` onto frames and
    shows a frozen frame 0. Probing is cheap and files are local at conform
    time; on any failure we fall back to extension guesses.
    """
    try:
        out = run_ffprobe(
            [
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,channels,sample_rate",
                "-of",
                "json",
                str(path),
            ]
        )
        data = json.loads(out)
        streams = data.get("streams", [])
        kinds = {s.get("codec_type") for s in streams}
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        duration = data.get("format", {}).get("duration")
        return {
            "duration": float(duration) if duration else None,
            "has_audio": bool(audio_streams),
            "has_video": "video" in kinds or "image" in kinds,
            "audio_sources": len(audio_streams),
            "audio_channels": max(
                (int(s.get("channels") or 0) for s in audio_streams), default=0
            ),
            "audio_rate": int(audio_streams[0]["sample_rate"])
            if audio_streams and audio_streams[0].get("sample_rate")
            else 0,
        }
    except Exception as exc:  # noqa: BLE001 - probing is best-effort
        logger.warning("ffprobe failed for %s (%s); guessing stream types", path, exc)
        audio_only = _is_audio_only(path)
        return {
            "duration": None,
            "has_audio": not _is_image(path),
            "has_video": not audio_only,
            "audio_sources": 1 if not _is_image(path) else 0,
            "audio_channels": 2 if not _is_image(path) else 0,
            "audio_rate": 48000 if not _is_image(path) else 0,
        }


def _spine_clip_tag(cut: Cut) -> str:
    """Return the FCPXML element tag for a V1_A1_PRIMARY spine clip.

    Visual sources MUST be `<asset-clip>`: a spine-level `<video>` is
    video-only (it never carries the asset's embedded audio) and DaVinci
    Resolve renders it as a frozen still placed at the wrong offset
    (verified 2026-09-22 - see docs/MASTER_MEMORIES.md). `<video>` is only
    safe as a *connected* clip nested under a parent with a `lane`. This
    helper therefore never returns "video" - audio-only sources become
    `<audio>` and everything else stays `<asset-clip>`.
    """
    return "audio" if _is_audio_only(cut.source_file) else "asset-clip"


def _validate_spine(spine: ET.Element) -> None:
    """Fail loudly if any spine visual would resolve to a `<video>` element.

    Guardrail for the Resolve bug where spine `<video>` clips freeze and
    misplace: a `<video>` may only ever appear *nested* under a spine clip
    (a connected overlay) and must carry a `lane`. Connected children of
    `<gap>` anchors are legal too. Rather than emit a timeline Resolve
    silently renders wrong, raise ConformError at conform time.
    """
    for child in spine:
        if child.tag == "video":
            raise ConformError(
                "Spine visual would be emitted as <video> "
                f"(name={child.get('name')!r}, offset={child.get('offset')!r}). "
                "Resolve renders spine <video> elements as frozen, misplaced "
                "stills - spine visuals must be <asset-clip> (or <audio> for "
                "audio-only sources); <video> is only allowed as a connected "
                "clip nested under a parent with a lane."
            )
        for nested in child:
            if nested.tag == "video" and nested.get("lane") is None:
                raise ConformError(
                    "Connected <video> clip is missing its lane attribute "
                    f"(name={nested.get('name')!r}). Resolve needs an explicit "
                    "lane to place the overlay - refusing to emit an "
                    "ambiguous timeline."
                )


def _duration(cut: Cut, fps: float) -> str:
    start = parse_timecode(cut.source_in)
    end = parse_timecode(cut.source_out)
    seconds = end - start
    denom = int(round(fps))
    numer = int(round(seconds * fps))
    return f"{numer}/{denom}s"


def _rational_seconds(seconds: float, fps: float) -> str:
    """Convert plain seconds to an FCPXML rational time string."""
    denom = int(round(fps))
    numer = int(round(seconds * fps))
    return f"{numer}/{denom}s"


def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ConformError(
            f"Cut source file must be an absolute path to produce a valid FCPXML "
            f"file:// URI, got: {path!r}. Ingest should resolve paths to absolute "
            f"before building the assembly blueprint (see directorcut.ingest.ingest())."
        )


def _asset_id_for(
    path: Path,
    assets: dict[str, str],
    resources: ET.Element,
    fmt_id: str,
    fps: float,
) -> str:
    """Return (creating if needed) a deduplicated asset id for `path`."""
    key = str(path)
    if key in assets:
        return assets[key]
    asset_id = f"a{len(assets) + 1}"
    assets[key] = asset_id
    info = _probe_asset(path)
    attrs = {
        "id": asset_id,
        "name": path.name,
        "src": f"file:///{path.as_posix()}",
        "start": "0s",
        "hasAudio": "1" if info["has_audio"] else "0",
        "hasVideo": "1" if info["has_video"] else "0",
    }
    if info["duration"] is not None:
        attrs["duration"] = _rational_seconds(info["duration"], fps)
    if info["has_video"]:
        attrs["format"] = fmt_id
    if info["audio_sources"]:
        attrs["audioSources"] = str(info["audio_sources"])
        attrs["audioChannels"] = str(info["audio_channels"])
        if info["audio_rate"]:
            attrs["audioRate"] = str(info["audio_rate"])
    ET.SubElement(resources, "asset", attrs)
    return asset_id


def _rational_to_seconds(tc: str) -> float:
    """Parse an FCPXML rational time (`123/30s` or `10s`) back to seconds."""
    num, _, den = tc.rstrip("s").partition("/")
    return int(num) / int(den) if den else float(num)


def build_fcpxml(
    blueprint: AssemblyBlueprint,
    *,
    target: str = "fcpx",
    beds: dict[str, Path] | None = None,
) -> str:
    """Build a DaVinci Resolve-compatible FCPXML 1.8 document.

    `V1_A1_PRIMARY` cuts are emitted as the main spine clips: `<asset-clip>`
    for visual sources (carries the file's embedded audio - a plain `<video>`
    element is video-only and imports silent) and `<audio>` for audio-only
    sources. `V2_OVERLAY_BROLL` cuts are
    nested inside the primary clip whose range contains their `timeline_in`
    as a connected clip (`lane="1"`), which is how FCPXML represents a
    secondary/overlay storyline item. Per the FCPXML 1.8 spec, a connected
    clip's `offset` is expressed in its *parent* clip's timebase - the
    parent's `start` plus the child's local offset - not relative to the
    sequence or to the parent's position on it. `A2_MUSIC`/`A3_SFX` cuts
    work the same way but as `<audio>` on lanes -2/-3. Any
    connected cut with no primary clip containing its `timeline_in` falls
    back to a synthetic `<gap>` anchor so it isn't silently dropped.

    `target` selects the audio-ducking flavour:

    - ``"fcpx"`` (default): A2/A3 clips carry a `volume` keyframeAnimation
      computed from the voice ranges - legal FCPXML, editable in FCP X.
    - ``"premiere"``: same document shape as ``"fcpx"`` (Premiere Pro
      imports FCPXML). TODO: Premiere's handling of `<param>` volume
      keyframes, connected-clip offsets, and `<gap>` anchors is UNVERIFIED
      - see docs/wiki/nle_targets.md for the quirk list.
    - ``"resolve"``: Resolve ignores `<param name="volume">` entirely, so no
      `<param>` elements are ever emitted. Lanes covered by `beds`
      (`{"A2_MUSIC": music_bed_ducked.wav, "A3_SFX": sfx_bed.wav}` from
      `conformer.mixdown.render_mixdown`) are replaced by ONE `<audio>`
      bed clip each, attached to the first spine clip and spanning the
      rendered bed. A lane that has cuts but no rendered bed still emits
      its per-cut `<audio>` children - un-ducked, but audible rather than
      silently missing.
    """
    fps = blueprint.timeline_meta.target_fps
    project_name = blueprint.timeline_meta.project_name
    # premiere shares the fcpx document shape until its quirks are verified.
    emit_ducking = target in ("fcpx", "premiere")
    bedded_tracks = set(beds) if target == "resolve" and beds else set()

    for cut in blueprint.cuts:
        _require_absolute(cut.source_file)

    root = ET.Element("fcpxml", version="1.8")
    resources = ET.SubElement(root, "resources")

    fmt_id = "r1"
    ET.SubElement(
        resources,
        "format",
        id=fmt_id,
        name=f"FFVideoFormat1080p{int(fps)}",
        frameDuration=frame_duration(fps),
    )

    assets: dict[str, str] = {}
    for cut in blueprint.cuts:
        _asset_id_for(cut.source_file, assets, resources, fmt_id, fps)

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="DirectorCut_Output")
    project = ET.SubElement(event, "project", name=project_name)
    timeline_end = max(
        (parse_timecode(c.timeline_out) for c in blueprint.cuts), default=0.0
    )
    # tcStart is required: without it Resolve defaults to a ~1h start
    # timecode, our offsets land "before" the timeline, and clips get placed
    # by source time instead - observed as ~1.5h of silence then the real
    # cut (2026-09-20). duration/tcFormat/audio* match Resolve's own exports.
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": fmt_id,
            "duration": _rational_seconds(timeline_end, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    primary_cuts = [c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"]
    unmatched_broll = [c for c in blueprint.cuts if c.track == "V2_OVERLAY_BROLL"]
    # Lanes covered by a rendered bed are replaced by a single clip - their
    # per-cut children would double the audio the bed already carries.
    audio_cuts = [
        c
        for c in blueprint.cuts
        if c.track in AUDIO_LANES and c.track not in bedded_tracks
    ]
    if target == "resolve":
        unbedded = {c.track for c in audio_cuts}
        if unbedded:
            logger.warning(
                "Resolve target without rendered beds for %s - emitting "
                "un-ducked per-cut audio (Resolve ignores volume keyframes)",
                sorted(unbedded),
            )
    voice = voice_ranges(blueprint)

    def attach_video(
        parent: ET.Element,
        cut: Cut,
        parent_timeline_in: float,
        parent_start: float,
    ) -> None:
        local = max(0.0, parse_timecode(cut.timeline_in) - parent_timeline_in)
        ET.SubElement(
            parent,
            "video",
            ref=assets[str(cut.source_file)],
            lane="1",
            offset=_rational_seconds(parent_start + local, fps),
            name=cut.source_file.name,
            start=to_rational(cut.source_in, fps),
            duration=_duration(cut, fps),
            enabled="1",
            format=fmt_id,
            tcFormat="NDF",
        )

    def attach_audio(
        parent: ET.Element,
        cut: Cut,
        parent_timeline_in: float,
        parent_start: float,
    ) -> None:
        local = max(0.0, parse_timecode(cut.timeline_in) - parent_timeline_in)
        el = ET.SubElement(
            parent,
            "audio",
            ref=assets[str(cut.source_file)],
            lane=AUDIO_LANES[cut.track],
            offset=_rational_seconds(parent_start + local, fps),
            name=cut.source_file.name,
            start=to_rational(cut.source_in, fps),
            duration=_duration(cut, fps),
            enabled="1",
            tcFormat="NDF",
        )
        if not emit_ducking:
            return
        keyframes = duck_keyframes(cut, voice)
        if keyframes:
            param = ET.SubElement(el, "param", name="volume")
            anim = ET.SubElement(param, "keyframeAnimation")
            for t, value in keyframes:
                ET.SubElement(
                    anim,
                    "keyframe",
                    time=_rational_seconds(t, fps),
                    value=f"{value:g}",
                )

    for cut in primary_cuts:
        ref = assets[str(cut.source_file)]
        offset = to_rational(cut.timeline_in, fps)
        start = to_rational(cut.source_in, fps)
        duration = _duration(cut, fps)
        tag = _spine_clip_tag(cut)
        clip_attrs = {
            "ref": ref,
            "offset": offset,
            "name": cut.source_file.name,
            "start": start,
            "duration": duration,
            "enabled": "1",
            "tcFormat": "NDF",
        }
        if tag == "asset-clip":
            clip_attrs["format"] = fmt_id
        clip_el = ET.SubElement(spine, tag, clip_attrs)

        # A connected clip anchors to the primary clip containing its start;
        # it may extend past the parent's end - FCP allows the overflow.
        primary_in = parse_timecode(cut.timeline_in)
        primary_out = parse_timecode(cut.timeline_out)
        # Connected-clip offsets live in the parent's timebase, so children
        # of this clip anchor at the parent's `start` (its source_in).
        primary_start = parse_timecode(cut.source_in)

        matched = [
            b
            for b in unmatched_broll
            if primary_in <= parse_timecode(b.timeline_in) < primary_out
        ]
        for broll in matched:
            attach_video(clip_el, broll, primary_in, primary_start)
            unmatched_broll.remove(broll)

        anchored = [
            a
            for a in audio_cuts
            if primary_in <= parse_timecode(a.timeline_in) < primary_out
        ]
        for acut in anchored:
            attach_audio(clip_el, acut, primary_in, primary_start)
            audio_cuts.remove(acut)

    orphans = sorted(
        unmatched_broll + audio_cuts, key=lambda c: parse_timecode(c.timeline_in)
    )
    for orphan in orphans:
        logger.warning(
            "Connected cut %s had no primary clip containing %s; anchoring to a gap",
            orphan.source_file,
            orphan.timeline_in,
        )
        gap_duration = _duration(orphan, fps)
        gap_el = ET.SubElement(
            spine,
            "gap",
            offset=to_rational(orphan.timeline_in, fps),
            name="gap",
            duration=gap_duration,
        )
        # A gap has no `start` (implicitly 0), so gap children use a plain
        # local offset.
        if orphan.track in AUDIO_LANES:
            attach_audio(gap_el, orphan, parse_timecode(orphan.timeline_in), 0.0)
        else:
            attach_video(gap_el, orphan, parse_timecode(orphan.timeline_in), 0.0)

    # Resolve target: attach each rendered bed as ONE connected <audio> clip
    # under the first spine clip. The bed file is rendered timeline-aligned
    # (position p in the file == timeline position p), so the child starts
    # at media position 0 and its offset is the parent's `start` in the
    # parent timebase. If the first clip doesn't sit at timeline 0 the bed's
    # head would precede the parent, so the clip starts at the parent's
    # timeline_in instead and the bed plays from that media position.
    if bedded_tracks:
        first_clip = next((c for c in spine if c.tag != "gap"), None)
        if first_clip is None:
            logger.warning(
                "No spine clip to anchor audio beds to - beds dropped"
            )
        else:
            parent_start = _rational_to_seconds(first_clip.get("start") or "0s")
            primary_in = _rational_to_seconds(first_clip.get("offset") or "0s")
            attach_at = max(0.0, primary_in)
            if attach_at > 0.0:
                logger.warning(
                    "First spine clip starts at %.3fs - beds will begin at "
                    "that point (their first %.3fs is dropped)",
                    primary_in,
                    primary_in,
                )
            for track in ("A2_MUSIC", "A3_SFX"):
                bed = beds.get(track) if beds else None
                if bed is None:
                    continue
                aid = _asset_id_for(bed, assets, resources, fmt_id, fps)
                bed_len = _probe_asset(bed)["duration"] or 0.0
                ET.SubElement(
                    first_clip,
                    "audio",
                    {
                        "ref": aid,
                        "lane": AUDIO_LANES[track],
                        "offset": _rational_seconds(
                            parent_start + (attach_at - primary_in), fps
                        ),
                        "name": bed.name,
                        "start": _rational_seconds(attach_at, fps),
                        "duration": _rational_seconds(
                            max(0.0, bed_len - attach_at), fps
                        ),
                        "enabled": "1",
                        "tcFormat": "NDF",
                    },
                )

    # Drop assets nothing references anymore (e.g. the music/SFX sources a
    # rendered bed replaced) - dangling assets confuse Resolve's relink UI.
    referenced = {el.get("ref") for el in spine.iter() if el.get("ref")}
    for asset in list(resources.findall("asset")):
        if asset.get("id") not in referenced:
            resources.remove(asset)

    # Guardrail: fail loudly instead of shipping a timeline Resolve renders
    # wrong (spine <video> freezes/misplaces - verified 2026-09-22).
    _validate_spine(spine)
    if target == "resolve" and root.find(".//param") is not None:
        # Resolve ignores <param> automation wholesale - emitting any means
        # the level ride silently does nothing, which is worse than failing.
        raise ConformError(
            "Resolve-target FCPXML must not contain <param> elements - "
            "bake level rides into the audio beds instead (see "
            "conformer.mixdown)."
        )

    rough = ET.tostring(root, encoding="unicode")
    reparsed = minidom.parseString(rough)
    pretty = reparsed.toprettyxml(indent="    ")
    # Drop the default XML declaration and re-add a clean one + the DOCTYPE
    # Resolve's own exports carry.
    lines = [line for line in pretty.splitlines() if line.strip()]
    if lines and lines[0].startswith('<?xml'):
        lines = lines[1:]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n" + "\n".join(lines)
    )
