"""Throwaway check: validate connected-clip offsets in sts2-ea-review-v2.fcpxml.

Not committed. Run from repo root:
    python tasks/sts2-ea-review-run/verify_v2.py
"""

import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
XML = REPO / "output" / "sts2-ea-review" / "sts2-ea-review-v2.fcpxml"


def rat(tc: str | None) -> float:
    if not tc:
        return 0.0
    num, _, den = tc.partition("/")
    return float(num) / (float(den.rstrip("s")) if den else 1.0)


def main() -> None:
    root = ET.parse(XML).getroot()
    spine = root.find(".//spine")

    bad = []
    zero_offset_under_nonzero_start = []
    timeline_positions = []
    n_children = 0
    n_vo = n_pip = 0
    music_values = set()

    for clip in spine:
        if clip.tag not in ("video", "asset-clip", "audio", "gap"):
            continue
        p_start = rat(clip.get("start"))  # gap: no start -> 0
        p_dur = rat(clip.get("duration"))
        p_off = rat(clip.get("offset"))
        for ch in clip:
            if ch.tag not in ("audio", "video"):
                continue
            n_children += 1
            c_off = rat(ch.get("offset"))
            lane = ch.get("lane")
            if lane == "-1":
                n_vo += 1
            if lane == "2":
                n_pip += 1
            local = c_off - p_start
            if not (0 <= local < p_dur):
                bad.append((clip.get("name"), ch.tag, lane, c_off, p_start, p_dur))
            if ch.get("offset") == "0/30s" and p_start > 0:
                zero_offset_under_nonzero_start.append(
                    (clip.get("name"), ch.tag, lane)
                )
            timeline_positions.append(p_off + local)
            if lane == "-2":
                for kf in ch.iter("keyframe"):
                    music_values.add(kf.get("value"))

    print(f"children checked: {n_children}")
    print(f"out-of-parent-range children: {len(bad)}")
    for b in bad[:10]:
        print("  BAD:", b)
    print(f"offset==0/30s under nonzero-start parent: "
          f"{len(zero_offset_under_nonzero_start)}")
    for z in zero_offset_under_nonzero_start[:10]:
        print("  ZERO:", z)
    print(f"timeline start pos min/max: {min(timeline_positions):.3f}s / "
          f"{max(timeline_positions):.3f}s (must be within [0, 525])")
    print(f"lane -1 audio: {n_vo} (expect 154)")
    print(f"lane 2 PiP: {n_pip} (expect 73)")
    print(f"music keyframe values: {sorted(music_values, key=float)}")


if __name__ == "__main__":
    main()
