"""Cost ledger + DIRECTORCUT_COST_CAP_USD (cost-accounting ticket).

All outbound calls are respx-mocked HTTP or dev-mode stubs - no real API
traffic. The ledger is activated explicitly per test so the suite never
depends on a pipeline run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner
from pydantic import BaseModel

from directorcut.cli import cli
from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient
from directorcut.exceptions import BudgetExceeded
from directorcut.gemini_client import GeminiClient
from directorcut.models import Asset, AssetRole, IngestManifest
from directorcut.safety.ledger import (
    CostLedger,
    activate_cost_ledger,
    estimate_text_tokens,
)

GEMINI_URL_PREFIX = "https://generativelanguage.googleapis.com/v1beta/models/"
_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_MUSIC_URL = "https://api.elevenlabs.io/v1/music"

_GEMINI_KEY_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_PAID_KEY_MAIN",
    "GEMINI_PAID_KEY",
    "GEMINI_FREE_KEY_MAIN",
    "GEMINI_FREE_KEY",
)


class _Echo(BaseModel):
    value: str


def _ok_body(value: str = "ok") -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps({"value": value})}]}}
        ]
    }


@pytest.fixture
def live_settings(tmp_path, monkeypatch):
    """API-backend settings with a fake key; the repo .env must not leak in."""
    for var in _GEMINI_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "real-test-key")
    return Settings(
        _env_file=None,
        env="prod",
        gemini_backend="api",
        output_dir=tmp_path,
    )


def _read_costs(project_dir: Path) -> dict:
    return json.loads((project_dir / "costs.json").read_text(encoding="utf-8"))


@respx.mock
def test_ledger_records_gemini_call_by_task_and_model(live_settings, tmp_path):
    respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(200, json=_ok_body())
    )
    project = tmp_path / "proj"
    project.mkdir()

    with activate_cost_ledger(project):
        client = GeminiClient(live_settings)
        client.call(prompt="tag these lines", media=None,
                    response_model=_Echo, task="narrative")

    data = _read_costs(project)
    assert data["version"] == 1
    assert len(data["calls"]) == 1
    entry = data["calls"][0]
    assert entry["kind"] == "gemini"
    assert entry["task"] == "narrative"
    assert entry["model"] == live_settings.gemini_model
    assert entry["input_tokens"] >= estimate_text_tokens("tag these lines")
    assert entry["output_tokens"] > 0
    assert entry["cost_usd"] > 0
    totals = data["totals"]
    assert totals["gemini_calls"] == 1
    # Totals are rounded to 6 decimals on flush.
    assert totals["cost_usd"] == pytest.approx(entry["cost_usd"], abs=1e-6)
    assert totals["by_task"]["narrative"]["calls"] == 1


@respx.mock
def test_ledger_accumulates_across_activations(live_settings, tmp_path):
    """A resumed run / later patch call must merge into the same file."""
    respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
        return_value=httpx.Response(200, json=_ok_body())
    )
    project = tmp_path / "proj"
    project.mkdir()

    for _ in range(2):
        with activate_cost_ledger(project):
            GeminiClient(live_settings).call(
                prompt="p", media=None, response_model=_Echo, task="director"
            )

    data = _read_costs(project)
    assert len(data["calls"]) == 2
    assert data["totals"]["gemini_calls"] == 2


def test_cap_breach_raises_after_recording(tmp_path, monkeypatch):
    """The call that pushes spend over the cap is recorded, then aborts."""
    for var in _GEMINI_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "real-test-key")
    settings = Settings(
        _env_file=None,
        env="prod",
        gemini_backend="api",
        output_dir=tmp_path,
        cost_cap_usd=0.0000001,  # below any single call's price
    )
    project = tmp_path / "proj"
    project.mkdir()

    with respx.mock:
        route = respx.post(url__startswith=GEMINI_URL_PREFIX).mock(
            return_value=httpx.Response(200, json=_ok_body())
        )
        with activate_cost_ledger(project, cap_usd=settings.cost_cap_usd):
            client = GeminiClient(settings)
            with pytest.raises(BudgetExceeded, match="Cost cap exceeded"):
                client.call(prompt="x" * 4000, media=None,
                            response_model=_Echo, task="director")
            # The breaching call was still recorded.
            assert _read_costs(project)["totals"]["cost_usd"] > 0
            # Pre-flight check: the NEXT call aborts before hitting HTTP.
            with pytest.raises(BudgetExceeded):
                client.call(prompt="hi", media=None,
                            response_model=_Echo, task="director")
        assert route.call_count == 1


def test_dev_stub_calls_record_zero_cost(tmp_path, monkeypatch):
    """Dev-mode stubs land in the ledger (token estimate kept) at cost 0 -
    they can never breach the cap."""
    monkeypatch.delenv("DIRECTORCUT_COST_CAP_USD", raising=False)
    settings = Settings(_env_file=None, env="test", output_dir=tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    with activate_cost_ledger(project, cap_usd=0.0000001):
        GeminiClient(settings).call(
            prompt="hello",
            media=None,
            response_model=_Echo,
            dev_mode_override={"value": "ok"},
            task="visual",
        )

    data = _read_costs(project)
    assert len(data["calls"]) == 1
    assert data["calls"][0]["stub"] is True
    assert data["calls"][0]["cost_usd"] == 0.0
    assert data["calls"][0]["input_tokens"] > 0
    assert data["totals"]["cost_usd"] == 0.0


@respx.mock
def test_elevenlabs_stt_records_audio_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")
    monkeypatch.setattr(
        "directorcut.elevenlabs_client._probe_duration", lambda p: 120.0
    )
    respx.post(_STT_URL).mock(
        return_value=httpx.Response(200, json={"words": []})
    )
    settings = Settings(
        _env_file=None, env="prod", output_dir=tmp_path
    )
    project = tmp_path / "proj"
    project.mkdir()
    audio = tmp_path / "vo.mp3"
    audio.write_bytes(b"fake-audio")

    with activate_cost_ledger(project):
        ElevenLabsClient(settings).speech_to_text(audio)

    entry = _read_costs(project)["calls"][0]
    assert entry["kind"] == "elevenlabs_stt"
    assert entry["seconds"] == pytest.approx(120.0)
    assert entry["cost_usd"] > 0


@respx.mock
def test_elevenlabs_music_records_rendered_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")
    respx.post(_MUSIC_URL).mock(
        return_value=httpx.Response(200, content=b"mp3-bytes")
    )
    settings = Settings(_env_file=None, env="prod", output_dir=tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    with activate_cost_ledger(project):
        ElevenLabsClient(settings).generate_music("bed", 10_000)

    entry = _read_costs(project)["calls"][0]
    assert entry["kind"] == "elevenlabs_music"
    assert entry["seconds"] == pytest.approx(10.0)


def test_cost_ledger_unreadable_file_recovers(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "costs.json").write_text("{torn", encoding="utf-8")
    ledger = CostLedger.load(project)
    assert ledger.totals()["cost_usd"] == 0.0


def test_run_pipeline_writes_costs_json(tmp_path, monkeypatch):
    """End-to-end wiring: a dev run leaves a costs.json with stub entries
    and surfaces the totals on the result dict."""
    import directorcut.pipeline as pipeline
    from directorcut.models import EnergyLevel, NarrativeNode, ThematicTag

    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    video = ingest_dir / "clip.mp4"
    video.write_bytes(b"fake-video")
    output_dir = tmp_path / "output"
    settings = Settings(
        _env_file=None,
        env="dev",
        ingest_dir=ingest_dir,
        output_dir=output_dir,
        fcpxml_target="fcpx",  # avoid the Resolve mixdown's real ffmpeg
    )
    manifest = IngestManifest(
        project_name="proj",
        video_path=video.resolve(),
        duration_seconds=10.0,
        assets=[
            Asset(path=video.resolve(), role=AssetRole.A_ROLL, duration_seconds=10.0)
        ],
    )
    node = NarrativeNode(
        node_id="N_001",
        time_start="00:00:00.000",
        time_end="00:00:05.000",
        spoken_text="hello",
        thematic_tag=ThematicTag.EXPOSITION,
        energy_level=EnergyLevel.MEDIUM,
    )
    def _touch(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
        return path

    monkeypatch.setattr(pipeline, "ingest", lambda *a, **k: manifest)
    monkeypatch.setattr(pipeline, "interpret_assets", lambda m, s: m)
    monkeypatch.setattr(pipeline, "prepare_aroll_media", lambda m, d, s: (m, False))
    monkeypatch.setattr(pipeline, "tag_narrative", lambda nodes, s, direction=None: nodes)
    # Unscripted path: _transcript_sources -> transcribe_audio per source.
    monkeypatch.setattr(pipeline, "transcribe_audio", lambda *a, **k: [node])
    monkeypatch.setattr(
        pipeline, "extract_audio", lambda src, out: _touch(out / f"{src.stem}_audio.mp3")
    )
    monkeypatch.setattr(
        pipeline,
        "generate_visual_proxy",
        lambda src, out: _touch(out / f"{src.stem}_visual.mp4"),
    )
    monkeypatch.setattr(pipeline, "analyze_visual_proxy", lambda *a, **k: [])

    result = pipeline.run_pipeline(settings, "proj")

    costs_file = output_dir / "proj" / "costs.json"
    assert costs_file.exists()
    data = json.loads(costs_file.read_text(encoding="utf-8"))
    # Dev-mode Gemini stubs (tagger + director) and the dev ElevenLabs stub
    # all recorded at zero cost.
    assert len(data["calls"]) >= 1
    assert all(c["cost_usd"] == 0.0 for c in data["calls"])
    assert result["costs"]["cost_usd"] == 0.0


def test_cli_run_surfaces_budget_exceeded(tmp_path, monkeypatch):
    """A cap breach mid-run exits with the ledger's message, not a traceback."""
    monkeypatch.setenv("INGEST_DIR", str(tmp_path / "ingest"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("DIRECTORCUT_ENV", "dev")
    video = tmp_path / "v.mp4"
    manifest = IngestManifest(
        project_name="proj",
        assets=[
            Asset(path=video, role=AssetRole.A_ROLL, duration_seconds=10.0)
        ],
        duration_seconds=10.0,
    )
    monkeypatch.setattr("directorcut.ingest.ingest", lambda *a, **k: manifest)

    def boom(*a, **k):
        raise BudgetExceeded(
            "Cost cap exceeded: $2.00 spent against a $1.00 cap"
        )

    monkeypatch.setattr("directorcut.cli.run_pipeline", boom)
    res = CliRunner().invoke(cli, ["run", "-y", "-p", "proj"])
    assert res.exit_code != 0
    assert "Cost cap exceeded" in res.output
