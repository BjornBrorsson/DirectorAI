"""Re-normalize the take2 blueprint and rebuild its FCPXML.

The pipeline run emitted one V1 cut whose timeline span (4.019s) did not
match its source span (3.319s) - a model arithmetic slip. _enforce_
unique_source used to preserve the timeline span when re-laying cuts,
leaving a 0.7s hole in the conformed spine. It now re-lays by source
span; this script re-applies that normalization to the saved
blueprint.json (nodes are unavailable so text-dedup is a no-op - the run
already produced zero drops) and rebuilds the FCPXML.

Run from repo root:
    python tasks/multi-aroll-api-run/reconform.py output/sts2-review-take2
"""

from __future__ import annotations

import sys
from pathlib import Path

from directorcut.assembly.director import _enforce_unique_source
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.models import AssemblyBlueprint


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    blueprint = AssemblyBlueprint.model_validate_json(
        (out_dir / "blueprint.json").read_text(encoding="utf-8")
    )
    blueprint.cuts = _enforce_unique_source(list(blueprint.cuts), [])
    (out_dir / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )
    xml = build_fcpxml(blueprint)
    dst = out_dir / f"{out_dir.name}.fcpxml"
    dst.write_text(xml, encoding="utf-8")
    print(f"wrote {dst} ({len(blueprint.cuts)} cuts)")


if __name__ == "__main__":
    main()
