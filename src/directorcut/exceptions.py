"""Domain exceptions for DirectorCut-AI."""

from __future__ import annotations


class DirectorCutError(Exception):
    """Base exception."""


class IngestError(DirectorCutError):
    """Invalid or missing ingest inputs."""


class FFmpegError(DirectorCutError):
    """FFmpeg/FFprobe invocation or discovery failed."""


class GeminiError(DirectorCutError):
    """Gemini API or structured-output validation failed."""


class BudgetError(DirectorCutError):
    """Token or cost budget exceeded."""


class ConformError(DirectorCutError):
    """FCPXML generation failed."""
