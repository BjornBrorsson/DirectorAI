"""Post-Pass-3 quality report for sts2-review-take2.

Prints timeline/coverage/coverage-of-secondary stats and writes a
per-V1-cut listing (timeline / duration / source range / covered words)
to <project>/v1_listing.txt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from directorcut.models import NarrativeNode
from directorcut.utils import parse_timecode

V1 = "V1_A1_PRIMARY"
V2 = "V2_OVERLAY_BROLL"


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    bp = json.loads((out_dir / "blueprint.json").read_text(encoding="utf-8"))
    nodes = [
        NarrativeNode.model_validate(n)
        for n in json.loads((out_dir / "narrative.json").read_text(encoding="utf-8"))
    ]
    cuts = bp["cuts"]
    v1 = sorted(
        (c for c in cuts if c["track"] == V1),
        key=lambda c: parse_timecode(c["timeline_in"]),
    )
    v2 = [c for c in cuts if c["track"] == V2]

    tl_end = max(parse_timecode(c["timeline_out"]) for c in cuts)
    v1_end = max(parse_timecode(c["timeline_out"]) for c in v1)
    durs = [
        parse_timecode(c["timeline_out"]) - parse_timecode(c["timeline_in"])
        for c in v1
    ]

    # V2 coverage of the V1 runtime (union of overlay spans).
    spans = sorted(
        (parse_timecode(c["timeline_in"]), parse_timecode(c["timeline_out"]))
        for c in v2
    )
    covered = 0.0
    cur_s = cur_e = None
    for s, e in spans:
        if cur_s is None or s > cur_e:
            if cur_s is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_s is not None:
        covered += cur_e - cur_s

    secondary = [c for c in v1 if "secondary" in Path(c["source_file"]).name]
    sec_fully_covered = 0
    for c in secondary:
        ci, co = parse_timecode(c["timeline_in"]), parse_timecode(c["timeline_out"])
        # union of overlay spans intersected with the cut
        segs = sorted(
            (max(s, ci), min(e, co)) for s, e in spans if s < co and e > ci
        )
        cov = 0.0
        ce = ci
        for s, e in segs:
            if s > ce:
                break
            cov += e - max(s, ce)
            ce = max(ce, e)
        if cov >= (co - ci) - 0.05:
            sec_fully_covered += 1

    print(f"timeline length: {tl_end:.3f}s ({tl_end/60:.2f} min)")
    print(f"V1 cuts: {len(v1)}  V2 cuts: {len(v2)}")
    print(f"V1 runtime: {v1_end:.1f}s  V2 coverage: {covered:.1f}s "
          f"({100 * covered / v1_end:.0f}%)")
    print(f"V1 from secondary take: {len(secondary)}, "
          f"fully covered by V2: {sec_fully_covered}")
    print(f"min V1 duration: {min(durs):.3f}s  V1 < 1.0s: "
          f"{sum(1 for d in durs if d < 1.0)}")

    lines = []
    for c in v1:
        si, so = parse_timecode(c["source_in"]), parse_timecode(c["source_out"])
        words = " ".join(
            n.spoken_text
            for n in nodes
            if Path(n.source_file or "") == Path(c["source_file"])
            and parse_timecode(n.time_start) >= si - 0.15
            and parse_timecode(n.time_end) <= so + 0.15
        )
        dur = parse_timecode(c["timeline_out"]) - parse_timecode(c["timeline_in"])
        lines.append(
            f"{c['timeline_in']}-{c['timeline_out']} ({dur:5.1f}s) "
            f"src {c['source_in']}-{c['source_out']} "
            f"{Path(c['source_file']).name}: {words[:110]}"
        )
    listing = out_dir / "v1_listing.txt"
    listing.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {listing}")


if __name__ == "__main__":
    main()
