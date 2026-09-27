import json
from pathlib import Path

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


# Key env vars the tests manage - cleared before each fixture so neither the
# repo .env nor the real environment can leak a credential into a test. NOTE:
# these are set via monkeypatch.setenv, never Settings(...) kwargs - two
# fields share the GEMINI_PAID_KEY_MAIN alias and pydantic-settings' init
# kwarg restore raises KeyError when such fields are passed by name.
_GEMINI_KEY_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_PAID_KEY_MAIN",
    "GEMINI_PAID_KEY",
    "GEMINI_FREE_KEY_MAIN",
    "GEMINI_FREE_KEY",
)


@pytest.fixture
def live_settings(monkeypatch):
    """Settings that route through the real (mocked) HTTP branch, not dev-mode stubs."""
    # Pin the HTTP backend explicitly: a local .env may set
    # GEMINI_BACKEND=antigravity, which would leak into Settings() and route
    # these tests through the CLI branch instead of the mocked HTTP one.
    # _env_file=None + cleared key pool: the repo .env carries REAL keys
    # (GEMINI_PAID_KEY_MAIN etc.) which must never enter a test's key chain
    # or its log output.
    for var in _GEMINI_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "real-test-key")
    return Settings(_env_file=None, env="prod", gemini_backend="api")


def _gemini_success_response(value: str) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": f'{{"value": "{value}"}}'}]}},
        ]
    }


@respx.mock
def test_call_model_override_changes_url(live_settings, monkeypatch):
    """`call(..., model=...)` swaps the endpoint model for the API backend."""
    route = respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(200, json=_gemini_success_response("ok"))
    )

    client = GeminiClient(live_settings)
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, model="gemini-fast"
    )

    assert result.value == "ok"
    assert "models/gemini-fast:generateContent" in str(
        route.calls[0].request.url
    )


@respx.mock
def test_falls_back_to_response_schema_on_400(live_settings, monkeypatch):
    """Endpoints without response_json_schema get the sanitized subset."""
    monkeypatch.setattr(
        "directorcut.gemini_client.time.sleep", lambda _seconds: None
    )
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)
    route.side_effect = [
        httpx.Response(
            400,
            json={"error": {"message": "Unknown field response_json_schema"}},
        ),
        httpx.Response(200, json=_gemini_success_response("ok")),
    ]

    client = GeminiClient(live_settings, max_retries=3, base_delay=0.01)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "ok"
    assert route.call_count == 2
    second = json.loads(route.calls[1].request.content)
    assert "response_schema" in second["generationConfig"]
    assert "response_json_schema" not in second["generationConfig"]


def test_gemini_schema_inlines_refs_and_strips():
    """The fallback schema subset inlines $defs and drops unsupported keys."""
    from directorcut.assembly.director import _AssemblyPlan
    from directorcut.gemini_client import _gemini_schema

    schema = _gemini_schema(_AssemblyPlan.model_json_schema())

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in (
                    "$ref",
                    "$defs",
                    "definitions",
                    "title",
                    "pattern",
                    "default",
                    "additionalProperties",
                )
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    # Cut was $ref'd - after inlining its fields sit directly on items.
    cut_props = schema["properties"]["cuts"]["items"]["properties"]
    assert cut_props["track"]["enum"] == [
        "V1_A1_PRIMARY",
        "V2_OVERLAY_BROLL",
        "A2_MUSIC",
        "A3_SFX",
    ]
    assert "timeline_in" in cut_props


def test_model_for_prefers_fast_model():
    s = Settings(gemini_model="big", gemini_model_fast="fast")
    assert s.model_for("visual") == "fast"
    assert s.model_for("interpret") == "fast"
    assert s.model_for("narrative") == "big"
    assert s.model_for("director") == "big"
    assert s.model_for("audio") == "big"
    # Pin fast=None explicitly: a real .env may set GEMINI_MODEL_FAST and
    # leak into Settings() through the dotenv source.
    fallback = Settings(gemini_model="big", gemini_model_fast=None)
    assert fallback.model_for("visual") == "big"


def test_gemini_paid_key_alias(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_KEY_MAIN", raising=False)
    monkeypatch.delenv("GEMINI_FREE_KEY", raising=False)
    monkeypatch.setenv("GEMINI_PAID_KEY", "paid-key")
    # _env_file=None: the repo .env carries a real GEMINI_PAID_KEY_MAIN, which
    # outranks GEMINI_PAID_KEY in the alias list and would mask this.
    assert Settings(_env_file=None).gemini_api_key == "paid-key"


def test_gemini_paid_key_main_outranks_other_aliases(monkeypatch):
    """GEMINI_PAID_KEY_MAIN is the highest-priority alias for the primary key."""
    for var in ("GEMINI_API_KEY", "GEMINI_PAID_KEY", "GEMINI_FREE_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "generic-key")
    monkeypatch.setenv("GEMINI_PAID_KEY", "paid-key")
    monkeypatch.setenv("GEMINI_PAID_KEY_MAIN", "main-key")
    s = Settings(_env_file=None)
    assert s.gemini_api_key == "main-key"
    assert s.gemini_paid_key_main == "main-key"
    assert s.gemini_paid_key == "paid-key"


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


# --- Gemini CLI backend -----------------------------------------------------


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def cli_settings():
    return Settings(
        _env_file=None, env="prod", gemini_backend="cli", gemini_cli_bin="gemini"
    )


def _stub_cli(monkeypatch, proc):
    monkeypatch.setattr(
        "directorcut.gemini_client._find_cli_binary", lambda s: Path("gemini")
    )
    monkeypatch.setattr(
        "directorcut.gemini_client.subprocess.run", lambda *a, **kw: proc
    )


def test_cli_backend_parses_json_envelope(cli_settings, monkeypatch):
    """CLI stdout may carry log lines before the JSON envelope."""
    payload = json.dumps({"response": '{"value": "from-cli"}', "stats": {}})
    _stub_cli(monkeypatch, _Proc(stdout=f"Loaded cached credentials.\n{payload}"))

    client = GeminiClient(cli_settings)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "from-cli"


def test_cli_backend_strips_code_fence(cli_settings, monkeypatch):
    fenced = '```json\n{"value": "fenced"}\n```'
    payload = json.dumps({"response": fenced})
    _stub_cli(monkeypatch, _Proc(stdout=payload))

    client = GeminiClient(cli_settings)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "fenced"


def test_cli_backend_nonzero_exit_raises(cli_settings, monkeypatch):
    _stub_cli(monkeypatch, _Proc(stderr="auth failed", returncode=1))

    client = GeminiClient(cli_settings)
    with pytest.raises(GeminiError, match="auth failed"):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_cli_backend_no_json_raises(cli_settings, monkeypatch):
    _stub_cli(monkeypatch, _Proc(stdout="plain text with no json"))

    client = GeminiClient(cli_settings)
    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_dev_mode_stubs_even_in_cli_backend(cli_settings, monkeypatch):
    """Dev mode must never invoke the CLI binary."""
    settings = Settings(env="dev", gemini_backend="cli")
    monkeypatch.setattr(
        "directorcut.gemini_client.subprocess.run",
        lambda *a, **kw: pytest.fail("CLI should not run in dev mode"),
    )
    client = GeminiClient(settings)
    result = client.call(
        prompt="hi",
        media=None,
        response_model=_Echo,
        dev_mode_override={"value": "stubbed"},
    )
    assert result.value == "stubbed"


# --- Antigravity (agy) backend -----------------------------------------------


@pytest.fixture
def agy_settings():
    return Settings(
        _env_file=None,
        env="prod",
        gemini_backend="antigravity",
        antigravity_cli_bin="agy",
    )


def _stub_agy(monkeypatch, proc):
    monkeypatch.setattr(
        "directorcut.gemini_client._find_agy_binary", lambda s: Path("agy")
    )
    monkeypatch.setattr(
        "directorcut.gemini_client.subprocess.run", lambda *a, **kw: proc
    )


def _agy_envelope(**fields) -> str:
    base = {"conversation_id": "c1", "status": "SUCCESS", "response": "", "usage": {}}
    base.update(fields)
    return json.dumps(base)


def test_agy_backend_reads_response_json(agy_settings, monkeypatch):
    stdout = _agy_envelope(response='{"value": "from-response"}')
    _stub_agy(monkeypatch, _Proc(stdout=stdout))

    client = GeminiClient(agy_settings)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "from-response"


def test_agy_backend_tolerates_tool_call_junk(agy_settings, monkeypatch):
    """The last valid JSON object wins over embedded tool-call artifacts."""
    response = (
        'Here is the answer:\n{"value": "real"}\n'
        '{"toolAction": "Finishing task", "toolSummary": "done"}\n'
    )
    _stub_agy(monkeypatch, _Proc(stdout=_agy_envelope(response=response)))

    client = GeminiClient(agy_settings)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "real"


def test_agy_backend_error_status_raises(agy_settings, monkeypatch):
    stdout = _agy_envelope(status="ERROR", error="authentication required")
    _stub_agy(monkeypatch, _Proc(stdout=stdout, returncode=1))

    client = GeminiClient(agy_settings)
    with pytest.raises(GeminiError, match="authentication required"):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_agy_backend_nonzero_exit_no_json_raises(agy_settings, monkeypatch):
    _stub_agy(monkeypatch, _Proc(stderr="boom", returncode=1))

    client = GeminiClient(agy_settings)
    with pytest.raises(GeminiError, match="boom"):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_agy_backend_no_json_raises(agy_settings, monkeypatch):
    _stub_agy(monkeypatch, _Proc(stdout="plain text, no json"))

    client = GeminiClient(agy_settings)
    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_agy_backend_media_cwd_and_prompt_schema(agy_settings, monkeypatch):
    """The run must cd into the media dir and embed the schema in the prompt."""
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        captured["media_file"] = (
            Path(kw["cwd"]) / "media_0000.jpg"
        ).exists()
        captured["prompt_txt"] = (Path(kw["cwd"]) / "prompt.txt").read_text(
            encoding="utf-8"
        )
        stdout = _agy_envelope(response='{"value": "ok"}')
        return _Proc(stdout=stdout)

    monkeypatch.setattr(
        "directorcut.gemini_client._find_agy_binary", lambda s: Path("agy")
    )
    monkeypatch.setattr("directorcut.gemini_client.subprocess.run", fake_run)

    client = GeminiClient(agy_settings)
    result = client.call(prompt="hi", media=[b"jpeg-bytes"], response_model=_Echo)

    assert result.value == "ok"
    assert captured["media_file"] is True
    assert captured["cwd"] is not None
    args = captured["cmd"]
    assert "--json-schema" not in args
    assert "--dangerously-skip-permissions" in args
    # The real prompt (incl. schema) lives in prompt.txt; the -p arg is a
    # short wrapper so the command line stays under the Windows limit.
    prompt_arg = args[args.index("-p") + 1]
    assert "prompt.txt" in prompt_arg
    assert len(prompt_arg) < 1000
    assert json.loads(captured["prompt_txt"].split("JSON schema:")[1].split(
        "No markdown"
    )[0])["type"] == "object"


def test_agy_backend_reads_result_file(agy_settings, monkeypatch):
    """An empty `response` is fine when the agent wrote result.json."""
    def fake_run(cmd, **kw):
        (Path(kw["cwd"]) / "result.json").write_text('{"value": "from-file"}')
        return _Proc(stdout=_agy_envelope(response=""))

    monkeypatch.setattr(
        "directorcut.gemini_client._find_agy_binary", lambda s: Path("agy")
    )
    monkeypatch.setattr("directorcut.gemini_client.subprocess.run", fake_run)

    client = GeminiClient(agy_settings)
    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "from-file"


def test_agy_backend_tiles_multi_frame_media(agy_settings, monkeypatch):
    """Multiple frames become one contact sheet to save agent round-trips."""
    captured: dict = {}

    def fake_ffmpeg(args, settings=None):
        captured["ffmpeg_args"] = args
        out = Path(args[-1])
        out.write_bytes(b"sheet")

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["prompt_txt"] = (Path(kw["cwd"]) / "prompt.txt").read_text(
            encoding="utf-8"
        )
        return _Proc(stdout=_agy_envelope(response='{"value": "ok"}'))

    monkeypatch.setattr(
        "directorcut.gemini_client._find_agy_binary", lambda s: Path("agy")
    )
    monkeypatch.setattr("directorcut.gemini_client.run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("directorcut.gemini_client.subprocess.run", fake_run)

    client = GeminiClient(agy_settings)
    result = client.call(
        prompt="hi", media=[b"f1", b"f2", b"f3"], response_model=_Echo
    )

    assert result.value == "ok"
    # The media note now travels inside prompt.txt, not the -p arg.
    assert "media_sheet.jpg" in captured["prompt_txt"]
    assert any("2x2" in str(a) for a in captured["ffmpeg_args"])


def test_agy_backend_denied_actions_error(agy_settings, monkeypatch):
    """Empty response + denied_actions should produce an actionable error."""
    stdout = _agy_envelope(
        response="",
        denied_actions=[{"action": "command", "display_name": "RunCommand"}],
    )
    _stub_agy(monkeypatch, _Proc(stdout=stdout))

    client = GeminiClient(agy_settings)
    with pytest.raises(GeminiError, match="denied tool actions"):
        client.call(prompt="hi", media=None, response_model=_Echo)


def test_agy_backend_model_flag(agy_settings, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        stdout = _agy_envelope(response='{"value": "ok"}')
        return _Proc(stdout=stdout)

    monkeypatch.setattr(
        "directorcut.gemini_client._find_agy_binary", lambda s: Path("agy")
    )
    monkeypatch.setattr("directorcut.gemini_client.subprocess.run", fake_run)

    settings = Settings(
        env="prod",
        gemini_backend="antigravity",
        antigravity_cli_bin="agy",
        antigravity_cli_model="gemini-3.8-flash-medium",
    )
    GeminiClient(settings).call(prompt="hi", media=None, response_model=_Echo)

    args = captured["cmd"]
    assert args[args.index("--model") + 1] == "gemini-3.8-flash-medium"


def test_agy_dev_mode_stubs(monkeypatch):
    """Dev mode must never invoke the agy binary."""
    settings = Settings(env="dev", gemini_backend="antigravity")
    monkeypatch.setattr(
        "directorcut.gemini_client.subprocess.run",
        lambda *a, **kw: pytest.fail("agy should not run in dev mode"),
    )
    client = GeminiClient(settings)
    result = client.call(
        prompt="hi",
        media=None,
        response_model=_Echo,
        dev_mode_override={"value": "stubbed"},
    )
    assert result.value == "stubbed"


def test_timeout_seconds():
    from directorcut.gemini_client import _timeout_seconds

    assert _timeout_seconds("15m") == 960.0
    assert _timeout_seconds("90s") == 150.0
    assert _timeout_seconds("1h") == 3660.0
    assert _timeout_seconds("bogus") == 960.0


# --- Key + model failover chain (API backend) --------------------------------


@pytest.fixture
def chained_settings(monkeypatch):
    """Factory: prod API settings with a 3-key, 2-model chain, no .env leakage."""
    base = {
        "GEMINI_PAID_KEY_MAIN": "main-key",
        "GEMINI_PAID_KEY": "paid-key",
        "GEMINI_FREE_KEY": "free-key",
        "GEMINI_MODEL_CHAIN_DIRECTOR": "model-a,model-b",
    }

    def make(**overrides):
        for var in _GEMINI_KEY_VARS:
            monkeypatch.delenv(var, raising=False)
        env = {**base, **overrides}
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return Settings(_env_file=None, env="prod", gemini_backend="api")

    return make


def _key_of(request) -> str:
    from httpx import QueryParams

    return QueryParams(request.url.params).get("key", "")


@respx.mock
def test_chain_falls_to_next_model_on_persistent_429(monkeypatch, chained_settings):
    """Persistent quota-exhaustion on model 1 falls through to model 2."""
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    route_a = respx.post(url__startswith=GEMINI_URL_PREFIX + "model-a:").mock(
        return_value=httpx.Response(429, json={"error": "quota exhausted"})
    )
    route_b = respx.post(url__startswith=GEMINI_URL_PREFIX + "model-b:").mock(
        return_value=httpx.Response(200, json=_gemini_success_response("ok"))
    )

    client = GeminiClient(chained_settings(), max_retries=2, base_delay=0.0)
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, task="director"
    )

    assert result.value == "ok"
    assert route_a.call_count == 2  # retries exhausted on model-a
    assert route_b.call_count == 1  # then model-b on the SAME key
    assert _key_of(route_b.calls[0].request) == "free-key"


@respx.mock
def test_chain_falls_to_next_key_when_models_exhausted(
    monkeypatch, chained_settings
):
    """Every model 429ing on key 1 promotes the next key in the chain."""
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)

    def side_effect(request):
        if _key_of(request) == "paid-key":
            return httpx.Response(200, json=_gemini_success_response("paid"))
        return httpx.Response(429, json={"error": "quota"})

    route.side_effect = side_effect

    client = GeminiClient(chained_settings(), max_retries=1, base_delay=0.0)
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, task="director"
    )

    assert result.value == "paid"
    keys = [_key_of(c.request) for c in route.calls]
    # Free key tries both models, then the paid-main key tries both.
    assert keys[:4] == ["free-key", "free-key", "main-key", "main-key"]
    assert keys[4] == "paid-key"


@respx.mock
def test_fast_task_prefers_free_key(monkeypatch, chained_settings):
    """visual/interpret burn GEMINI_FREE_KEY before touching paid quota."""
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(200, json=_gemini_success_response("ok"))
    )

    client = GeminiClient(chained_settings())
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, task="visual"
    )

    assert result.value == "ok"
    assert _key_of(route.calls[0].request) == "free-key"


def test_key_chain_ordering(chained_settings):
    s = chained_settings()
    # Free-first for every task (2026-09-27 policy): paid keys are the
    # safety net once free capacity rate-limits.
    for task in ("visual", "interpret", "director", "narrative", "audio"):
        assert s.key_chain(task) == ["free-key", "main-key", "paid-key"]


def test_free_key_main_leads_the_chain(chained_settings):
    s = chained_settings(GEMINI_FREE_KEY_MAIN="free-main-key")
    assert s.key_chain("director") == [
        "free-main-key",
        "free-key",
        "main-key",
        "paid-key",
    ]
    # Single configured key collapses to a one-entry chain (old behaviour).
    solo = chained_settings(
        GEMINI_PAID_KEY_MAIN=None,
        GEMINI_PAID_KEY=None,
        GEMINI_FREE_KEY=None,
        GEMINI_API_KEY="only-key",
    )
    assert solo.key_chain("director") == ["only-key"]
    assert solo.key_chain("visual") == ["only-key"]


def test_model_chain_env_and_default(chained_settings):
    s = chained_settings()
    assert s.model_chain("director") == ["model-a", "model-b"]
    # No chain env -> the single configured model, exactly as before.
    assert s.model_chain("narrative") == [s.gemini_model]
    s_fast = chained_settings(GEMINI_MODEL_FAST="fast-m")
    assert s_fast.model_chain("visual") == ["fast-m"]


def test_no_key_raises_clear_error(monkeypatch):
    for var in _GEMINI_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None, env="prod", gemini_backend="api")
    client = GeminiClient(s)
    with pytest.raises(GeminiError, match="No Gemini API key"):
        client.call(prompt="hi", media=None, response_model=_Echo, task="visual")


@respx.mock
def test_dead_key_rotates_without_burning_model_retries(
    monkeypatch, chained_settings
):
    """A 401 on key 1 skips the model chain on that key entirely."""
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX)

    def side_effect(request):
        if _key_of(request) == "free-key":
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json=_gemini_success_response("rotated"))

    route.side_effect = side_effect

    client = GeminiClient(chained_settings(), max_retries=3, base_delay=0.0)
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, task="director"
    )

    assert result.value == "rotated"
    keys = [_key_of(c.request) for c in route.calls]
    # One 401 on key 1 -> straight to key 2 (no model-2 retry on a dead key).
    assert keys == ["free-key", "main-key"]


def test_agy_retries_validation_error_with_corrective_hint(agy_settings, monkeypatch):
    """A schema-invalid agent reply retries with feedback instead of killing
    the pipeline (a real run died when a tagging node came back with
    time_end == time_start)."""
    prompts = []

    def fake_once(self, prompt, media, response_model):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise GeminiError(
                "agy CLI response invalid: 1 validation error for _NarrativeNodeList"
            )
        return _Echo(value="fixed")

    monkeypatch.setattr(GeminiClient, "_run_agy_once", fake_once)
    client = GeminiClient(agy_settings, max_retries=3, base_delay=0.0)

    result = client.call(prompt="hi", media=None, response_model=_Echo)

    assert result.value == "fixed"
    assert len(prompts) == 2
    assert "previous response could not be validated" in prompts[1]


def test_agy_nontransient_error_raises_immediately(agy_settings, monkeypatch):
    """Errors that are neither transient nor bad-response (e.g. the binary
    exiting non-zero) can't be fixed by a retry - fail fast."""
    calls = []

    def fake_once(self, prompt, media, response_model):
        calls.append(prompt)
        raise GeminiError("agy CLI exited 2: unknown flag")

    monkeypatch.setattr(GeminiClient, "_run_agy_once", fake_once)
    client = GeminiClient(agy_settings, max_retries=3, base_delay=0.0)

    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo)

    assert len(calls) == 1


def test_agy_media_sampling_over_frame_cap(agy_settings, tmp_path, monkeypatch):
    """Pass-2 chunks on long videos carry thousands of 1fps frames - the
    media set is capped + evenly sampled with the stride reported, or the
    single contact sheet becomes an unreadable mega-image."""
    calls = []
    monkeypatch.setattr(
        "directorcut.gemini_client.run_ffmpeg",
        lambda args, settings=None: calls.append(args),
    )
    client = GeminiClient(agy_settings)
    media = [f"frame{i}".encode() for i in range(40)]

    note = client._write_agy_media(media, tmp_path)

    assert len(list(tmp_path.glob("media_*.jpg"))) == 16
    assert "sampled evenly" in note
    assert "40-frame sequence" in note
    assert "media_sheet.jpg" in note
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "tile=4x4" in vf


# --- Backend fallback: exhausted API plan -> antigravity CLI -----------------


@respx.mock
def test_backend_fallback_to_agy_on_chain_exhaustion(monkeypatch, chained_settings):
    """Every key x model dead -> DIRECTORCUT_BACKEND_FALLBACK=antigravity
    routes the call through the agy CLI instead of failing the run."""
    monkeypatch.setenv("DIRECTORCUT_BACKEND_FALLBACK", "antigravity")
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    route = respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(429, json={"error": "quota"})
    )

    seen: list[dict] = []

    def _fake_agy(self, prompt, media, response_model, task=None):
        seen.append({"prompt": prompt, "task": task})
        return response_model.model_validate({"value": "via-agy"})

    monkeypatch.setattr(GeminiClient, "_call_agy", _fake_agy)

    client = GeminiClient(chained_settings(), max_retries=1, base_delay=0.0)
    result = client.call(
        prompt="hi", media=None, response_model=_Echo, task="director"
    )

    assert result.value == "via-agy"
    assert seen == [{"prompt": "hi", "task": "director"}]
    assert route.call_count > 0  # the API plan really was exhausted first


@respx.mock
def test_backend_fallback_disabled_by_default(monkeypatch, chained_settings):
    """Without DIRECTORCUT_BACKEND_FALLBACK, chain exhaustion just raises."""
    monkeypatch.delenv("DIRECTORCUT_BACKEND_FALLBACK", raising=False)
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(429, json={"error": "quota"})
    )
    agy_calls = []
    monkeypatch.setattr(
        GeminiClient,
        "_call_agy",
        lambda *a, **k: agy_calls.append(1) or _Echo(value="nope"),
    )

    client = GeminiClient(chained_settings(), max_retries=1, base_delay=0.0)
    with pytest.raises(GeminiError, match="key"):
        client.call(prompt="hi", media=None, response_model=_Echo, task="director")
    assert agy_calls == []


@respx.mock
def test_backend_fallback_skipped_on_request_bug(monkeypatch, chained_settings):
    """A malformed request (HTTP 400 abort) is not retried on agy - the
    failure is in the request, not the credentials."""
    monkeypatch.setenv("DIRECTORCUT_BACKEND_FALLBACK", "antigravity")
    monkeypatch.setattr("directorcut.gemini_client.time.sleep", lambda _s: None)
    respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    agy_calls = []
    monkeypatch.setattr(
        GeminiClient,
        "_call_agy",
        lambda *a, **k: agy_calls.append(1) or _Echo(value="nope"),
    )

    client = GeminiClient(chained_settings(), max_retries=1, base_delay=0.0)
    with pytest.raises(GeminiError):
        client.call(prompt="hi", media=None, response_model=_Echo, task="director")
    assert agy_calls == []


def test_repair_truncated_json_closes_open_tail():
    from directorcut.gemini_client import _repair_truncated_json

    # Cut mid-string inside a nested list response - the partial trailing
    # element is salvaged as far as valid JSON allows.
    text = (
        '{"narrative_nodes": [{"node_id": "N_001", "thematic_tag": '
        '"EXPOSITION"}, {"node_id": "N_002", "thematic_'
    )
    out = _repair_truncated_json(text)
    assert out["narrative_nodes"][0] == {
        "node_id": "N_001",
        "thematic_tag": "EXPOSITION",
    }
    assert out["narrative_nodes"][1]["node_id"] == "N_002"


def test_repair_truncated_json_partial_literal():
    from directorcut.gemini_client import _repair_truncated_json

    # Truncated mid-value token - drops the incomplete element.
    out = _repair_truncated_json('{"a": [1, 2, tru')
    assert out == {"a": [1, 2]}


def test_repair_truncated_json_rejects_non_truncation():
    from directorcut.gemini_client import _repair_truncated_json

    assert _repair_truncated_json("{}") is None
    assert _repair_truncated_json("") is None
    assert _repair_truncated_json("{}} garbage") is None
