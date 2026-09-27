"""Reconform the StS2 review FCPXML from the saved blueprint.

Regenerates output/sts2-ea-review/sts2-ea-review.fcpxml in place: the v1
file was built with parent-local connected-clip offsets, which Resolve
interprets in the parent's timebase and lands minutes early. The conformer
now emits parent `start` + local, so this just rebuilds from blueprint.json.

Run from repo root:
    python tasks/sts2-ea-review-run/reconform.py
"""

from pathlib import Path

from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.models import AssemblyBlueprint

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "output" / "sts2-ea-review"


def main() -> None:
    blueprint = AssemblyBlueprint.model_validate_json(
        (OUT / "blueprint.json").read_text(encoding="utf-8")
    )
    xml = build_fcpxml(blueprint)
    dst = OUT / "sts2-ea-review.fcpxml"
    dst.write_text(xml, encoding="utf-8")
    print(f"wrote {dst} ({len(blueprint.cuts)} cuts)")


if __name__ == "__main__":
    main()
