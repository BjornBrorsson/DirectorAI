"""Ingestion engine: validate inputs and build a run manifest."""

from __future__ import annotations

from pathlib import Path

from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import run_ffprobe
from directorcut.log import get_logger
from directorcut.models import Asset, AssetRole, IngestManifest

logger = get_logger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SCRIPT_EXTS = {".txt", ".md"}

# Files that carry free-text direction rather than timeline content.
_DIRECTION_FILES = {"direction.txt", "direction.md", "directions.txt", "notes.txt"}

# A role assigned by directory name or filename prefix must be compatible
# with the file's media type - e.g. `music_clip.mp4` is footage of/about
# music, not a music bed, so a video file can never take the MUSIC role.
# Incompatible hints fall through to AUTO and get interpreted later.
ROLE_COMPATIBLE_EXTS = {
    AssetRole.A_ROLL: VIDEO_EXTS,
    AssetRole.B_ROLL: VIDEO_EXTS,
    AssetRole.VOICEOVER: AUDIO_EXTS,
    AssetRole.MUSIC: AUDIO_EXTS,
    AssetRole.SFX: AUDIO_EXTS,
    AssetRole.IMAGE: IMAGE_EXTS,
    AssetRole.SCRIPT: SCRIPT_EXTS,
}

# Role-named subdirectories anywhere under the ingest root.
_ROLE_DIRS = {
    AssetRole.A_ROLL: {"aroll", "a-roll", "a_roll", "primary"},
    AssetRole.B_ROLL: {"broll", "b-roll", "b_roll", "overlay", "overlays"},
    AssetRole.VOICEOVER: {"voiceover", "vo", "voice", "narration", "dialogue"},
    AssetRole.MUSIC: {"music", "soundtrack", "score"},
    AssetRole.SFX: {"sfx", "sound", "sounds", "effects", "foley"},
    AssetRole.IMAGE: {"images", "image", "img", "stills", "photos"},
    AssetRole.SCRIPT: {"scripts", "script", "docs", "text"},
}

# Filename prefixes as a lighter-weight alternative to subdirs.
_ROLE_PREFIXES = {
    AssetRole.A_ROLL: ("aroll_", "aroll-", "a-roll_", "a-roll-"),
    AssetRole.B_ROLL: ("broll_", "broll-", "b-roll_", "b-roll-"),
    AssetRole.VOICEOVER: ("vo_", "vo-", "voiceover_", "voiceover-", "voiceover."),
    AssetRole.MUSIC: ("music_", "music-", "music."),
    AssetRole.SFX: ("sfx_", "sfx-", "sound_", "sound-"),
    AssetRole.IMAGE: ("img_", "img-", "image_", "image-"),
    AssetRole.SCRIPT: ("script_", "script-", "script."),
}


def _role_from_dir(path: Path, ingest_dir: Path) -> AssetRole | None:
    """Map any ancestor directory name (relative to the ingest root) to a role."""
    try:
        rel = path.relative_to(ingest_dir)
    except ValueError:
        return None
    for part in rel.parts[:-1]:
        norm = part.lower()
        for role, names in _ROLE_DIRS.items():
            if norm in names:
                return role
    return None


def _role_from_prefix(name: str) -> AssetRole | None:
    lowered = name.lower()
    for role, prefixes in _ROLE_PREFIXES.items():
        if lowered.startswith(prefixes):
            return role
    return None


def _compatible(role: AssetRole, ext: str) -> bool:
    return ext in ROLE_COMPATIBLE_EXTS.get(role, set())


def _resolve_role(path: Path, ingest_dir: Path) -> AssetRole:
    """Resolve an asset's role from directory names, then filename prefix."""
    ext = path.suffix.lower()
    for hint in (_role_from_dir(path, ingest_dir), _role_from_prefix(path.name)):
        if hint is not None and _compatible(hint, ext):
            return hint
    return AssetRole.AUTO


def _probe_video_info(path: Path) -> tuple[float, float]:
    """Return (duration_seconds, fps) via FFprobe.

    Never raises: probing is best-effort. On any failure this logs a warning
    and falls back to (0.0, 60.0) so ingest() can proceed without a hard crash.
    """
    try:
        output = run_ffprobe([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ])
        lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
        # ffprobe -show_entries with both `format=` and `stream=` sections prints
        # the stream field(s) first, then the format field(s).
        fps = 60.0
        duration = 0.0
        for line in lines:
            if "/" in line:
                num, _, den = line.partition("/")
                fps = float(num) / float(den) if float(den) != 0 else fps
            else:
                duration = float(line)
        return duration, fps
    except (FFmpegError, ValueError, ZeroDivisionError) as exc:
        logger.warning("Could not probe video info for %s: %s", path, exc)
        return 0.0, 60.0


def _probe_audio_stream_count(path: Path) -> int:
    """Count embedded audio streams in a container; 0 on any failure.

    Best-effort like the other probes: a file ffprobe can't read just
    reports 0 streams and the pipeline treats it as single-track.
    """
    try:
        output = run_ffprobe([
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ])
        return sum(1 for line in output.splitlines() if line.strip())
    except FFmpegError as exc:
        logger.warning("Could not probe audio streams for %s: %s", path, exc)
        return 0


def _probe_duration(path: Path) -> float:
    """Best-effort duration probe for audio files; 0.0 on any failure."""
    try:
        output = run_ffprobe([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ])
        return float(output.strip())
    except (FFmpegError, ValueError) as exc:
        logger.warning("Could not probe duration for %s: %s", path, exc)
        return 0.0


def _first_by(assets: list[Asset], exts: set[str], roles: tuple[AssetRole, ...]) -> Path | None:
    """First asset path whose extension and role match, roles in priority order."""
    for role in roles:
        for asset in assets:
            if asset.role == role and asset.path.suffix.lower() in exts:
                return asset.path
    return None


def _load_direction(ingest_dir: Path) -> str | None:
    """Read a direction/notes file from the ingest root, if present."""
    for name in _DIRECTION_FILES:
        candidate = ingest_dir / name
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8-sig").strip()
            if text:
                return text
    return None


def with_primary_fields(manifest: IngestManifest) -> IngestManifest:
    """Recompute the single-path fields + fps/duration from `assets`.

    Called at the end of ingest() and again after the asset-interpretation
    pass resolves AUTO roles, so the legacy fields always reflect the final
    role assignments.
    """
    assets = manifest.assets
    video = _first_by(
        assets, VIDEO_EXTS, (AssetRole.A_ROLL, AssetRole.AUTO, AssetRole.B_ROLL)
    )
    voice = _first_by(
        assets, AUDIO_EXTS, (AssetRole.VOICEOVER, AssetRole.AUTO, AssetRole.MUSIC, AssetRole.SFX)
    )
    script = _first_by(assets, SCRIPT_EXTS, (AssetRole.SCRIPT, AssetRole.AUTO))

    fps = manifest.target_fps
    duration = manifest.duration_seconds
    if video is not None:
        _, fps = _probe_video_info(video)
        video_asset = next((a for a in assets if a.path == video), None)
        duration = video_asset.duration_seconds if video_asset else duration

    return manifest.model_copy(
        update={
            "video_path": video,
            "audio_path": voice,
            "voiceover_path": voice,
            "script_path": script,
            "target_fps": fps,
            "duration_seconds": duration,
        }
    )


def ingest(
    ingest_dir: Path,
    project_name: str = "project",
    direction: str | None = None,
) -> IngestManifest:
    """Scan the ingest directory recursively and build a validated manifest."""
    if not ingest_dir.exists():
        raise FileNotFoundError(f"Ingest directory not found: {ingest_dir}")

    files = sorted(p for p in ingest_dir.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"No files in ingest directory: {ingest_dir}")

    assets: list[Asset] = []
    for path in files:
        if path.name.lower() in _DIRECTION_FILES:
            continue
        ext = path.suffix.lower()
        if ext not in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | SCRIPT_EXTS:
            logger.info("Skipping unsupported file: %s", path)
            continue
        role = _resolve_role(path, ingest_dir)
        duration = None
        stream_count = 0
        if ext in VIDEO_EXTS:
            duration, _ = _probe_video_info(path)
            stream_count = _probe_audio_stream_count(path)
        elif ext in AUDIO_EXTS:
            duration = _probe_duration(path)
        # Resolve to absolute paths now: everything downstream (proxies, FCPXML
        # `file://` URIs) assumes absolute paths, and the ingest dir is commonly
        # supplied as a relative path (e.g. the default `./ingest`).
        assets.append(
            Asset(
                path=path.resolve(),
                role=role,
                duration_seconds=duration,
                audio_stream_count=stream_count,
            )
        )

    if not any(a.path.suffix.lower() in VIDEO_EXTS | IMAGE_EXTS for a in assets):
        raise ValueError("No video or image file found in ingest directory.")

    manifest = IngestManifest(
        project_name=project_name,
        assets=assets,
        direction=direction or _load_direction(ingest_dir),
    )
    manifest = with_primary_fields(manifest)

    if manifest.audio_path is None:
        # If no standalone audio, we can extract from the video later.
        logger.warning("No dedicated audio/voiceover file found; will extract from video.")

    logger.info(
        "Ingested %d asset(s): video=%s audio=%s script=%s duration=%.2fs fps=%.3f",
        len(assets),
        manifest.video_path,
        manifest.audio_path,
        manifest.script_path,
        manifest.duration_seconds or 0.0,
        manifest.target_fps,
    )
    return manifest
