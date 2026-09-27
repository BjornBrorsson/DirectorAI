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


class ElevenLabsError(DirectorCutError):
    """ElevenLabs API invocation failed."""


class BudgetError(DirectorCutError):
    """Token or cost budget exceeded."""


# Name pinned by the ticket spec ("a BudgetExceeded error") - noqa N818.
class BudgetExceeded(BudgetError):  # noqa: N818
    """Run-wide USD cost cap (DIRECTORCUT_COST_CAP_USD) exceeded mid-run.

    Subclasses BudgetError so existing `except BudgetError` handlers keep
    catching it; carries a message meant to be shown to the user verbatim.
    """


class ConformError(DirectorCutError):
    """FCPXML generation failed."""
