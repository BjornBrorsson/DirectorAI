"""FCPXML 1.8 builder."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from directorcut.conformer.timecode import frame_duration, to_rational
from directorcut.exceptions import ConformError
from directorcut.ingest import AUDIO_EXTS
from directorcut.log import get_logger
from directorcut.models import AssemblyBlueprint, Cut
from directorcut.utils import parse_timecode

logger = get_logger(__name__)


def _is_audio_only(path: Path) -> bool:
    """Return True if the path's extension indicates an audio-only file."""
    return path.suffix.lower() in AUDIO_EXTS


def _duration(cut: Cut, fps: float) -> str:
    start = parse_timecode(cut.source_in)
    end = parse_timecode(cut.source_out)
    seconds = end - start
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


def _asset_id_for(path: Path, assets: dict[str, str], resources: ET.Element) -> str:
    """Return (creating if needed) a deduplicated asset id for `path`."""
    key = str(path)
    if key in assets:
        return assets[key]
    asset_id = f"a{len(assets) + 1}"
    assets[key] = asset_id
    is_audio = _is_audio_only(path)
    ET.SubElement(
        resources,
        "asset",
        id=asset_id,
        name=path.name,
        src=f"file:///{path.as_posix()}",
        start="0s",
        hasAudio="1",
        hasVideo="0" if is_audio else "1",
    )
    return asset_id


def build_fcpxml(blueprint: AssemblyBlueprint) -> str:
    """Build a DaVinci Resolve-compatible FCPXML 1.8 document.

    `V1_A1_PRIMARY` cuts are emitted as the main spine clips (as `<audio>` if
    their source is audio-only, else `<video>`). `V2_OVERLAY_BROLL` cuts that
    start at the same timeline position as a primary clip are nested inside
    it as a connected clip (`lane="1"`), which is how FCPXML represents a
    secondary/overlay storyline item. Per the FCPXML 1.8 spec, a connected
    clip's `offset` is relative to its *parent* clip's local start, not the
    sequence - so a B-roll starting together with its primary clip gets
    `offset="0s"` here even though its own `timeline_in` is an absolute
    sequence position. Any B-roll with no matching primary clip at the same
    position falls back to a synthetic `<gap>` anchor so it isn't silently
    dropped.
    """
    fps = blueprint.timeline_meta.target_fps
    project_name = blueprint.timeline_meta.project_name

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
        _asset_id_for(cut.source_file, assets, resources)

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="DirectorCut_Output")
    project = ET.SubElement(event, "project", name=project_name)
    sequence = ET.SubElement(project, "sequence", format=fmt_id)
    spine = ET.SubElement(sequence, "spine")

    primary_cuts = [c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"]
    unmatched_broll = [c for c in blueprint.cuts if c.track == "V2_OVERLAY_BROLL"]

    for cut in primary_cuts:
        ref = assets[str(cut.source_file)]
        offset = to_rational(cut.timeline_in, fps)
        start = to_rational(cut.source_in, fps)
        duration = _duration(cut, fps)
        tag = "audio" if _is_audio_only(cut.source_file) else "video"
        clip_el = ET.SubElement(
            spine,
            tag,
            ref=ref,
            offset=offset,
            name=cut.source_file.name,
            start=start,
            duration=duration,
        )

        matched = [b for b in unmatched_broll if b.timeline_in == cut.timeline_in]
        for broll in matched:
            broll_ref = assets[str(broll.source_file)]
            ET.SubElement(
                clip_el,
                "video",
                ref=broll_ref,
                lane="1",
                offset="0s",
                name=broll.source_file.name,
                start=to_rational(broll.source_in, fps),
                duration=_duration(broll, fps),
            )
            unmatched_broll.remove(broll)

    for broll in unmatched_broll:
        logger.warning(
            "B-roll cut %s had no matching primary clip at %s; anchoring to a gap",
            broll.source_file,
            broll.timeline_in,
        )
        broll_duration = _duration(broll, fps)
        gap_el = ET.SubElement(
            spine,
            "gap",
            offset=to_rational(broll.timeline_in, fps),
            name="gap",
            duration=broll_duration,
        )
        broll_ref = assets[str(broll.source_file)]
        ET.SubElement(
            gap_el,
            "video",
            ref=broll_ref,
            lane="1",
            offset="0s",
            name=broll.source_file.name,
            start=to_rational(broll.source_in, fps),
            duration=broll_duration,
        )

    rough = ET.tostring(root, encoding="unicode")
    reparsed = minidom.parseString(rough)
    pretty = reparsed.toprettyxml(indent="    ")
    # Drop the default XML declaration and re-add a clean one.
    lines = [line for line in pretty.splitlines() if line.strip()]
    if lines and lines[0].startswith('<?xml'):
        lines = lines[1:]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)
