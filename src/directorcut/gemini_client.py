"""Gemini client with structured output and dev/test stubbing."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from directorcut.config import Settings
from directorcut.exceptions import GeminiError
from directorcut.log import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Transient failures worth retrying: rate limiting and server-side errors.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _fake_tag_narrative(nodes: list[Any]) -> dict[str, Any]:
    """Return a dev-mode tagging stub with exactly one entry per input node.

    Maps 1:1 by index so callers doing `zip(nodes, result, strict=True)` can
    never hit a length mismatch (a previous version derived the stub by
    re-splitting joined text, which could silently produce a different node
    count than the input and crash the zip). Tags are inferred from simple
    punctuation heuristics so the stub still exercises the CALLBACK_REQ /
    EMOTIONAL_SPIKE paths in tests.
    """
    entries = []
    for i, node in enumerate(nodes, start=1):
        text = getattr(node, "spoken_text", "")
        if text.rstrip().endswith("?"):
            tag = "CALLBACK_REQ"
        elif text.rstrip().endswith("!"):
            tag = "EMOTIONAL_SPIKE"
        else:
            tag = "EXPOSITION"
        entries.append(
            {
                "node_id": f"N_{i:03d}",
                "time_start": "00:00:00.000",
                "time_end": "00:00:05.000",
                "spoken_text": text,
                "thematic_tag": tag,
                "energy_level": "MEDIUM",
            }
        )
    return {"narrative_nodes": entries}


def _fake_visual(_frames: list[bytes]) -> dict[str, Any]:
    """Return a deterministic fake visual result for tests."""
    return {
        "visual_events": [
            {
                "event_id": "V_001",
                "time_start": "00:00:01.000",
                "time_end": "00:00:03.000",
                "classification": "STATE_ACTION",
                "visual_description": "Player runs through an open area.",
            }
        ]
    }


def _to_b64(blob: bytes) -> str:
    """Encode bytes as a URL-safe base64 string for Gemini inline data."""
    return base64.b64encode(blob).decode("ascii")


class GeminiClient:
    """Minimal Gemini client with structured output, retries, and dev fallback."""

    def __init__(
        self,
        settings: Settings,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self.settings = settings
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._http = httpx.Client(timeout=120.0)

    def call(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        dev_mode_override: dict[str, Any] | None = None,
    ) -> T:
        """Call Gemini with a prompt and optional media, returning a validated model."""
        if self.settings.dev_mode or self.settings.safe_api_key is None:
            logger.info("Dev/test mode: returning stub response for %s", response_model.__name__)
            stub = dev_mode_override or {}
            try:
                return response_model.model_validate(stub)
            except ValidationError as exc:
                raise GeminiError(f"Stub response invalid: {exc}") from exc

        api_key = self.settings.safe_api_key
        model = self.settings.gemini_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={api_key}"
        )
        parts: list[dict[str, Any]] = [{"text": prompt}]
        if media:
            for blob in media:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": _to_b64(blob),
                        }
                    }
                )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": response_model.model_json_schema(),
            },
        }

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._http.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                return response_model.model_validate(parsed)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            except httpx.TransportError as exc:
                last_exc = exc
            except (KeyError, json.JSONDecodeError, ValidationError) as exc:
                # Malformed response - not a transient network issue, don't retry.
                logger.error("Gemini call failed: %s", exc)
                raise GeminiError(f"Gemini call failed: {exc}") from exc

            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.warning(
                    "Gemini call failed (attempt %d/%d): %s - retrying in %.1fs",
                    attempt + 1,
                    self.max_retries,
                    last_exc,
                    delay,
                )
                time.sleep(delay)

        logger.error("Gemini call failed after %d attempt(s): %s", self.max_retries, last_exc)
        raise GeminiError(f"Gemini call failed after {self.max_retries} attempt(s): {last_exc}")
