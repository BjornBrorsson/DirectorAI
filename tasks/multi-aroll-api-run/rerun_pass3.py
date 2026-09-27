"""Re-run Pass 3 (director assembly) only, for an existing project.

Reuses the saved ingest manifest, narrative nodes, and visual events -
no ingest/STT/visual work is repeated. The manifest's direction is
overridden with the current ingest/direction.txt.

If the director model 503s after the client's own retries, the whole
call is retried up to 3 times with 2-minute waits, then falls back to
FALLBACK_MODEL for this call only.

Generated SFX/music cues whose audio file already exists under
<project>/audio/<cue_id>.mp3 are reused instead of re-rendered.

Run from repo root:
    python tasks/multi-aroll-api-run/rerun_pass3.py output/sts2-review-take2
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from directorcut.assembly.director import (
    PROMPT,
    _AssemblyPlan,
    _fake_plan,
    _inventory_lines,
    _narrative_block,
    _plan_to_blueprint,
    run_director,
)
from directorcut.audio_design import render_cues
from directorcut.config import Settings
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.exceptions import GeminiError
from directorcut.gemini_client import GeminiClient
from directorcut.ingest import _probe_duration
from directorcut.models import (
    AssemblyBlueprint,
    AudioCueKind,
    Cut,
    IngestManifest,
    NarrativeNode,
    VisualEvent,
)
from directorcut.utils import format_timecode, parse_timecode

REPO = Path(__file__).resolve().parents[2]
FALLBACK_MODEL = "gemini-3-flash-preview"
MAX_ATTEMPTS = 3
RETRY_WAIT_S = 120


def _run_director_with_model(
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
    settings: Settings,
    model: str,
) -> AssemblyBlueprint:
    """run_director() with an explicit model override (fallback path)."""
    client = GeminiClient(settings)
    prompt = PROMPT.format(
        direction=manifest.direction or "",
        assets=_inventory_lines(manifest),
        narrative=_narrative_block(nodes),
        visual=json.dumps([e.model_dump(mode="json") for e in events], indent=2),
    )
    plan = client.call(
        prompt=prompt,
        media=None,
        response_model=_AssemblyPlan,
        dev_mode_override=_fake_plan(manifest, nodes, events),
        model=model,
    )
    return _plan_to_blueprint(plan, manifest, nodes, events)


def _cue_cut(cue, audio_file: Path) -> Cut:
    """Build the A2/A3 cut for a cue whose audio file already exists."""
    start = parse_timecode(cue.timeline_in)
    want = max(0.5, parse_timecode(cue.timeline_out) - start)
    actual = _probe_duration(audio_file) or want
    source_out = min(actual, want)
    return Cut(
        track="A2_MUSIC" if cue.kind == AudioCueKind.MUSIC else "A3_SFX",
        source_file=audio_file.resolve(),
        timeline_in=cue.timeline_in,
        timeline_out=format_timecode(start + source_out),
        source_in="00:00:00.000",
        source_out=format_timecode(source_out),
    )


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    settings = Settings()

    # model_validate() not _json: the manifest's before-validator coerces
    # str->Path, and strict JSON-mode Path validation rejects Path objects.
    manifest = IngestManifest.model_validate(
        json.loads((out_dir / "assets.json").read_text(encoding="utf-8"))
    )
    nodes = [
        NarrativeNode.model_validate(n)
        for n in json.loads((out_dir / "narrative.json").read_text(encoding="utf-8"))
    ]
    events = [
        VisualEvent.model_validate(e)
        for e in json.loads((out_dir / "visual.json").read_text(encoding="utf-8"))
    ]
    direction = (REPO / "ingest" / "direction.txt").read_text(encoding="utf-8")
    manifest = manifest.model_copy(update={"direction": direction})
    print(
        f"loaded: {len(manifest.assets)} assets, {len(nodes)} nodes, "
        f"{len(events)} events; director model={settings.model_for('director')}"
    )

    blueprint: AssemblyBlueprint | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            blueprint = run_director(manifest, nodes, events, settings)
            break
        except GeminiError as exc:
            print(f"director attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_S)
    if blueprint is None:
        print(f"falling back to {FALLBACK_MODEL} for the director call")
        blueprint = _run_director_with_model(
            manifest, nodes, events, settings, FALLBACK_MODEL
        )

    print(
        f"director: {len(blueprint.cuts)} cuts, "
        f"{len(blueprint.audio_cues)} audio cue(s)"
    )

    # Reuse already-rendered cue audio; only new cue_ids hit ElevenLabs.
    audio_dir = out_dir / "audio"
    cue_cuts: list[Cut] = []
    pending = []
    for cue in blueprint.audio_cues:
        existing = audio_dir / f"{cue.cue_id}.mp3"
        if existing.exists():
            print(f"reusing rendered cue {existing.name}")
            cue_cuts.append(_cue_cut(cue, existing))
        else:
            pending.append(cue)
    cue_cuts += render_cues(pending, out_dir, settings)
    if cue_cuts:
        blueprint = blueprint.model_copy(
            update={"cuts": [*blueprint.cuts, *cue_cuts]}
        )

    xml = build_fcpxml(blueprint)
    xml_path = out_dir / f"{out_dir.name}.fcpxml"
    xml_path.write_text(xml, encoding="utf-8")
    (out_dir / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"wrote {xml_path} ({len(blueprint.cuts)} cuts)")


if __name__ == "__main__":
    main()
