"""Ingest-time media prep: bake reframes and voice normalization into media.

Two quirks drive this module (verified on the StS2 EA-review runs, see
docs/MASTER_MEMORIES.md):

* DaVinci Resolve mis-applies FCPXML `adjust-transform` on rotated/portrait
  footage (black frames), and rotated A-roll pillarboxes in a 16:9
  timeline. The durable fix is to bake the reframe into a new media file:
  ffmpeg autorotates on decode, so `crop`/`scale` filters operate in the
  *displayed* frame orientation and the encoded output carries no rotation
  metadata at all.
* Phone/camera voice records quiet (-26/-29 LUFS measured) with clipped
  peaks. The proven repair chain is `highpass=f=80 -> compand -> loudnorm
  I=-16/TP=-1.5 -> aac 192k`, applied by remuxing (`-c:v copy`) so video
  and rotation side-data survive untouched.

Both run once per A-roll asset at ingest; the rewritten `Asset.path` points
at the baked file in `output/<project>/media/` and `Asset.derived_from`
keeps the original path for provenance (and so extracted-stream assets can
still be matched to their source).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import find_ffmpeg, run_ffprobe
from directorcut.ingest import VIDEO_EXTS, with_primary_fields
from directorcut.log import get_logger
from directorcut.models import Asset, AssetRole, IngestManifest

logger = get_logger(__name__)

MEDIA_DIR_NAME = "media"

# The FCPXML sequence is authored 1920x1080; anything whose effective
# (post-rotation) aspect differs gets reframed to it.
TARGET_W = 1920
TARGET_H = 1080
TARGET_AR = TARGET_W / TARGET_H
# Aspect slack before a reframe is considered worthwhile (~1% of 16:9).
_AR_TOLERANCE = 0.02
# Where the crop window sits along the spare axis: 0.39 == "upper-middle",
# biased toward the face. Verified on the StS2 A-roll: (3840-1215)*0.39
# ~= 1024, which put the speaker's eyes on the upper third line.
_FACE_BIAS = 0.39

# Voice normalization: quiet-speech compand into loudnorm targeting
# broadcast-safe -16 LUFS / -1.5 dBTP, resampled to 48k for the AAC encode.
VOICE_NORM_FILTER = (
    "highpass=f=80,"
    "compand=attacks=0.02:decays=0.25:"
    "points=-80/-90|-45/-45|-27/-20|0/-15:soft-knee=6,"
    "loudnorm=I=-16:TP=-1.5,"
    "aresample=48000"
)
# Already-normalized audio lands inside this integrated-loudness band;
# re-running loudnorm on it would double-process and pump the noise floor.
LOUDNESS_OK_MIN = -18.0
LOUDNESS_OK_MAX = -15.0

# Direction phrases that disable the automatic reframe ("keep my vertical
# crop" workflows). Config `reframe_aroll=False` is the hard switch.
_REFRAME_OPT_OUT = re.compile(
    r"(no|don't|do not|without)\s+re-?fram|keep\s+(the\s+)?(original|native|"
    r"full[\s-]?frame|portrait)\s*(framing|frame|aspect|crop)?",
    re.IGNORECASE,
)

# ebur128's closing summary line, e.g. "    I:         -26.1 LUFS".
_LUFS_RE = re.compile(r"I:\s*(-?[\d.]+)\s*LUFS")


@dataclass
class VideoGeometry:
    """Probed video dimensions plus display rotation (degrees, may be None)."""

    width: int
    height: int
    rotation: float | None

    @property
    def effective(self) -> tuple[int, int]:
        """(width, height) as displayed - swapped when rotated +/-90 deg."""
        if self.rotation is not None and int(self.rotation) % 180 != 0:
            return self.height, self.width
        return self.width, self.height


def probe_geometry(path: Path, settings: Settings | None = None) -> VideoGeometry | None:
    """ffprobe the first video stream for size + rotation side-data.

    Returns None on any failure - the caller treats an unprobeable file as
    "leave it alone" rather than risking a bad bake.
    """
    try:
        out = run_ffprobe(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                "-of",
                "json",
                str(path),
            ],
            settings,
        )
        streams = json.loads(out).get("streams") or []
    except (FFmpegError, json.JSONDecodeError) as exc:
        logger.warning("Could not probe geometry for %s: %s", path, exc)
        return None
    if not streams:
        return None
    stream = streams[0]
    if not stream.get("width") or not stream.get("height"):
        return None
    rotation: float | None = None
    for sd in stream.get("side_data_list") or []:
        if sd.get("rotation") is not None:
            rotation = float(sd["rotation"])
            break
    if rotation is None and (stream.get("tags") or {}).get("rotate") is not None:
        try:
            rotation = float(stream["tags"]["rotate"])
        except (TypeError, ValueError):
            rotation = None
    return VideoGeometry(
        int(stream["width"]), int(stream["height"]), rotation
    )


def needs_reframe(geom: VideoGeometry) -> bool:
    """True when the clip won't land clean 16:9 on the spine.

    Any display rotation must be baked away (Resolve mis-reads it), and any
    non-16:9 effective frame gets cropped to the sequence AR.
    """
    if geom.rotation is not None and int(geom.rotation) % 360 != 0:
        return True
    w, h = geom.effective
    return abs(w / h - TARGET_AR) > _AR_TOLERANCE


def reframe_filter(geom: VideoGeometry) -> str:
    """Build the crop+scale filter that lands the frame at 1920x1080.

    ffmpeg autorotates on decode, so the crop runs in the rotated frame
    space (e.g. rotation=90 on a 3840x2160 file yields 2160x3840 frames).
    Portrait/oversized-height material gets a face-biased vertical crop;
    oversized-width crops stay horizontally centered.
    """
    ew, eh = geom.effective
    filters = []
    if abs(ew / eh - TARGET_AR) > _AR_TOLERANCE:
        if ew / eh > TARGET_AR:
            # Too wide: crop width, keep full height, centered.
            cw = _even(eh * TARGET_AR)
            x = _even((ew - cw) / 2)
            filters.append(f"crop={cw}:{eh}:{x}:0")
        else:
            # Too tall (portrait etc.): crop height with the window biased
            # ~39% down the slack - where faces usually sit.
            ch = _even(ew / TARGET_AR)
            y = _even((eh - ch) * _FACE_BIAS)
            filters.append(f"crop={ew}:{ch}:0:{y}")
    filters.append(f"scale={TARGET_W}:{TARGET_H}:flags=lanczos")
    return ",".join(filters)


def _even(value: float) -> int:
    """Round to the nearest even int - h264/yuv420p wants even geometry."""
    return int(round(value / 2) * 2)


def measure_integrated_loudness(
    path: Path, settings: Settings | None = None
) -> float | None:
    """Return the ebur128 integrated loudness (LUFS) of the first audio
    stream, or None when the file can't be measured (no audio, probe fail).
    """
    proc = subprocess.run(
        [
            str(find_ffmpeg(settings)),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("Could not measure loudness for %s", path)
        return None
    hits = _LUFS_RE.findall(proc.stderr)
    return float(hits[-1]) if hits else None


def _direction_blocks_reframe(direction: str | None) -> bool:
    return bool(direction and _REFRAME_OPT_OUT.search(direction))


def _bake(
    src: Path,
    dst: Path,
    *,
    reframe_vf: str | None,
    normalize_audio: bool,
    settings: Settings | None,
) -> None:
    """One ffmpeg pass producing the baked file.

    `reframe_vf` set => video is decoded (autorotated, dropping rotation
    metadata) and re-encoded h264; unset => `-c:v copy` remux that preserves
    rotation side-data. `normalize_audio` swaps `-c:a copy` for the proven
    voice chain (aac 192k). Only the first video stream and the audio
    streams are mapped - data/subtitle junk can break the mp4 muxer.
    Audio-only sources (a voiceover mp3/m4a) skip the video map entirely -
    `0:v:0` would fail their probe with "Stream map matches no streams".
    """
    is_video = src.suffix.lower() in VIDEO_EXTS
    cmd = [str(find_ffmpeg(settings)), "-y", "-i", str(src)]
    if is_video:
        cmd += ["-map", "0:v:0", "-map", "0:a?"]
    else:
        cmd += ["-map", "0:a:0"]
    if reframe_vf is not None:
        cmd += [
            "-vf",
            reframe_vf,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
        ]
    elif is_video:
        cmd += ["-c:v", "copy"]
    if normalize_audio:
        cmd += ["-af", VOICE_NORM_FILTER, "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    else:
        cmd += ["-c:a", "copy"]
    cmd += ["-movflags", "+faststart", str(dst)]
    logger.info("Running FFmpeg bake: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg media-prep failed for {dst.name}:\n{proc.stderr[-3000:]}"
        )


def _prep_one(
    asset: Asset,
    media_dir: Path,
    settings: Settings,
    reframe_allowed: bool,
) -> Asset | None:
    """Return the re-pointed asset when the source needed a bake, else None."""
    src = asset.path
    ext = src.suffix.lower()
    is_video = ext in VIDEO_EXTS

    want_reframe = False
    vf: str | None = None
    if is_video and asset.role == AssetRole.A_ROLL and reframe_allowed:
        geom = probe_geometry(src, settings)
        if geom is not None and needs_reframe(geom):
            want_reframe = True
            vf = reframe_filter(geom)
            ew, eh = geom.effective
            logger.info(
                "Reframing %s (%dx%d rot=%s -> effective %dx%d): %s",
                src.name,
                geom.width,
                geom.height,
                geom.rotation,
                ew,
                eh,
                vf,
            )

    want_norm = False
    if settings.normalize_voice and asset.role in (
        AssetRole.A_ROLL,
        AssetRole.VOICEOVER,
    ):
        lufs = measure_integrated_loudness(src, settings)
        if lufs is None:
            logger.info("Normalize-skip %s - no measurable audio", src.name)
        elif LOUDNESS_OK_MIN <= lufs <= LOUDNESS_OK_MAX:
            # Double-normalizing pumps the noise floor - leave good audio.
            logger.info(
                "Normalize-skip %s - already %.1f LUFS (target band %.0f..%.0f)",
                src.name,
                lufs,
                LOUDNESS_OK_MIN,
                LOUDNESS_OK_MAX,
            )
        else:
            want_norm = True
            logger.info(
                "Normalizing %s - measured %.1f LUFS, target -16/-1.5TP",
                src.name,
                lufs,
            )

    if not want_reframe and not want_norm:
        return None

    stem = src.stem + ("_reframed" if want_reframe else "_voicenorm")
    dst = media_dir / f"{stem}{'.mp4' if is_video else '.m4a'}"
    if not dst.exists():
        media_dir.mkdir(parents=True, exist_ok=True)
        _bake(src, dst, reframe_vf=vf, normalize_audio=want_norm, settings=settings)
    else:
        logger.info("Reusing baked media %s", dst.name)
    return asset.model_copy(
        update={"path": dst.resolve(), "derived_from": src.resolve()}
    )


def prepare_aroll_media(
    manifest: IngestManifest,
    media_dir: Path,
    settings: Settings,
) -> tuple[IngestManifest, bool]:
    """Bake reframe/normalization into A-roll (+ voiceover) media.

    Returns (manifest, changed). Runs after role interpretation so AUTO
    assets classified as a_roll are covered, and is safe to re-run on a
    resumed manifest: baked outputs are content-stable (`exists()` reuse)
    and a deleted media file is re-rendered from `derived_from`.
    """
    reframe_allowed = settings.reframe_aroll and not _direction_blocks_reframe(
        manifest.direction
    )
    if settings.reframe_aroll and not reframe_allowed:
        logger.info("A-roll reframe disabled by direction text")

    changed = False
    new_assets: list[Asset] = []
    for asset in manifest.assets:
        if asset.role not in (AssetRole.A_ROLL, AssetRole.VOICEOVER):
            new_assets.append(asset)
            continue
        src = asset.path
        # A baked file that disappeared on disk (e.g. media/ cleaned) is
        # re-rendered from the recorded original rather than left dangling.
        if not src.exists() and asset.derived_from and asset.derived_from.exists():
            src = asset.derived_from
        if not src.exists():
            if asset.derived_from is not None:
                logger.warning(
                    "Baked media %s missing and original %s gone - asset left "
                    "dangling", asset.path.name, asset.derived_from.name,
                )
            new_assets.append(asset)
            continue
        if asset.derived_from is not None and src == asset.path:
            # Already baked in a previous run: decisions (probe + loudness)
            # are made against the ingest original so the check is stable,
            # and the exists()-skip on the baked name makes it cheap.
            src = asset.derived_from
        updated = _prep_one(
            asset.model_copy(update={"path": src}), media_dir, settings,
            reframe_allowed,
        )
        if updated is None:
            new_assets.append(asset)
            continue
        # Keep the baked path stable across resumes: derived_from always
        # names the true ingest original.
        if asset.derived_from is not None:
            updated = updated.model_copy(
                update={"derived_from": asset.derived_from}
            )
        new_assets.append(updated)
        changed = changed or updated != asset

    if not changed:
        return manifest, False
    manifest = manifest.model_copy(update={"assets": new_assets})
    # video_path/audio_path etc. must track the baked media.
    return with_primary_fields(manifest), True
