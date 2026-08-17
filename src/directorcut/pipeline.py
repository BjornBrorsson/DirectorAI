"""End-to-end DirectorCut-AI pipeline."""

from __future__ import annotations

from pathlib import Path

from directorcut.assembly.crossref import match_events
from directorcut.assembly.cuts import generate_cuts
from directorcut.config import Settings
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.ingest import ingest
from directorcut.log import get_logger
from directorcut.narrative.ingest import load_narrative_input
from directorcut.narrative.tagger import tag_narrative
from directorcut.narrative.transcript import transcribe
from directorcut.proxy.audio import extract_audio
from directorcut.proxy.visual import generate_visual_proxy
from directorcut.visual.chunked import analyze_visual_proxy

logger = get_logger(__name__)


def run_pipeline(settings: Settings, project_name: str) -> dict:
    """Run the full pipeline from ingestion to FCPXML."""
    manifest = ingest(settings.ingest_dir, project_name)
    output_dir = settings.output_dir / project_name
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[proxy] Extracting audio and visual proxies")
    audio_source = manifest.audio_path or manifest.video_path
    audio_proxy = extract_audio(audio_source, output_dir) if audio_source else None
    video_proxy = (
        generate_visual_proxy(manifest.video_path, output_dir)
        if manifest.video_path
        else None
    )

    logger.info("[pass1] Narrative mapping")
    nar_input = load_narrative_input(manifest.script_path, audio_proxy)
    nodes = transcribe(nar_input.text, nar_input.audio_path)
    nodes = tag_narrative(nodes, settings)

    logger.info("[pass2] Visual logging")
    from directorcut.ffmpeg import run_ffprobe

    duration = 0.0
    if video_proxy:
        probe = run_ffprobe([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_proxy),
        ])
        duration = float(probe.strip())
    events = analyze_visual_proxy(video_proxy, duration, settings) if video_proxy else []

    logger.info("[pass3] Assembly")
    matches = match_events(nodes, events)
    blueprint = generate_cuts(
        project_name=project_name,
        nodes=nodes,
        events=events,
        matches=matches,
        video_source=manifest.video_path or Path("video.mp4"),
        audio_source=manifest.audio_path or manifest.video_path or Path("audio.mp3"),
        target_fps=manifest.target_fps,
    )

    logger.info("[conform] Building FCPXML")
    xml = build_fcpxml(blueprint)
    xml_path = output_dir / f"{project_name}.fcpxml"
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)

    # Save intermediate JSONs.
    import json

    (output_dir / "narrative.json").write_text(
        json.dumps([n.model_dump(mode="json") for n in nodes], indent=2), encoding="utf-8"
    )
    (output_dir / "visual.json").write_text(
        json.dumps([e.model_dump(mode="json") for e in events], indent=2), encoding="utf-8"
    )
    (output_dir / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )

    logger.info("Done. FCPXML: %s", xml_path)
    return {
        "manifest": manifest,
        "nodes": nodes,
        "events": events,
        "blueprint": blueprint,
        "xml_path": xml_path,
        "output_dir": output_dir,
    }
