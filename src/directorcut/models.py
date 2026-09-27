"""Pydantic models and schemas for DirectorCut-AI runtime data."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThematicTag(StrEnum):
    """Narrative intent tags."""

    EXPOSITION = "EXPOSITION"
    COMEDIC_SET = "COMEDIC_SET"
    EMOTIONAL_SPIKE = "EMOTIONAL_SPIKE"
    CALLBACK_REQ = "CALLBACK_REQ"


class EnergyLevel(StrEnum):
    """Spoken energy level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class VisualState(StrEnum):
    """Visual gameplay state tags."""

    STATE_UI = "STATE_UI"
    STATE_ACTION = "STATE_ACTION"
    STATE_DEATH = "STATE_DEATH"
    STATE_TUTORIAL = "STATE_TUTORIAL"


class AssetRole(StrEnum):
    """Role a dumped source file plays on the timeline."""

    A_ROLL = "a_roll"
    B_ROLL = "b_roll"
    VOICEOVER = "voiceover"
    MUSIC = "music"
    SFX = "sfx"
    IMAGE = "image"
    SCRIPT = "script"
    AUTO = "auto"


class AudioCueKind(StrEnum):
    """Kind of AI-generated audio cue."""

    MUSIC = "music"
    SFX = "sfx"


class NarrativeNode(BaseModel):
    """A single timestamped narrative segment."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(..., description="Stable narrative node identifier.")
    time_start: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    time_end: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    spoken_text: str
    thematic_tag: ThematicTag
    energy_level: EnergyLevel
    # Which source file the speech was transcribed from. Optional so
    # narratives/JSON produced before multi-A-roll ingest existed still
    # validate; single-source projects leave it unset.
    source_file: Path | None = None

    @field_validator("time_end")
    @classmethod
    def _end_after_start(cls, end: str, info) -> str:  # noqa: ANN001
        start = info.data.get("time_start")
        if start is None:
            return end
        if end <= start:
            raise ValueError("time_end must be after time_start")
        return end


class VisualEvent(BaseModel):
    """A gameplay state event detected from the visual proxy."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(..., description="Stable visual event identifier.")
    time_start: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    time_end: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    classification: VisualState
    visual_description: str
    # Which source video the event was observed in. Optional so blueprints /
    # JSON produced before multi-asset ingest existed still validate.
    source_file: Path | None = None


class Cut(BaseModel):
    """A single cut to be placed on an NLE timeline."""

    model_config = ConfigDict(frozen=True)

    track: Literal["V1_A1_PRIMARY", "V2_OVERLAY_BROLL", "A2_MUSIC", "A3_SFX"]
    source_file: Path
    timeline_in: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    timeline_out: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    source_in: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    source_out: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


class AudioCue(BaseModel):
    """A request for AI-generated audio (music bed or SFX stinger).

    Emitted by the Pass 3 assembly director; materialized into real audio
    files by the audio design step, which then appends A2_MUSIC / A3_SFX
    cuts to the blueprint.
    """

    model_config = ConfigDict(frozen=True)

    cue_id: str = Field(..., description="Stable audio cue identifier.")
    kind: AudioCueKind
    prompt: str = Field(..., description="Text prompt for the audio generator.")
    timeline_in: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    timeline_out: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


class TimelineMeta(BaseModel):
    """Metadata for the generated timeline."""

    project_name: str
    target_fps: float = 60.0


class AssemblyBlueprint(BaseModel):
    """Final cut-list output consumed by the FCPXML conformer."""

    timeline_meta: TimelineMeta
    cuts: list[Cut]
    # Kept on the blueprint so blueprint.json shows which cues the director
    # asked for even though the generated audio is referenced via `cuts`.
    audio_cues: list[AudioCue] = []


class Asset(BaseModel):
    """One dumped source file with its assigned timeline role."""

    path: Path
    role: AssetRole = AssetRole.AUTO
    duration_seconds: float | None = None
    # Number of audio streams embedded in a video file (probed at ingest).
    # Multi-track captures - e.g. OBS recording mic, game, and desktop audio
    # to separate streams - get each stream extracted as its own asset so
    # they can be classified and placed independently. 0/1 = nothing extra.
    audio_stream_count: int = 0
    # When media_prep baked a reframe/normalization into a new file, this
    # records the ingest original the baked file came from. Downstream
    # stages key on `path` (the baked file); `derived_from` exists for
    # provenance, healing a deleted bake, and matching extracted audio
    # streams that still carry the original filename stem.
    derived_from: Path | None = None
    # Short content description, filled by the asset-interpretation pass for
    # images and AUTO-classified files so Pass 3 can place them sensibly.
    description: str | None = None


class IngestManifest(BaseModel):
    """Validated inputs for a DirectorCut run."""

    project_name: str
    video_path: Path | None = None
    audio_path: Path | None = None
    voiceover_path: Path | None = None
    script_path: Path | None = None
    target_fps: float = 60.0
    duration_seconds: float | None = None
    # Multi-asset extension: every dumped file with a resolved role, plus the
    # free-text creative direction the user supplied (from direction.txt or
    # the web UI). The four single-path fields above remain the canonical
    # "primary" picks derived from `assets`.
    assets: list[Asset] = []
    direction: str | None = None

    @field_validator("video_path", "audio_path", "voiceover_path", "script_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> object:
        # Pass str through UNTOUCHED: pydantic coerces str -> Path natively in
        # both python and JSON validation modes. Pre-converting to Path here
        # feeds a Path object into JSON-mode validation, which only accepts
        # strings - that broke model_validate_json(model_dump_json())
        # round-trips (the rerun_pass3 workaround needed model_validate
        # instead). This hook now only normalizes non-str os.PathLike values.
        if value is None or isinstance(value, (str, Path)):
            return value
        return Path(value)

    def assets_with_role(self, *roles: AssetRole) -> list[Asset]:
        """Return all assets matching any of the given roles, in order."""
        wanted = set(roles)
        return [a for a in self.assets if a.role in wanted]
