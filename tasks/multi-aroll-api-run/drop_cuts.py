"""Drop V1 cuts by 1-based timeline-order index and re-finalize a project.

    python tasks/multi-aroll-api-run/drop_cuts.py <project_dir> <n> [<n>...]

Indices match the row numbers in the project's v1_listing.txt. Drops go
through compact_timeline() so the spine re-lays gaplessly and the other
tracks shift; then blueprint.json + FCPXML are rewritten and the audio
beds / final FCPXML / listing are regenerated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from directorcut.assembly.director import compact_timeline
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.models import AssemblyBlueprint, NarrativeNode
from directorcut.utils import parse_timecode

TASK_DIR = Path(__file__).resolve().parent


def _run(script: str, project: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(TASK_DIR / script), str(project)],
        capture_output=True,
        text=True,
    )
    print(proc.stdout, end="")
    if proc.returncode != 0:
        sys.exit(f"{script} failed:\n{proc.stderr[-2000:]}")


def main() -> None:
    project = Path(sys.argv[1])
    indices = {int(a) for a in sys.argv[2:]}
    if not indices:
        sys.exit("usage: drop_cuts.py <project_dir> <n> [<n>...]")

    blueprint = AssemblyBlueprint.model_validate(
        json.loads((project / "blueprint.json").read_text(encoding="utf-8"))
    )
    nodes = [
        NarrativeNode.model_validate(n)
        for n in json.loads((project / "narrative.json").read_text(encoding="utf-8"))
    ]

    v1 = sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    dropped = []
    for n in sorted(indices):
        cut = v1[n - 1]
        text = " ".join(
            node.spoken_text
            for node in nodes
            if node.source_file == cut.source_file
            and parse_timecode(node.time_start) >= parse_timecode(cut.source_in) - 0.15
            and parse_timecode(node.time_end) <= parse_timecode(cut.source_out) + 0.15
        )
        print(
            f"dropping V1 #{n}: {cut.timeline_in}-{cut.timeline_out} "
            f"src {cut.source_in}-{cut.source_out} "
            f"{cut.source_file.name}: {text[:90]}"
        )
        dropped.append(cut)

    blueprint.cuts = compact_timeline(list(blueprint.cuts), dropped)
    (project / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )
    xml_path = project / f"{project.name}.fcpxml"
    xml_path.write_text(build_fcpxml(blueprint), encoding="utf-8")
    print(f"wrote {xml_path} ({len(blueprint.cuts)} cuts)")

    for script in (
        "render_audio.py",
        "finalize_fcpxml.py",
        "verify_final.py",
        "postcheck_pass3.py",
    ):
        _run(script, project)


if __name__ == "__main__":
    main()
