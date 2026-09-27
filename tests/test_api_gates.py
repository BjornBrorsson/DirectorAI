"""Review gates between passes (web-ui-review-gates ticket).

The pipeline runs on a daemon thread; a `gate` hook in run_pipeline pauses it
after a stage checkpoint lands, the API exposes the artifact for review, and
POST .../resume (with optional edits that overwrite the checkpoint JSON)
releases it. These tests drive the REAL run_pipeline with stage functions
stubbed (same pattern as test_resume.py) - no ffmpeg, no model calls.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import directorcut.api.main as api_main
import directorcut.pipeline as pipeline
from directorcut.api.main import app
from directorcut.models import (
    AssemblyBlueprint,
    Asset,
    AssetRole,
    Cut,
    EnergyLevel,
    IngestManifest,
    NarrativeNode,
    ThematicTag,
    TimelineMeta,
    VisualEvent,
    VisualState,
)


def _node(node_id="N_001", text="hello world"):
    return NarrativeNode(
        node_id=node_id,
        time_start="00:00:00.000",
        time_end="00:00:05.000",
        spoken_text=text,
        thematic_tag=ThematicTag.EXPOSITION,
        energy_level=EnergyLevel.MEDIUM,
    )


def _event(event_id="V_001"):
    return VisualEvent(
        event_id=event_id,
        time_start="00:00:01.000",
        time_end="00:00:03.000",
        classification=VisualState.STATE_ACTION,
        visual_description="action",
    )


def _wait_done(state, timeout=30.0):
    deadline = time.time() + timeout
    while state.status == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert state.status == "done", f"run stuck at {state.status}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """API TestClient over an isolated projects dir, with pipeline stages
    stubbed but the real checkpoint/gate machinery intact."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(api_main, "PROJECTS_DIR", projects_dir)
    # fcpx target: pure-XML conform, no ffmpeg mixdown on fake media bytes.
    monkeypatch.setattr(
        api_main,
        "settings",
        api_main.settings.model_copy(
            update={"env": "dev", "fcpxml_target": "fcpx"}
        ),
    )

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    manifest = IngestManifest(
        project_name="gated",
        video_path=video.resolve(),
        duration_seconds=10.0,
        assets=[
            Asset(
                path=video.resolve(),
                role=AssetRole.A_ROLL,
                duration_seconds=10.0,
            )
        ],
    )
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="gated", target_fps=60.0),
        cuts=[
            Cut(
                track="V1_A1_PRIMARY",
                source_file=video.resolve(),
                timeline_in="00:00:00.000",
                timeline_out="00:00:05.000",
                source_in="00:00:00.000",
                source_out="00:00:05.000",
            )
        ],
    )

    def _touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
        return path

    monkeypatch.setattr(pipeline, "ingest", lambda *a, **k: manifest)
    monkeypatch.setattr(pipeline, "interpret_assets", lambda m, s: m)
    monkeypatch.setattr(
        pipeline,
        "extract_audio",
        lambda src, out: _touch(out / f"{src.stem}_audio.mp3"),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_visual_proxy",
        lambda src, out: _touch(out / f"{src.stem}_visual.mp4"),
    )
    monkeypatch.setattr(
        pipeline, "transcribe_audio", lambda *a, **k: [_node()]
    )
    monkeypatch.setattr(
        pipeline, "tag_narrative", lambda nodes, s, direction=None: nodes
    )
    monkeypatch.setattr(
        pipeline, "analyze_visual_proxy", lambda *a, **k: [_event()]
    )
    monkeypatch.setattr(
        pipeline, "run_director", lambda *a, **k: blueprint
    )
    return TestClient(app), projects_dir


def _ingest_clip(client, tmp_path, name):
    media = tmp_path / f"{name}_src.mp4"
    media.write_bytes(b"v")
    client.post("/projects", params={"name": name})
    resp = client.post(
        f"/projects/{name}/ingest", json={"assets": [{"path": str(media)}]}
    )
    assert resp.status_code == 200


def test_run_gate_pauses_and_resume_with_edits(client, tmp_path):
    """gate_mode=pass1: run pauses after Pass 1, artifact is reviewable, and
    resume applies JSON edits that persist into narrative.json."""
    tc, projects_dir = client
    _ingest_clip(tc, tmp_path, "gated")

    resp = tc.post("/projects/gated/run", json={"gate_mode": "pass1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_gate"
    assert body["stage"] == "pass1"

    # The pause was announced on the run's event log (WS subscribers see it).
    state = api_main._RUNS["gated"]
    assert {"status": "awaiting_gate", "stage": "pass1"} in state.events

    # Review the artifact the gate is holding.
    r = tc.get("/projects/gated/stage/pass1")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data[0]["spoken_text"] == "hello world"
    assert r.json()["artifact"] == "narrative.json"

    # A run waiting at pass1 rejects a resume aimed at another stage.
    assert tc.post("/projects/gated/stage/pass2/resume").status_code == 409

    # Invalid edits are rejected WITHOUT releasing the gate.
    bad = tc.post(
        "/projects/gated/stage/pass1/resume", json={"edits": [{"bogus": 1}]}
    )
    assert bad.status_code == 400
    assert state.awaiting_gate == "pass1"

    # Approve with edits - the pipeline reloads narrative.json after the gate.
    edited = [dict(data[0], spoken_text="EDITED BY REVIEWER")]
    r = tc.post(
        "/projects/gated/stage/pass1/resume", json={"edits": edited}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "resumed"

    _wait_done(state)
    saved = json.loads(
        (projects_dir / "gated" / "narrative.json").read_text(encoding="utf-8")
    )
    assert saved[0]["spoken_text"] == "EDITED BY REVIEWER"
    # Pass 2/3 ran ungated and the run finished.
    assert (projects_dir / "gated" / "blueprint.json").exists()
    assert (projects_dir / "gated" / "gated.fcpxml").exists()


def test_gate_mode_all_pauses_at_each_stage(client, tmp_path):
    tc, projects_dir = client
    _ingest_clip(tc, tmp_path, "allgates")

    resp = tc.post("/projects/allgates/run", json={"gate_mode": "all"})
    assert resp.json()["stage"] == "pass1"
    state = api_main._RUNS["allgates"]

    for stage, artifact in (
        ("pass1", "narrative.json"),
        ("pass2", "visual.json"),
        ("pass3", "blueprint.json"),
    ):
        # Between gates the run is briefly unpaused (awaiting_gate=None).
        deadline = time.time() + 30
        while state.awaiting_gate != stage:
            assert state.status == "running"
            assert time.time() < deadline, f"never reached gate {stage}"
            time.sleep(0.05)
        r = tc.get(f"/projects/allgates/stage/{stage}")
        assert r.status_code == 200
        assert r.json()["artifact"] == artifact
        if stage == "pass3":
            # The V1 listing preview rides along with the blueprint JSON.
            assert any("V1" in line for line in r.json()["listing"])
        assert (
            tc.post(f"/projects/allgates/stage/{stage}/resume").status_code
            == 200
        )

    _wait_done(state)
    gates_hit = [
        e["stage"] for e in state.events if e.get("status") == "awaiting_gate"
    ]
    assert gates_hit == ["pass1", "pass2", "pass3"]
    assert (projects_dir / "allgates" / "allgates.fcpxml").exists()


def test_auto_gate_mode_never_pauses(client, tmp_path):
    tc, projects_dir = client
    _ingest_clip(tc, tmp_path, "auto")

    resp = tc.post("/projects/auto/run", json={"gate_mode": "auto"})
    assert resp.status_code == 200
    assert "xml_path" in resp.json()  # ran straight through to done
    state = api_main._RUNS["auto"]
    assert not any(
        e.get("status") == "awaiting_gate" for e in state.events
    )
    assert (projects_dir / "auto" / "auto.fcpxml").exists()


def test_get_stage_artifact_404_before_produced(client, tmp_path):
    tc, _ = client
    tc.post("/projects", params={"name": "empty"})
    assert tc.get("/projects/empty/stage/pass1").status_code == 404
    assert tc.get("/projects/empty/stage/pass9").status_code == 400
    # No live run at all -> resume is a conflict, not a crash.
    assert tc.post("/projects/empty/stage/pass1/resume").status_code == 409
