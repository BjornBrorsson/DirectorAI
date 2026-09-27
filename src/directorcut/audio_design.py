"""Audio design: render AI-requested music/SFX cues into files and cuts.

The Pass 3 director emits `AudioCue` requests for audio that doesn't exist
in the ingest dump. This step renders each cue via ElevenLabs (or a
synthesized stub offline) and converts it into an A2_MUSIC / A3_SFX cut the
conformer can lay onto the timeline.
"""

from __future__ import annotations

from pathlib import Path

from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient
from directorcut.ingest import _probe_duration
from directorcut.log import get_logger
from directorcut.models import AudioCue, AudioCueKind, Cut
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)

_AUDIO_DIR_NAME = "audio"
_RENDERED_EXTS = ("mp3", "wav", "m4a")


def _existing_cue_file(audio_dir: Path, cue_id: str) -> Path | None:
    """Return an already-rendered cue file from a previous run, if any.

    A resumed pipeline re-runs render_cues on the checkpointed blueprint;
    reusing the rendered file keeps the paid audio API calls at zero for
    cues that already rendered before the interruption.
    """
    for ext in _RENDERED_EXTS:
        candidate = audio_dir / f"{cue_id}.{ext}"
        if candidate.exists():
            return candidate
    return None


def render_cues(
    cues: list[AudioCue],
    output_dir: Path,
    settings: Settings,
) -> list[Cut]:
    """Render each audio cue to a file and return the resulting cuts."""
    if not cues:
        return []

    client = ElevenLabsClient(settings)
    audio_dir = output_dir / _AUDIO_DIR_NAME
    audio_dir.mkdir(parents=True, exist_ok=True)

    cuts: list[Cut] = []
    for cue in cues:
        start = parse_timecode(cue.timeline_in)
        end = parse_timecode(cue.timeline_out)
        want = end - start
        if want <= 0:
            # A cue with no forward range would emit a nonsensical cut.
            logger.warning(
                "Skipping cue %s: timeline_out (%s) is not after timeline_in (%s)",
                cue.cue_id,
                cue.timeline_out,
                cue.timeline_in,
            )
            continue
        want = max(0.5, want)

        track = "A2_MUSIC" if cue.kind == AudioCueKind.MUSIC else "A3_SFX"
        out = _existing_cue_file(audio_dir, cue.cue_id)
        if out is None:
            if cue.kind == AudioCueKind.MUSIC:
                blob, ext = client.generate_music(cue.prompt, int(want * 1000))
            else:
                blob, ext = client.generate_sfx(cue.prompt, want)
            out = audio_dir / f"{cue.cue_id}.{ext}"
            out.write_bytes(blob)
            logger.info("Rendered %s cue %s -> %s", cue.kind.value, cue.cue_id, out)
        else:
            logger.info("Reusing rendered cue %s -> %s", cue.cue_id, out)

        # The generated file may run shorter/longer than requested; cut no
        # more than what was actually rendered.
        actual = _probe_duration(out) or want
        source_out = min(actual, want)
        cuts.append(
            Cut(
                track=track,  # type: ignore[arg-type]
                source_file=out.resolve(),
                timeline_in=cue.timeline_in,
                timeline_out=format_timecode(start + source_out),
                source_in="00:00:00.000",
                source_out=format_timecode(source_out),
            )
        )
    return cuts
