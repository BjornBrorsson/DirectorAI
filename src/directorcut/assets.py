"""Asset interpretation: resolve AUTO roles and describe visual assets.

When the user dumps files without role hints (flat folder, no prefixes, or
explicitly marked "auto" in the UI), this pass asks Gemini to classify each
ambiguous file by filename, media type, duration, and - for videos and
images - a thumbnail frame.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import run_ffmpeg
from directorcut.gemini_client import GeminiClient
from directorcut.ingest import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    ROLE_COMPATIBLE_EXTS,
    SCRIPT_EXTS,
    VIDEO_EXTS,
    with_primary_fields,
)
from directorcut.log import get_logger
from directorcut.models import Asset, AssetRole, IngestManifest
from directorcut.utils import temp_dir

logger = get_logger(__name__)

PROMPT = """You are an assistant video editor sorting raw production files.
Assign each file below one role:

- a_roll: primary footage the story plays out on (main camera / gameplay capture)
- b_roll: supplemental footage meant to overlay the A-roll
- voiceover: spoken narration or dialogue track
- music: background music bed
- sfx: short sound effect or stinger
- image: still image usable as a b-roll overlay
- script: text file containing a script or transcript

Also give each file a short description of what it appears to contain.
Return JSON matching the AssetInterpretation schema.

Files (numbered; thumbnails attached in the same order when available):
{listing}

Direction from the editor (may be empty):
{direction}
"""


class _AssetEntry(BaseModel):
    path: str
    role: AssetRole
    description: str | None = None


class _AssetInterpretation(BaseModel):
    assets: list[_AssetEntry]


def _heuristic_role(asset: Asset, has_a_roll: bool) -> AssetRole:
    """Deterministic fallback used in dev mode when no Gemini call runs."""
    ext = asset.path.suffix.lower()
    name = asset.path.stem.lower()
    if ext in VIDEO_EXTS:
        return AssetRole.B_ROLL if has_a_roll else AssetRole.A_ROLL
    if ext in AUDIO_EXTS:
        if any(k in name for k in ("music", "track", "score", "bgm")):
            return AssetRole.MUSIC
        if any(k in name for k in ("sfx", "sound", "effect", "stinger")):
            return AssetRole.SFX
        return AssetRole.VOICEOVER
    if ext in IMAGE_EXTS:
        return AssetRole.IMAGE
    if ext in SCRIPT_EXTS:
        return AssetRole.SCRIPT
    return AssetRole.AUTO


def _fake_interpretation(assets: list[Asset]) -> dict:
    """Dev-mode stub mirroring _heuristic_role over the queried assets."""
    has_a_roll = False
    entries = []
    for asset in assets:
        role = _heuristic_role(asset, has_a_roll)
        if role == AssetRole.A_ROLL:
            has_a_roll = True
        entries.append(
            {"path": str(asset.path), "role": role.value, "description": asset.path.name}
        )
    return {"assets": entries}


def _thumbnail_bytes(asset: Asset, work_dir: Path) -> bytes | None:
    """Return JPEG bytes representing the asset, or None if unavailable."""
    ext = asset.path.suffix.lower()
    if ext in IMAGE_EXTS:
        try:
            return asset.path.read_bytes()
        except OSError:
            return None
    if ext in VIDEO_EXTS:
        thumb = work_dir / f"{asset.path.stem}_thumb.jpg"
        try:
            run_ffmpeg([
                "-y",
                "-i",
                str(asset.path),
                "-frames:v",
                "1",
                "-q:v",
                "5",
                str(thumb),
            ])
            return thumb.read_bytes()
        except (OSError, FFmpegError):
            # Thumbnails are best-effort context for the classifier - a file
            # that ffmpeg can't frame-grab still gets classified by metadata.
            return None
    return None


def interpret_assets(manifest: IngestManifest, settings: Settings) -> IngestManifest:
    """Resolve AUTO asset roles (and image descriptions) via Gemini.

    Assets with an explicit role from the directory/prefix conventions are
    only sent for description when they are images. Legacy single-path fields
    are recomputed afterwards so they track the final roles.
    """
    auto_assets = [a for a in manifest.assets if a.role == AssetRole.AUTO]
    image_assets = [
        a for a in manifest.assets if a.role == AssetRole.IMAGE and not a.description
    ]
    to_classify = auto_assets + image_assets
    if not to_classify:
        return with_primary_fields(manifest)

    listing_lines = []
    media: list[bytes] = []
    with temp_dir() as work_dir:
        for i, asset in enumerate(to_classify, start=1):
            dur = f"{asset.duration_seconds:.1f}s" if asset.duration_seconds else "?"
            line = f"{i}. {asset.path.name} ({asset.path.suffix}, {dur})"
            # Pre-filled descriptions carry context the filename can't -
            # e.g. "audio stream 1 embedded in obs.mp4" lets the model reason
            # about which embedded track is the mic vs. gameplay audio.
            if asset.description:
                line += f" - {asset.description}"
            listing_lines.append(line)
            thumb = _thumbnail_bytes(asset, work_dir)
            if thumb:
                media.append(thumb)

        prompt = PROMPT.format(
            listing="\n".join(listing_lines),
            direction=manifest.direction or "",
        )
        client = GeminiClient(settings)
        result = client.call(
            prompt=prompt,
            media=media or None,
            response_model=_AssetInterpretation,
            dev_mode_override=_fake_interpretation(to_classify),
            task="interpret",
        )

    # The model echoes each file's path back; match on the exact string first
    # and fall back to the basename in case it normalizes separators.
    by_path = {e.path: e for e in result.assets}
    by_name = {Path(e.path).name: e for e in result.assets}
    classified = {a.path for a in to_classify}

    new_assets: list[Asset] = []
    for asset in manifest.assets:
        entry = by_path.get(str(asset.path)) or by_name.get(asset.path.name)
        if asset.path not in classified or entry is None:
            new_assets.append(asset)
            continue
        role = entry.role
        if role == AssetRole.AUTO or asset.path.suffix.lower() not in ROLE_COMPATIBLE_EXTS.get(
            role, set()
        ):
            # The model returned "auto" or a role incompatible with the file
            # type (e.g. music on an .mp4) - fall back to the heuristic.
            role = _heuristic_role(
                asset, any(a.role == AssetRole.A_ROLL for a in new_assets)
            )
        new_assets.append(
            asset.model_copy(update={"role": role, "description": entry.description})
        )

    updated = manifest.model_copy(update={"assets": new_assets})
    logger.info(
        "Interpreted %d asset(s): %s",
        len(to_classify),
        {a.path.name: a.role.value for a in new_assets},
    )
    return with_primary_fields(updated)
