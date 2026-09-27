"""Render the audio beds for a DirectorCut project (multi-A-roll version).

Resolve ignores <param name="volume"> keyframeAnimation, so ducking is
pre-rendered into the media instead of expressed in FCPXML:

1. voice_guide.wav - the A-roll audio placed at each V1 cut's timeline_in.
   Unlike the single-take version, the voice is baked into each A-roll
   video, so every V1 cut's audio is pulled from cut.source_file itself
   (one ffmpeg input per distinct source file, fanned out with asplit).
   Sidechain key only; not shipped in the timeline.
2. music_bed_ducked.wav - the A2_MUSIC cuts mixed into one bed, ducked
   under the voice guide via sidechaincompress, then trimmed -8 dB.
3. sfx_bed.wav - the A3_SFX cues at their timeline_in, -9 dB, not ducked.

Usage:
    python tasks/multi-aroll-api-run/render_audio.py output/sts2-review-take2
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from directorcut.utils import parse_timecode

SIDECHAIN = (
    "sidechaincompress=threshold=0.015:ratio=12:"
    "attack=40:release=500:makeup=1:level_sc=1"
)


def _run_ffmpeg(inputs: list[Path], graph: str, out: Path, script: Path) -> None:
    script.write_text(graph, encoding="utf-8")
    cmd = ["ffmpeg", "-y"]
    for inp in inputs:
        cmd += ["-i", str(inp)]
    cmd += [
        "-filter_complex_script",
        str(script),
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"ffmpeg failed for {out.name}:\n{proc.stderr[-3000:]}")
    print(f"wrote {out.name}")


def _delay_chain(src: str, cut: dict, label: str) -> str:
    """atrim the cut's source range and delay it to its timeline_in."""
    si = parse_timecode(cut["source_in"])
    so = parse_timecode(cut["source_out"])
    d = int(round(parse_timecode(cut["timeline_in"]) * 1000))
    return (
        f"{src}atrim=start={si:.3f}:end={so:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=channel_layouts=stereo,adelay={d}|{d}{label}"
    )


def render_voice_guide(cuts: list[dict], timeline_len: float, out_dir: Path) -> Path:
    """Place each V1 cut's own source audio on the timeline and mix."""
    v1 = [c for c in cuts if c["track"] == "V1_A1_PRIMARY"]
    # One input per distinct A-roll source file; asplit fans each out to
    # the cuts that came from it.
    by_source: OrderedDict[str, list[dict]] = OrderedDict()
    for cut in v1:
        by_source.setdefault(cut["source_file"], []).append(cut)
    inputs = [Path(src) for src in by_source]
    chains = []
    segs = []
    for input_idx, src_cuts in enumerate(by_source.values()):
        n = len(src_cuts)
        chains.append(
            f"[{input_idx}:a]asplit={n}"
            + "".join(f"[v{input_idx}_{j}]" for j in range(n))
            + ";"
        )
        for j, cut in enumerate(src_cuts):
            label = f"[s{input_idx}_{j}]"
            chains.append(_delay_chain(f"[v{input_idx}_{j}]", cut, label) + ";")
            segs.append(label)
    chains.append(f"anullsrc=r=48000:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[out]"
    )
    voice_guide = out_dir / "voice_guide.wav"
    _run_ffmpeg(inputs, "\n".join(chains), voice_guide, out_dir / "voice_guide.ffgraph")
    return voice_guide


def render_music_bed(
    cuts: list[dict], timeline_len: float, out_dir: Path, voice_guide: Path
) -> None:
    """Mix the A2_MUSIC cuts, sidechain-duck under the voice guide, -8 dB."""
    music = [c for c in cuts if c["track"] == "A2_MUSIC"]
    inputs = [Path(c["source_file"]) for c in music] + [voice_guide]
    chains = []
    segs = []
    for i, cut in enumerate(music):
        chains.append(_delay_chain(f"[{i}:a]", cut, f"[m{i}]") + ";")
        segs.append(f"[m{i}]")
    chains.append(f"anullsrc=r=48000:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[bed];"
    )
    key = len(music)  # voice guide input index
    chains.append(f"[bed][{key}:a]{SIDECHAIN}[ducked];")
    chains.append("[ducked]volume=-8dB[out]")
    _run_ffmpeg(
        inputs,
        "\n".join(chains),
        out_dir / "music_bed_ducked.wav",
        out_dir / "music_bed.ffgraph",
    )


def render_sfx_bed(cuts: list[dict], timeline_len: float, out_dir: Path) -> None:
    """Mix the A3_SFX cues at their timeline_in, flat -9 dB, no ducking."""
    sfx = [c for c in cuts if c["track"] == "A3_SFX"]
    inputs = [Path(c["source_file"]) for c in sfx]
    chains = []
    segs = []
    for i, cut in enumerate(sfx):
        chains.append(_delay_chain(f"[{i}:a]", cut, f"[x{i}]") + ";")
        segs.append(f"[x{i}]")
    chains.append(f"anullsrc=r=48000:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[mix];"
    )
    chains.append("[mix]volume=-9dB[out]")
    _run_ffmpeg(
        inputs,
        "\n".join(chains),
        out_dir / "sfx_bed.wav",
        out_dir / "sfx_bed.ffgraph",
    )


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/sts2-review-take2")
    cuts = json.loads((out_dir / "blueprint.json").read_text(encoding="utf-8"))["cuts"]
    timeline_len = max(parse_timecode(c["timeline_out"]) for c in cuts)
    print(f"project: {out_dir}  timeline: {timeline_len:.3f}s")
    voice_guide = render_voice_guide(cuts, timeline_len, out_dir)
    render_music_bed(cuts, timeline_len, out_dir, voice_guide)
    render_sfx_bed(cuts, timeline_len, out_dir)


if __name__ == "__main__":
    main()
