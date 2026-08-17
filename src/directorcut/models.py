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


class NarrativeNode(BaseModel):
    """A single timestamped narrative segment."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(..., description="Stable narrative node identifier.")
    time_start: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    time_end: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    spoken_text: str
    thematic_tag: ThematicTag
    energy_level: EnergyLevel

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


class Cut(BaseModel):
    """A single cut to be placed on an NLE timeline."""

    model_config = ConfigDict(frozen=True)

    track: Literal["V1_A1_PRIMARY", "V2_OVERLAY_BROLL"]
    source_file: Path
    timeline_in: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    timeline_out: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    source_in: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    source_out: str = Field(..., pattern=r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


class TimelineMeta(BaseModel):
    """Metadata for the generated timeline."""

    project_name: str
    target_fps: float = 60.0


class AssemblyBlueprint(BaseModel):
    """Final cut-list output consumed by the FCPXML conformer."""

    timeline_meta: TimelineMeta
    cuts: list[Cut]


class IngestManifest(BaseModel):
    """Validated inputs for a DirectorCut run."""

    project_name: str
    video_path: Path | None = None
    audio_path: Path | None = None
    voiceover_path: Path | None = None
    script_path: Path | None = None
    target_fps: float = 60.0
    duration_seconds: float | None = None

    @field_validator("video_path", "audio_path", "voiceover_path", "script_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path | None) -> Path | None:  # noqa: ANN001
        if value is None or isinstance(value, Path):
            return value
        return Path(value)
