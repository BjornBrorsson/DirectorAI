import httpx
import pytest
import respx
from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.exceptions import GeminiError
from directorcut.gemini_client import GeminiClient

GEMINI_URL_PREFIX = "https://generativelanguage.googleapis.com/v1beta/models/"


class _Echo(BaseModel):
    value: str


@pytest.fixture
def live_settings():
    """Settings that route through the real (mocked) HTTP branch, not dev-mode stubs."""
    return Settings(gemini_api_key="real-test-key", env="prod")


def _gemini_success_response(value: str) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": f'{{"value": "{value}"}}'}]}},
        ]
    }


@respx.mock
def test_retries_on_503_then_succeeds(live_settings, monkeypatch):
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _seconds: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)
    route.side_effect = [
        httpx.Response(503, json={"error": "unavailable"}),
        httpx.Response(200, json=_gemini_success_response("ok")),
    ]

    client = GeminiClient(live_settings, max_retries=3, base_delay=0.01)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "ok"
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_max_retries(live_settings, monkeypatch):
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _seconds: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)
    route.mock(return_value=httpx.Response(500, json={"error": "boom"}))

    client = GeminiClient(live_settings, max_retries=3, base_delay=0.01)
    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo)

    assert route.call_count == 3


@respx.mock
def test_non_retryable_status_fails_fast(live_settings, monkeypatch):
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _seconds: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)
    route.mock(return_value=httpx.Response(400, json={"error": "bad request"}))

    client = GeminiClient(live_settings, max_retries=3, base_delay=0.01)
    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo)

    assert route.call_count == 1
