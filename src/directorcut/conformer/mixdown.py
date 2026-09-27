"""Pre-rendered audio beds for Resolve-target FCPXML ("mixdown" stage).

DaVinci Resolve ignores `<param name="volume">` keyframeAnimation entirely
(verified 2026-09-22 - see docs/MASTER_MEMORIES.md), so for Resolve targets
ducking is baked into media instead of expressed in the XML:

1. `voice_guide.wav` - every V1_A1_PRIMARY cut's own source audio placed at
   its timeline position. It is only the sidechain key for ducking; it is
   NOT shipped on the timeline (the spine asset-clips carry their own
   embedded audio). Multi-A-roll projects get one ffmpeg input per distinct
   source file, fanned out with `asplit`.
2. `music_bed_ducked.wav` - all A2_MUSIC cuts mixed into one bed spanning
   the timeline, sidechain-compressed against the voice guide, then trimmed
   -8 dB overall.
3. `sfx_bed.wav` - all A3_SFX cues at their timeline positions, flat -9 dB,
   not ducked.

The `.ffgraph` files written next to the wavs are the rendered filter
graphs - kept for debugging a mix that sounds wrong.

This is the productized version of `tasks/*/render_audio.py` (the recipe
that produced the only Resolve-verified mix so far).
"""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from pathlib import Path

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import find_ffmpeg, run_ffprobe
from directorcut.ingest import AUDIO_EXTS, IMAGE_EXTS
from directorcut.log import get_logger
from directorcut.models import AssemblyBlueprint, Cut
from directorcut.utils import parse_timecode

logger = get_logger(__name__)

VOICE_GUIDE_NAME = "voice_guide.wav"
MUSIC_BED_NAME = "music_bed_ducked.wav"
SFX_BED_NAME = "sfx_bed.wav"

# Sidechain duck: ~-36.5 dBFS threshold, 12:1, 40 ms attack / 500 ms release.
# Verified recipe: lands the bed ~-25 LUFS in voice gaps and ~-46 under voice.
SIDECHAIN = (
    "sidechaincompress=threshold=0.015:ratio=12:"
    "attack=40:release=500:makeup=1:level_sc=1"
)
MUSIC_TRIM_DB = -8.0
SFX_TRIM_DB = -9.0
SAMPLE_RATE = 48000


def _run_ffmpeg(
    inputs: list[Path],
    graph: str,
    out: Path,
    script: Path,
    settings: Settings | None = None,
) -> None:
    """Render `[out]` from a filter_complex_script; raise FFmpegError on failure.

    The graph is written to `script` first because Windows CreateProcess
    caps command lines near 32K chars - a large project's filter graph can
    approach that, and a script file also survives for debugging.
    """
    script.write_text(graph, encoding="utf-8")
    cmd = [str(find_ffmpeg(settings)), "-y"]
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
        str(SAMPLE_RATE),
        "-ac",
        "2",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg mixdown failed for {out.name}:\n{proc.stderr[-3000:]}"
        )
    logger.info("wrote %s", out.name)


def _delay_chain(src: str, cut: Cut, label: str) -> str:
    """atrim the cut's source range and adelay it to its timeline_in."""
    si = parse_timecode(cut.source_in)
    so = parse_timecode(cut.source_out)
    delay_ms = int(round(parse_timecode(cut.timeline_in) * 1000))
    return (
        f"{src}atrim=start={si:.3f}:end={so:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=channel_layouts=stereo,adelay={delay_ms}|{delay_ms}{label}"
    )


def _has_audio(path: Path, settings: Settings | None) -> bool:
    """Best-effort audio-stream check - sources without audio can't feed a guide."""
    if path.suffix.lower() in AUDIO_EXTS:
        return True
    if path.suffix.lower() in IMAGE_EXTS:
        return False
    try:
        out = run_ffprobe(
            [
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            settings,
        )
        return any(line.strip() for line in out.splitlines())
    except FFmpegError:
        # Unprobed sources are assumed to carry audio; ffmpeg will fail loudly
        # in the mixdown if they actually don't.
        logger.warning("Could not probe audio streams for %s; assuming audio", path)
        return True


def render_voice_guide(
    cuts: list[Cut],
    timeline_len: float,
    out_dir: Path,
    settings: Settings | None = None,
) -> Path | None:
    """Mix every audible V1 cut's source audio onto the timeline.

    Returns the guide path, or None when the spine carries no usable audio
    (e.g. stills-only or silent sources) - the caller then renders beds
    without a sidechain key.
    """
    v1 = [c for c in cuts if c.track == "V1_A1_PRIMARY"]
    # One input per distinct source file; asplit fans each out to its cuts.
    by_source: OrderedDict[Path, list[Cut]] = OrderedDict()
    for cut in v1:
        if _has_audio(cut.source_file, settings):
            by_source.setdefault(cut.source_file, []).append(cut)
        else:
            logger.info(
                "Voice guide skips %s - no audio stream", cut.source_file.name
            )
    if not by_source:
        return None
    inputs = list(by_source)
    chains: list[str] = []
    segs: list[str] = []
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
    chains.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[out]"
    )
    out = out_dir / VOICE_GUIDE_NAME
    _run_ffmpeg(inputs, "\n".join(chains), out, out_dir / "voice_guide.ffgraph", settings)
    return out


def render_music_bed(
    cuts: list[Cut],
    timeline_len: float,
    out_dir: Path,
    voice_guide: Path | None,
    settings: Settings | None = None,
) -> Path | None:
    """Mix A2_MUSIC cuts into one bed, sidechain-ducked under the voice guide."""
    music = [c for c in cuts if c.track == "A2_MUSIC"]
    if not music:
        return None
    inputs = [c.source_file for c in music]
    chains: list[str] = []
    segs: list[str] = []
    for i, cut in enumerate(music):
        chains.append(_delay_chain(f"[{i}:a]", cut, f"[m{i}]") + ";")
        segs.append(f"[m{i}]")
    chains.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[bed];"
    )
    if voice_guide is not None:
        key = len(music)  # the voice guide is appended as the last input
        inputs.append(voice_guide)
        chains.append(f"[bed][{key}:a]{SIDECHAIN}[ducked];")
        chains.append(f"[ducked]volume={MUSIC_TRIM_DB}dB[out]")
    else:
        # No dialogue on the spine - nothing to duck under; still trim the
        # bed so it doesn't fight whatever audio the clips carry.
        chains.append(f"[bed]volume={MUSIC_TRIM_DB}dB[out]")
    out = out_dir / MUSIC_BED_NAME
    _run_ffmpeg(inputs, "\n".join(chains), out, out_dir / "music_bed.ffgraph", settings)
    return out


def render_sfx_bed(
    cuts: list[Cut],
    timeline_len: float,
    out_dir: Path,
    settings: Settings | None = None,
) -> Path | None:
    """Mix A3_SFX cues at their timeline positions, flat -9 dB, no ducking."""
    sfx = [c for c in cuts if c.track == "A3_SFX"]
    if not sfx:
        return None
    inputs = [c.source_file for c in sfx]
    chains: list[str] = []
    segs: list[str] = []
    for i, cut in enumerate(sfx):
        chains.append(_delay_chain(f"[{i}:a]", cut, f"[x{i}]") + ";")
        segs.append(f"[x{i}]")
    chains.append(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d={timeline_len:.3f}[pad];")
    chains.append(
        "".join(segs)
        + f"[pad]amix=inputs={len(segs) + 1}:normalize=0:duration=longest[mix];"
    )
    chains.append(f"[mix]volume={SFX_TRIM_DB}dB[out]")
    out = out_dir / SFX_BED_NAME
    _run_ffmpeg(inputs, "\n".join(chains), out, out_dir / "sfx_bed.ffgraph", settings)
    return out


def render_mixdown(
    blueprint: AssemblyBlueprint,
    output_dir: Path,
    settings: Settings | None = None,
) -> dict[str, Path]:
    """Render the Resolve-target audio beds into `output_dir`.

    Returns a `{track: path}` map (`"A2_MUSIC"` / `"A3_SFX"`) containing an
    entry per lane that had cuts. Lanes with no cuts get no bed - the
    conformer then has nothing to attach for them. Returns {} when neither
    audio lane is populated.
    """
    cuts = list(blueprint.cuts)
    if not any(c.track in ("A2_MUSIC", "A3_SFX") for c in cuts):
        return {}
    timeline_len = max(
        (parse_timecode(c.timeline_out) for c in cuts), default=0.0
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "[mixdown] rendering audio beds (timeline %.3fs)", timeline_len
    )
    voice_guide = render_voice_guide(cuts, timeline_len, output_dir, settings)
    beds: dict[str, Path] = {}
    music = render_music_bed(cuts, timeline_len, output_dir, voice_guide, settings)
    if music is not None:
        beds["A2_MUSIC"] = music
    sfx = render_sfx_bed(cuts, timeline_len, output_dir, settings)
    if sfx is not None:
        beds["A3_SFX"] = sfx
    return {k: v.resolve() for k, v in beds.items()}
