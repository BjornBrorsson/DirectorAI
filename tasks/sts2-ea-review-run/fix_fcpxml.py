"""One-off polish pass for the StS2 review FCPXML (v4).

v3 kept spine clips as <asset-clip> but relied on FCPXML adjust-transform
to reframe the portrait A-roll and emitted lane-2 PiP twins over B-roll.
Resolve mis-applies transforms on the rotated footage (black frames) and
the PiP twins showed as repeated clips. v4 drops both XML tricks:

1. Reframe: baked into the media - "A-roll reframed.mp4" is the raw
   3840x2160/rotation=90 portrait clip cropped face-centred and scaled to
   flat 1920x1080 H.264, with the normalized voice as embedded audio.
   Spine clips stay plain <asset-clip> with no transforms.
2. PiP: gone from the XML entirely - it's a Resolve-side step now.
3. Music/SFX: unchanged - the pre-rendered beds (music_bed_ducked.wav
   sidechain-ducked under the voice guide, sfx_bed.wav at -9 dB) sit as a
   single <audio> child of the first spine clip on lanes -2/-3.

Run from repo root:
    python tasks/sts2-ea-review-run/render_audio.py   # if beds are missing
    python tasks/sts2-ea-review-run/fix_fcpxml.py
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "output" / "sts2-ea-review"
SRC_XML = OUT / "sts2-ea-review.fcpxml"
DST_XML = OUT / "sts2-ea-review-v4.fcpxml"
AROLL_REFRAMED = REPO / "ingest" / "aroll" / "A-roll reframed.mp4"
MUSIC_BED = OUT / "music_bed_ducked.wav"
SFX_BED = OUT / "sfx_bed.wav"
AROLL_NAME = "A-roll raw.mp4"
FPS = 30


def rat(seconds: float) -> str:
    return f"{int(round(seconds * FPS))}/{FPS}s"


def probe(path: Path) -> dict:
    import subprocess

    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,channels,sample_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    return {
        "duration": float(data["format"]["duration"]),
        "channels": max(int(s.get("channels") or 2) for s in audio),
        "rate": int(audio[0].get("sample_rate") or 48000),
    }


def main() -> None:
    tree = ET.parse(SRC_XML)
    root = tree.getroot()
    resources = root.find("resources")
    spine = root.find(".//spine")

    def register_asset(path: Path, has_video: bool) -> tuple[str, str]:
        """Add an <asset> for `path`; returns (asset id, duration rational)."""
        info = probe(path)
        aid = f"a{len(resources.findall('asset')) + 1}"
        attrs = {
            "id": aid,
            "name": path.name,
            "src": f"file:///{path.as_posix()}",
            "start": "0s",
            "duration": rat(info["duration"]),
            "hasAudio": "1",
            "hasVideo": "1" if has_video else "0",
            "audioSources": "1",
            "audioChannels": str(info["channels"]),
            "audioRate": str(info["rate"]),
        }
        if has_video:
            attrs["format"] = "r1"
        ET.SubElement(resources, "asset", attrs)
        return aid, attrs["duration"]

    aroll_id, _ = register_asset(AROLL_REFRAMED, has_video=True)
    music_id, music_dur = register_asset(MUSIC_BED, has_video=False)
    sfx_id, sfx_dur = register_asset(SFX_BED, has_video=False)

    clips = [c for c in spine if c.tag == "asset-clip" and c.get("name") == AROLL_NAME]

    # Drop every generated music/SFX lane child; the baked beds replace them.
    for parent in spine.iter():
        for ch in list(parent):
            if ch.tag == "audio" and ch.get("lane") in ("-2", "-3"):
                parent.remove(ch)

    for clip in clips:
        # Reframed A-roll: same timings as the raw file (stream-copied
        # timeline), voice baked in, reframe baked in. No transforms.
        clip.set("ref", aroll_id)
        clip.set("name", AROLL_REFRAMED.name)

    # One baked bed per lane under the first spine clip. The bed starts at
    # timeline 0, i.e. parent-timebase offset = the parent's start, and is
    # allowed to extend past the parent's end.
    first = clips[0]
    for lane, aid, dur, name in (
        ("-2", music_id, music_dur, MUSIC_BED.name),
        ("-3", sfx_id, sfx_dur, SFX_BED.name),
    ):
        ET.SubElement(
            first,
            "audio",
            {
                "ref": aid,
                "lane": lane,
                "offset": first.get("start"),
                "name": name,
                "start": "0/30s",
                "duration": dur,
                "enabled": "1",
                "tcFormat": "NDF",
            },
        )

    # Assets nothing references anymore (raw A-roll, OSTs, SFX cues) go away.
    referenced = {el.get("ref") for el in spine.iter() if el.get("ref")}
    removed = [
        a.get("name")
        for a in list(resources.findall("asset"))
        if a.get("id") not in referenced
    ]
    for asset in list(resources.findall("asset")):
        if asset.get("id") not in referenced:
            resources.remove(asset)

    rough = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="    ")
    lines = [line for line in pretty.splitlines() if line.strip()]
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    DST_XML.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        + "\n".join(lines),
        encoding="utf-8",
    )
    print(
        f"wrote {DST_XML} ({len(clips)} clips, "
        f"aroll {aroll_id}, music {music_id}, sfx {sfx_id}; "
        f"removed assets: {removed})"
    )


if __name__ == "__main__":
    main()
