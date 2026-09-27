"""Finalize step: render audio beds (Resolve target) and build the FCPXML.

Shared by `pipeline.run_pipeline` and the `directorcut cut` patch commands
so a blueprint re-conform always produces the same document the original
run would have written.
"""

from __future__ import annotations

import json
from pathlib import Path

from directorcut.config import Settings
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.conformer.mixdown import render_mixdown
from directorcut.export_pack import write_export_pack
from directorcut.log import get_logger
from directorcut.models import AssemblyBlueprint

logger = get_logger(__name__)


def finalize_blueprint(
    blueprint: AssemblyBlueprint,
    output_dir: Path,
    settings: Settings | None = None,
) -> Path:
    """Render beds if needed, build FCPXML, write `<project>.fcpxml`.

    For `fcpxml_target="resolve"` the mixdown stage renders
    `music_bed_ducked.wav` / `sfx_bed.wav` into `output_dir` and the
    conformer attaches them as single bed clips instead of per-cut audio
    with `<param>` keyframes (which Resolve ignores). For `"fcpx"` and
    `"premiere"` the keyframe-ducking path is emitted directly (Premiere
    support unverified - see docs/wiki/nle_targets.md).
    """
    target = settings.fcpxml_target if settings else "fcpx"
    beds = None
    if target == "resolve":
        beds = render_mixdown(blueprint, output_dir, settings)
    xml = build_fcpxml(blueprint, target=target, beds=beds)
    xml_path = output_dir / f"{output_dir.name}.fcpxml"
    xml_path.write_text(xml, encoding="utf-8")
    logger.info("FCPXML written: %s", xml_path)

    # Creator export pack (captions/chapters/thumbnails) tracks the
    # blueprint - refreshed on every conform incl. `cut` re-runs. Strictly
    # best-effort: a pack failure must never sink an otherwise-good conform.
    try:
        written = write_export_pack(output_dir, blueprint=blueprint, settings=settings)
        if written:
            logger.info(
                "Export pack: %d file(s) under %s", len(written), output_dir / "pack"
            )
    except Exception:  # noqa: BLE001
        logger.warning("Export pack generation failed", exc_info=True)
    return xml_path


def finalize_project(
    project_dir: Path,
    settings: Settings | None = None,
) -> Path:
    """Load `project_dir/blueprint.json` and re-run finalize on it."""
    blueprint = AssemblyBlueprint.model_validate(
        json.loads((project_dir / "blueprint.json").read_text(encoding="utf-8"))
    )
    return finalize_blueprint(blueprint, project_dir, settings)
