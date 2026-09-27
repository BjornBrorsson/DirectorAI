"""Verify sts2-review-take2-final.fcpxml before Resolve import."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

FPS = 30


def parse_rat(s: str) -> float:
    num, _, den = s.rstrip("s").partition("/")
    return int(num) / int(den or 1)


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    tree = ET.parse(out_dir / f"{out_dir.name}-final.fcpxml")
    root = tree.getroot()
    resources = root.find("resources")
    spine = root.find(".//spine")
    assets = {a.get("id"): a.get("name") for a in resources.findall("asset")}

    clips = list(spine)
    print(f"spine clips: {len(clips)}  tags: {sorted({c.tag for c in clips})}")

    # Spine contiguity.
    gaps = []
    for prev, cur in zip(clips, clips[1:], strict=False):
        expected = parse_rat(prev.get("offset")) + parse_rat(prev.get("duration"))
        got = parse_rat(cur.get("offset"))
        if abs(got - expected) > 1 / FPS + 1e-6:
            gaps.append((prev.get("offset"), cur.get("offset"), got - expected))
    print(f"spine gaps > 1 frame: {len(gaps)} {gaps[:5]}")

    # Connected children inside parents; lane/param census.
    lanes = Counter()
    bad = []
    for parent in clips:
        p_start = parse_rat(parent.get("start"))
        p_dur = parse_rat(parent.get("duration"))
        for ch in parent:
            lanes[ch.get("lane") or "0"] += 1
            local = parse_rat(ch.get("offset")) - p_start
            if not (0 <= local < p_dur):
                bad.append((parent.get("name"), ch.tag, ch.get("lane"), local))
    print(f"children by lane: {dict(lanes)}")
    print(f"children starting outside parent: {len(bad)} {bad[:5]}")

    params = root.findall(".//param")
    print(f"<param> elements: {len(params)}")

    referenced = {el.get("ref") for el in spine.iter() if el.get("ref")}
    dangling = referenced - set(assets)
    unused = set(assets) - referenced
    print(f"dangling refs: {dangling or 'none'}  unused assets: {unused or 'none'}")

    # Blueprint census.
    bp = json.loads((out_dir / "blueprint.json").read_text(encoding="utf-8"))["cuts"]
    print(f"blueprint cuts: {Counter(c['track'] for c in bp)}")
    v1_srcs = {Path(c["source_file"]).name for c in bp if c["track"] == "V1_A1_PRIMARY"}
    print(f"distinct V1 A-roll sources: {v1_srcs}")

    lane_m2 = [ch for p in clips for ch in p if ch.get("lane") == "-2"]
    lane_m3 = [ch for p in clips for ch in p if ch.get("lane") == "-3"]
    print(f"lane -2: {len(lane_m2)} ({[assets.get(c.get('ref')) for c in lane_m2]})")
    print(f"lane -3: {len(lane_m3)} ({[assets.get(c.get('ref')) for c in lane_m3]})")


if __name__ == "__main__":
    main()
