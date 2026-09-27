"""Render the audio beds for the StS2 review FCPXML (v3 approach).

Resolve ignores <param name="volume"> keyframeAnimation, so ducking is
pre-rendered into the media instead of expressed in FCPXML:

1. voice_guide.wav - the normalized A-roll VO placed at each primary cut's
   timeline_in. Sidechain key only; not shipped in the timeline.
2. music_bed_ducked.wav - the A2_MUSIC cuts mixed into one bed, ducked under
   the voice guide via sidechaincompress, then trimmed -8 dB overall.
3. sfx_bed.wav - the A3_SFX cue stingers at their timeline_in, -9 dB, not
   ducked.

Run from repo root:
    python tasks/sts2-ea-review-run/render_audio.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from directorcut.utils import parse_timecode

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "output" / "sts2-ea-review"
VO_NORM = REPO / "ingest" / "voiceover" / "aroll_voice_norm.mp3"

VOICE_GUIDE = OUT / "voice_guide.wav"
MUSIC_BED = OUT / "music_bed_ducked.wav"
SFX_BED = OUT / "sfx_bed.wav"

# Sidechain duck: ~-36.5 dB threshold, 12:1, 40 ms in / 500 ms out.
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


def render_voice_guide(cuts: list[dict], timeline_len: float) -> None:
    """Place the normalized VO under every primary cut and mix to one file."""
    v1 = [c for c in cuts if c["track"] == "V1_A1_PRIMARY"]
    n = len(v1)
    # One input file feeds every segment via asplit.
    chains = [
        "[0:a]asplit=" + str(n) + "".join(f"[v{i}]" for i in range(n)) + ";"
    ]
    segs = []
    for i, cut in enumerate(v1):
        chains.append(_delay_chain(f"[v{i}]", cut, f"[s{i}]") + ";")
        segs.append(f"[s{i}]")
    chains.append(
        f"anullsrc=r=48000:cl=stereo:d={timeline_len:.3f}[pad];"
    )
    chains.append(
        "".join(segs) + f"[pad]amix=inputs={n + 1}:normalize=0:"
        "duration=longest[out]"
    )
    _run_ffmpeg(
        [VO_NORM],
        "\n".join(chains),
        VOICE_GUIDE,
        OUT / "voice_guide.ffgraph",
    )


def render_music_bed(cuts: list[dict], timeline_len: float) -> None:
    """Mix the A2_MUSIC cuts, sidechain-duck under the voice guide, -8 dB."""
    music = [c for c in cuts if c["track"] == "A2_MUSIC"]
    inputs = [Path(c["source_file"]) for c in music] + [VOICE_GUIDE]
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
        MUSIC_BED,
        OUT / "music_bed.ffgraph",
    )


def render_sfx_bed(cuts: list[dict], timeline_len: float) -> None:
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
        SFX_BED,
        OUT / "sfx_bed.ffgraph",
    )


def main() -> None:
    cuts = json.loads((OUT / "blueprint.json").read_text(encoding="utf-8"))["cuts"]
    timeline_len = max(parse_timecode(c["timeline_out"]) for c in cuts)
    print(f"timeline length: {timeline_len:.3f}s")
    render_voice_guide(cuts, timeline_len)
    render_music_bed(cuts, timeline_len)
    render_sfx_bed(cuts, timeline_len)


if __name__ == "__main__":
    main()
