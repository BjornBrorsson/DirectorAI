"""Finalize a DirectorCut FCPXML for Resolve (multi-A-roll version).

The conformer's output already has the right spine shape (asset-clips
referencing normalized media, no transforms, no PiP). This pass only
fixes the audio:

1. Removes every lane -2 / -3 <audio> child (the per-cut music/SFX
   references the conformer emitted) and drops their now-unreferenced
   assets.
2. Adds the two pre-rendered beds - music_bed_ducked.wav (lane -2,
   sidechain-ducked under the voice guide) and sfx_bed.wav (lane -3,
   flat -9 dB) - as children of the first spine clip, offset = the
   parent's start (parent timebase), extending the full timeline.
3. Removes every <param> element - Resolve ignores volume keyframes;
   all level rides are baked into the beds.

Usage:
    python tasks/multi-aroll-api-run/finalize_fcpxml.py output/sts2-review-take2
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

FPS = 30


def rat(seconds: float) -> str:
    return f"{int(round(seconds * FPS))}/{FPS}s"


def probe(path: Path) -> dict:
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
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    src_xml = out_dir / f"{out_dir.name}.fcpxml"
    dst_xml = out_dir / f"{out_dir.name}-final.fcpxml"
    music_bed = out_dir / "music_bed_ducked.wav"
    sfx_bed = out_dir / "sfx_bed.wav"

    tree = ET.parse(src_xml)
    root = tree.getroot()
    resources = root.find("resources")
    spine = root.find(".//spine")

    def register_bed(path: Path) -> tuple[str, str]:
        info = probe(path)
        aid = f"a{len(resources.findall('asset')) + 1}"
        ET.SubElement(
            resources,
            "asset",
            {
                "id": aid,
                "name": path.name,
                "src": f"file:///{path.resolve().as_posix()}",
                "start": "0s",
                "duration": rat(info["duration"]),
                "hasAudio": "1",
                "hasVideo": "0",
                "audioSources": "1",
                "audioChannels": str(info["channels"]),
                "audioRate": str(info["rate"]),
            },
        )
        return aid, rat(info["duration"])

    music_id, music_dur = register_bed(music_bed)
    sfx_id, sfx_dur = register_bed(sfx_bed)

    # Drop generated music/SFX lane children; the baked beds replace them.
    removed_children = 0
    for parent in spine.iter():
        for ch in list(parent):
            if ch.tag == "audio" and ch.get("lane") in ("-2", "-3"):
                parent.remove(ch)
                removed_children += 1

    # Strip every <param> - Resolve ignores FCPXML volume automation.
    removed_params = 0
    for parent in root.iter():
        for ch in list(parent):
            if ch.tag == "param":
                parent.remove(ch)
                removed_params += 1

    clips = list(spine)
    first = clips[0]
    for lane, aid, dur, name in (
        ("-2", music_id, music_dur, music_bed.name),
        ("-3", sfx_id, sfx_dur, sfx_bed.name),
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

    # Assets nothing references anymore (OST mp3s, SFX cue mp3s) go away.
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
    dst_xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        + "\n".join(lines),
        encoding="utf-8",
    )
    print(
        f"wrote {dst_xml} ({len(clips)} spine clips, "
        f"music {music_id}, sfx {sfx_id}; dropped {removed_children} "
        f"lane children, {removed_params} params; removed assets: {removed})"
    )


if __name__ == "__main__":
    main()
