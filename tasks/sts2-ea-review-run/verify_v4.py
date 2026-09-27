"""Throwaway check: validate sts2-ea-review-v4.fcpxml structure.

Not committed. Run from repo root:
    python tasks/sts2-ea-review-run/verify_v4.py
"""

import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
XML = REPO / "output" / "sts2-ea-review" / "sts2-ea-review-v4.fcpxml"
BED_LANES = ("-2", "-3")


def rat(tc: str | None) -> float:
    if not tc:
        return 0.0
    num, _, den = tc.partition("/")
    return float(num) / (float(den.rstrip("s")) if den else 1.0)


def main() -> None:
    root = ET.parse(XML).getroot()
    resources = root.find("resources")
    spine = root.find(".//spine")

    asset_ids = {a.get("id") for a in resources.findall("asset")}
    norm_ids = {
        a.get("id") for a in resources.findall("asset")
        if a.get("name") == "A-roll reframed.mp4"
    }
    print(f"assets: {len(asset_ids)}, reframed A-roll id(s): {sorted(norm_ids)}")

    spine_els = list(spine)
    tags = {el.tag for el in spine_els}
    print(f"spine elements: {len(spine_els)}, tags: {sorted(tags)}")

    # No <video> in spine, no transforms, no <param>, no dangling refs.
    spine_videos = [el for el in spine_els if el.tag == "video"]
    transforms = list(root.iter("adjust-transform"))
    params = [el.get("name") for el in root.iter("param")]
    dangling = {
        el.get("ref") for el in spine.iter() if el.get("ref")
    } - asset_ids
    print(f"spine <video>: {len(spine_videos)} (expect 0)")
    print(f"adjust-transform: {len(transforms)} (expect 0)")
    print(f"<param> elements: {len(params)} (expect 0)")
    print(f"dangling refs: {sorted(dangling) or 'none'}")

    bad_refs = [el.get("name") for el in spine_els if el.get("ref") not in norm_ids]
    print(f"spine clips not ref'ing reframed asset: {len(bad_refs)} (expect 0)")

    # Connected-child offsets: local = child.offset - parent.start must be
    # inside the parent. Beds may overflow; only their start must be inside.
    bad_children = []
    n_children = n_pip = n_broll = n_mus = n_sfx = 0
    for clip in spine_els:
        p_start = rat(clip.get("start"))
        p_dur = rat(clip.get("duration"))
        for ch in clip:
            if ch.tag not in ("audio", "video"):
                continue
            n_children += 1
            lane = ch.get("lane")
            if lane == "2":
                n_pip += 1
            elif lane == "1":
                n_broll += 1
            elif lane == "-2":
                n_mus += 1
            elif lane == "-3":
                n_sfx += 1
            local = rat(ch.get("offset")) - p_start
            if not (0 <= local < p_dur):
                bad_children.append(
                    (clip.get("name"), ch.tag, lane, local, p_dur)
                )
    print(f"connected children: {n_children} (expect 33)")
    print(f"out-of-parent children: {len(bad_children)}")
    for b in bad_children[:10]:
        print("  BAD:", b)

    # Contiguity: each spine clip's offset == prev offset + prev duration.
    # The blueprint itself carries up to 1 frame of in/out jitter, so only
    # report gaps larger than that (with epsilon for float division).
    gaps = []
    max_gap = 0.0
    prev_end = 0.0
    for clip in spine_els:
        off = rat(clip.get("offset"))
        max_gap = max(max_gap, off - prev_end)
        if off - prev_end > 1.5 / 30:
            gaps.append((round(prev_end, 3), round(off, 3)))
        prev_end = off + rat(clip.get("duration"))
    print(f"spine gaps > 1 frame: {len(gaps)} (max gap {max_gap * 30:.2f} frames)")
    for g in gaps[:10]:
        print("  GAP:", g)
    print(f"timeline end: {prev_end:.3f}s")

    print(f"B-roll (lane 1): {n_broll} (expect 31)")
    print(f"PiP (lane 2): {n_pip} (expect 0)")
    print(f"lane -2 audio: {n_mus} (expect 1)")
    print(f"lane -3 audio: {n_sfx} (expect 1)")
    for el in spine.iter("audio"):
        if el.get("lane") in BED_LANES:
            print("  bed:", dict(el.attrib))


if __name__ == "__main__":
    main()
