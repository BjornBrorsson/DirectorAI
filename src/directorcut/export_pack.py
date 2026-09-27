"""Creator export pack: captions, chapters, and thumbnails for upload.

Runs as a post-finalize step (called from `finalize_blueprint`, so both the
pipeline and `directorcut cut` re-conforms refresh it) and writes
`output/<project>/pack/`:

- `captions.srt` / `captions.vtt` - the spoken text actually on the
  timeline. V1 cuts' source ranges are filled from STT word timing
  (`narrative.stt.load_cached_words` - free on a rerun because the cache is
  content-addressed); where no word timing exists the covered narrative
  nodes' `spoken_text` is distributed linearly over the cut.
- `chapters.txt` - `HH:MM:SS title` lines (YouTube/FFmpeg chapter format).
  Boundaries: a V1 cut takes the thematic_tag of the first narrative node
  it covers; consecutive same-tag cuts merge into one chapter and runs
  shorter than `_MIN_CHAPTER_S` fold into the previous chapter (raw tag
  flips every few seconds would otherwise spam chapters). Titles come
  from the chapter's leading spoken text, falling back to the tag.
- `thumbs/` - up to 5 JPEGs pulled by ffmpeg at HIGH-energy
  EMOTIONAL_SPIKE node midpoints (mapped from node source time through
  the covering V1 cut), then lower-energy spikes, then long V1-cut
  midpoints until at least 3 exist. These are candidate thumbnails for
  the creator, not picks.

The pack is strictly best-effort: a missing narrative.json, STT cache, or
an ffmpeg failure on one frame never fails the conform - the caller wraps
this in try/except and individual thumbnails skip on error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import run_ffmpeg
from directorcut.ingest import AUDIO_EXTS, VIDEO_EXTS
from directorcut.log import get_logger
from directorcut.models import (
    AssemblyBlueprint,
    IngestManifest,
    NarrativeNode,
)
from directorcut.narrative.stt import load_cached_words
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)

PACK_DIR = "pack"
_THUMBS_DIR = "thumbs"
_STT_CACHE_DIR = "stt_cache"

# Subtitle conventions: ~42 chars/line, a few seconds per cue.
_MAX_CUE_CHARS = 45
_MAX_CUE_S = 4.0
# Tag runs shorter than this fold into the previous chapter.
_MIN_CHAPTER_S = 60.0
_THUMB_COUNT = 5
# Fewer than this many picks triggers midpoint backfill on the longest cuts.
_THUMB_MIN = 3
# Two thumbnail moments closer than this on the same file are duplicates.
_THUMB_MIN_SEPARATION_S = 1.5
# Node/cut coverage slop, matching cutops._spoken_for / director._cut_text.
_COVER_TOL_S = 0.15
_SENTENCE_END = (".", "?", "!")


@dataclass(frozen=True)
class _Cue:
    """One caption cue on the edited timeline (seconds + text)."""

    start_s: float
    end_s: float
    text: str


# ---------------------------------------------------------------------------
# Loading


def _load_blueprint(project_dir: Path) -> AssemblyBlueprint | None:
    path = project_dir / "blueprint.json"
    if not path.exists():
        return None
    try:
        return AssemblyBlueprint.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Export pack: unreadable blueprint.json (%s)", exc)
        return None


def _load_nodes(project_dir: Path) -> list[NarrativeNode]:
    path = project_dir / "narrative.json"
    if not path.exists():
        return []
    try:
        return [
            NarrativeNode.model_validate(n)
            for n in json.loads(path.read_text(encoding="utf-8"))
        ]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Export pack: unreadable narrative.json (%s)", exc)
        return []


def _load_manifest(project_dir: Path) -> IngestManifest | None:
    path = project_dir / "assets.json"
    if not path.exists():
        return None
    try:
        return IngestManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Export pack: unreadable assets.json (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Captions


def _audio_for_source(
    source_file: Path,
    manifest: IngestManifest | None,
    output_dir: Path,
) -> Path | None:
    """Find the audio file a source's STT transcript was keyed on.

    Mirrors pipeline._transcript_sources' mapping (video -> extracted
    `<stem>_stream0.mp3` or `<stem>_audio.mp3`, incl. pre-bake
    `derived_from` stems) but NEVER extracts - a pack refresh must not run
    ffmpeg just to find a cache key. Audio sources are their own transcript.
    """
    if source_file.suffix.lower() in AUDIO_EXTS:
        return source_file
    stems = [source_file.stem]
    if manifest is not None:
        for asset in manifest.assets:
            if asset.path == source_file and asset.derived_from is not None:
                stems.append(asset.derived_from.stem)
    for stem in stems:
        for name in (f"{stem}_stream0.mp3", f"{stem}_audio.mp3"):
            candidate = output_dir / name
            if candidate.exists():
                return candidate
    return None


def _words_for_source(
    source_file: Path,
    manifest: IngestManifest | None,
    output_dir: Path,
    settings: Settings | None,
) -> list[dict]:
    """Raw Scribe words[] for the source's transcript, or [] when uncached."""
    audio = _audio_for_source(source_file, manifest, output_dir)
    if audio is None:
        return []
    # load_cached_words only reads the STT cache-key fields off settings
    # (resolved backend + model id), then falls back to ANY backend's entry
    # for the same audio bytes - so a bare Settings(_env_file=None) still
    # finds the transcript and never touches .env secrets or the network.
    settings = settings or Settings(_env_file=None)
    entries = load_cached_words(audio, settings, output_dir / _STT_CACHE_DIR)
    return entries or []


def _covered_nodes(
    nodes: list[NarrativeNode], source_file: Path, src_in: float, src_out: float
) -> list[NarrativeNode]:
    """Narrative nodes whose range sits inside the cut's source range."""
    out = [
        n
        for n in nodes
        if n.source_file == source_file
        and parse_timecode(n.time_start) >= src_in - _COVER_TOL_S
        and parse_timecode(n.time_end) <= src_out + _COVER_TOL_S
    ]
    return sorted(out, key=lambda n: parse_timecode(n.time_start))


def _words_to_cues(words: list[dict], offset: float) -> list[_Cue]:
    """Group timed words into subtitle cues; `offset` maps source->timeline."""
    cues: list[_Cue] = []
    text_parts: list[str] = []
    cue_start: float | None = None
    cue_end = 0.0

    def flush() -> None:
        nonlocal cue_start
        if cue_start is None or not text_parts:
            cue_start = None
            text_parts.clear()
            return
        cues.append(_Cue(cue_start + offset, cue_end + offset, " ".join(text_parts)))
        cue_start = None
        text_parts.clear()

    for w in words:
        if w.get("type") != "word":
            continue
        try:
            w_start = float(w["start"])
            w_end = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        if cue_start is not None:
            too_long = len(" ".join(text_parts)) > _MAX_CUE_CHARS
            too_slow = w_end - cue_start > _MAX_CUE_S
            sentence_break = text_parts[-1].rstrip().endswith(_SENTENCE_END)
            if too_long or too_slow or sentence_break:
                flush()
        if cue_start is None:
            cue_start = w_start
        text_parts.append(text)
        cue_end = w_end
    flush()
    return cues


def _node_cues(
    cut_nodes: list[NarrativeNode], src_in: float, tl_in: float
) -> list[_Cue]:
    """Fallback: one cue per covered node, its span mapped onto the timeline."""
    cues = []
    for n in cut_nodes:
        start = tl_in + (parse_timecode(n.time_start) - src_in)
        end = tl_in + (parse_timecode(n.time_end) - src_in)
        if end > start:
            cues.append(_Cue(start, end, n.spoken_text.strip()))
    return cues


def _caption_cues(
    blueprint: AssemblyBlueprint,
    nodes: list[NarrativeNode],
    manifest: IngestManifest | None,
    project_dir: Path,
    settings: Settings | None,
) -> list[_Cue]:
    """Timeline-positioned caption cues from every V1 cut, in order."""
    cues: list[_Cue] = []
    v1 = sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    for cut in v1:
        src_in = parse_timecode(cut.source_in)
        src_out = parse_timecode(cut.source_out)
        tl_in = parse_timecode(cut.timeline_in)
        tl_out = parse_timecode(cut.timeline_out)
        offset = tl_in - src_in

        words = _words_for_source(cut.source_file, manifest, project_dir, settings)
        in_range = [
            w
            for w in words
            if w.get("type") == "word"
            and float(w.get("end") or 0.0) > src_in
            and float(w.get("start") or 0.0) < src_out
        ]
        if in_range:
            cut_cues = _words_to_cues(in_range, offset)
        else:
            cut_cues = _node_cues(
                _covered_nodes(nodes, cut.source_file, src_in, src_out),
                src_in,
                tl_in,
            )
        # Clamp to the cut's on-screen span - word timing can slightly
        # overrun the source range the cut actually ships.
        for cue in cut_cues:
            start = max(tl_in, cue.start_s)
            end = min(tl_out, cue.end_s)
            if end > start and cue.text:
                cues.append(_Cue(start, end, cue.text))
    cues.sort(key=lambda c: c.start_s)
    return cues


def _srt_time(seconds: float) -> str:
    return format_timecode(seconds).replace(".", ",")


def _write_srt(cues: list[_Cue], path: Path) -> None:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_srt_time(cue.start_s)} --> {_srt_time(cue.end_s)}\n{cue.text}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _write_vtt(cues: list[_Cue], path: Path) -> None:
    blocks = []
    for cue in cues:
        blocks.append(
            f"{format_timecode(cue.start_s)} --> {format_timecode(cue.end_s)}\n"
            f"{cue.text}"
        )
    path.write_text("WEBVTT\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Chapters


def _chapter_title(text: str, tag: str, ordinal: int) -> str:
    """Chapter title from leading spoken text; tag title when unusable."""
    text = re.sub(r"\s+", " ", text).strip()
    # Unscripted coverage markers read "Unscripted segment HH:MM:SS-..." -
    # meaningless as a chapter name.
    if not text or text.lower().startswith("unscripted segment"):
        return f"{tag.replace('_', ' ').title()} (part {ordinal})"
    words = text.split(" ")[:8]
    title = " ".join(words)
    return title[:60] + ("..." if len(title) > 60 else "")


def _chapters(
    blueprint: AssemblyBlueprint, nodes: list[NarrativeNode]
) -> list[tuple[float, str]]:
    """(seconds, title) chapter list - tag-change boundaries, merged short.

    Each V1 cut inherits the thematic_tag of the first node it covers
    (EXPOSITION when it covers none); consecutive same-tag cuts form a
    run. Runs shorter than `_MIN_CHAPTER_S` fold into the previous chapter
    so rapid tag flips (spike -> exposition -> spike) don't fragment the
    chapter list. Chapters sit at V1-cut boundaries - the only guaranteed
    seam positions on the timeline.
    """
    v1 = sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    if not v1:
        return []

    # (start_s, tag, leading_text) per tag run.
    runs: list[list] = []
    for cut in v1:
        tl_in = parse_timecode(cut.timeline_in)
        src_in = parse_timecode(cut.source_in)
        src_out = parse_timecode(cut.source_out)
        covered = _covered_nodes(nodes, cut.source_file, src_in, src_out)
        tag = covered[0].thematic_tag.value if covered else "EXPOSITION"
        text = covered[0].spoken_text if covered else ""
        if runs and runs[-1][1] == tag:
            runs[-1][2] += parse_timecode(cut.timeline_out) - tl_in  # extend
            continue
        runs.append([tl_in, tag, parse_timecode(cut.timeline_out) - tl_in, text])

    # Merge runs shorter than the floor into the previous chapter. A short
    # FIRST run has no previous - keep it (better a short opener than
    # skipping the timeline's first chapter).
    merged: list[list] = []
    for run in runs:
        if merged and run[2] < _MIN_CHAPTER_S:
            merged[-1][2] += run[2]
            continue
        merged.append(run)

    chapters: list[tuple[float, str]] = []
    for i, (start, tag, _dur, text) in enumerate(merged, start=1):
        chapters.append((start, _chapter_title(text, tag, i)))
    return chapters


def _chapter_stamp(seconds: float) -> str:
    """YouTube/FFmpeg chapter timestamp: HH:MM:SS (whole seconds, floored)."""
    return format_timecode(int(seconds))[:8]


# ---------------------------------------------------------------------------
# Thumbnails


def _thumbnail_moments(
    blueprint: AssemblyBlueprint, nodes: list[NarrativeNode]
) -> list[tuple[Path, float, float]]:
    """(source_file, source_seconds, timeline_seconds) frame picks.

    HIGH-energy EMOTIONAL_SPIKE node midpoints first (the ticket's hook -
    spikes are where a face/reaction is most likely), then any remaining
    spikes, then midpoints of the longest V1 cuts until at least
    `_THUMB_MIN` picks exist. Sources must be video files.
    """
    v1 = sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )

    def map_node(node: NarrativeNode) -> tuple[Path, float, float] | None:
        mid = (parse_timecode(node.time_start) + parse_timecode(node.time_end)) / 2
        for cut in v1:
            if node.source_file is not None and cut.source_file != node.source_file:
                continue
            if parse_timecode(cut.source_in) <= mid <= parse_timecode(cut.source_out):
                tl = parse_timecode(cut.timeline_in) + (mid - parse_timecode(cut.source_in))
                return cut.source_file, mid, tl
        return None

    spikes = [n for n in nodes if n.thematic_tag.value == "EMOTIONAL_SPIKE"]
    spikes.sort(key=lambda n: (n.energy_level.value != "HIGH", n.time_start))
    candidates = [map_node(n) for n in spikes]

    # Backfill from the longest spine cuts when spikes alone can't ship the
    # 3-5 candidates the ticket asks for.
    longest = sorted(
        v1,
        key=lambda c: parse_timecode(c.timeline_out) - parse_timecode(c.timeline_in),
        reverse=True,
    )
    usable_spikes = sum(1 for c in candidates if c is not None)
    if usable_spikes < _THUMB_MIN:
        for cut in longest:
            src_mid = (
                parse_timecode(cut.source_in) + parse_timecode(cut.source_out)
            ) / 2
            tl_mid = (
                parse_timecode(cut.timeline_in) + parse_timecode(cut.timeline_out)
            ) / 2
            candidates.append((cut.source_file, src_mid, tl_mid))
            if len(candidates) >= _THUMB_COUNT:
                break

    picks: list[tuple[Path, float, float]] = []
    for cand in candidates:
        if cand is None:
            continue
        src, t_src, t_tl = cand
        if src.suffix.lower() not in VIDEO_EXTS:
            continue  # audio/image sources can't yield a video frame
        if any(
            p[0] == src and abs(p[1] - t_src) < _THUMB_MIN_SEPARATION_S for p in picks
        ):
            continue
        picks.append(cand)
        if len(picks) >= _THUMB_COUNT:
            break
    return picks


def _extract_thumbnails(
    moments: list[tuple[Path, float, float]], thumbs_dir: Path, settings
) -> list[Path]:
    """ffmpeg frame-grab each moment; per-frame failures skip, never raise."""
    written: list[Path] = []
    if not moments:
        return written
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    for i, (src, t_src, t_tl) in enumerate(moments, start=1):
        stamp = _chapter_stamp(t_tl).replace(":", "-")
        out = thumbs_dir / f"thumb_{i:02d}_{stamp}.jpg"
        try:
            # -ss before -i = fast input seek; fine for candidate thumbs.
            run_ffmpeg(
                [
                    "-y",
                    "-ss",
                    f"{t_src:.3f}",
                    "-i",
                    str(src),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(out),
                ],
                settings,
            )
        except FFmpegError as exc:
            logger.warning("Thumbnail grab failed at %.2fs on %s: %s", t_src, src, exc)
            continue
        written.append(out)
    return written


# ---------------------------------------------------------------------------
# Entry point


def write_export_pack(
    project_dir: Path,
    blueprint: AssemblyBlueprint | None = None,
    settings: Settings | None = None,
) -> list[Path]:
    """Write pack/ captions, chapters, and thumbnails; return written paths.

    `blueprint` may be passed in (the finalize caller already holds it);
    otherwise it's loaded from blueprint.json. Returns [] when no blueprint
    exists - a pack without a timeline would be fiction.
    """
    blueprint = blueprint or _load_blueprint(project_dir)
    if blueprint is None:
        return []
    nodes = _load_nodes(project_dir)
    manifest = _load_manifest(project_dir)

    pack = project_dir / PACK_DIR
    pack.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    cues = _caption_cues(blueprint, nodes, manifest, project_dir, settings)
    if cues:
        srt = pack / "captions.srt"
        vtt = pack / "captions.vtt"
        _write_srt(cues, srt)
        _write_vtt(cues, vtt)
        written += [srt, vtt]

    chapters = _chapters(blueprint, nodes)
    if chapters:
        lines = [f"{_chapter_stamp(t)} {title}" for t, title in chapters]
        chapters_path = pack / "chapters.txt"
        chapters_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(chapters_path)

    written += _extract_thumbnails(
        _thumbnail_moments(blueprint, nodes), pack / _THUMBS_DIR, settings
    )
    return written
