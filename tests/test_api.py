import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import directorcut.api.main as api_main
from directorcut.api.main import app

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate each test's projects from the repo's real output/projects dir.
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(api_main, "PROJECTS_DIR", projects_dir)
    # A local .env may set DIRECTORCUT_ENV=prod (real Gemini/agy calls);
    # force dev mode so API tests always stub model calls regardless.
    monkeypatch.setattr(
        api_main, "settings", api_main.settings.model_copy(update={"env": "dev"})
    )
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_settings(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "gemini_model" in resp.json()


def test_rejects_invalid_project_name(client):
    resp = client.post("/projects", params={"name": "../../evil"})
    assert resp.status_code == 400

    resp = client.post("/projects", params={"name": "C:\\Windows\\Temp\\pwn"})
    assert resp.status_code == 400

    resp = client.post("/projects", params={"name": ""})
    assert resp.status_code in (400, 422)


def test_accepts_valid_project_name(client):
    resp = client.post("/projects", params={"name": "my-project_1"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "my-project_1"


def test_download_rejects_path_traversal(client):
    client.post("/projects", params={"name": "proj1"})
    resp = client.get("/projects/proj1/download/..%2f..%2fsecrets.env")
    assert resp.status_code in (400, 404)


def test_ingest_endpoint_copies_valid_files(client, tmp_path):
    client.post("/projects", params={"name": "proj2"})
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video, just bytes for the test")

    resp = client.post(
        "/projects/proj2/ingest",
        json={"video_path": str(video)},
    )
    assert resp.status_code == 200
    # The legacy video_path maps to the a_roll role subdirectory.
    assert resp.json()["files"] == [str(Path("aroll") / "clip.mp4")]


def test_ingest_endpoint_rejects_missing_file(client, tmp_path):
    client.post("/projects", params={"name": "proj3"})
    missing = tmp_path / "does_not_exist.mp4"

    resp = client.post(
        "/projects/proj3/ingest",
        json={"video_path": str(missing)},
    )
    assert resp.status_code == 400


def test_ingest_assets_with_roles_and_direction(client, tmp_path):
    """The multi-asset ingest shape sorts files into role subdirectories and
    persists the free-text direction for the pipeline to pick up."""
    client.post("/projects", params={"name": "proj4"})
    a_roll = tmp_path / "main.mp4"
    a_roll.write_bytes(b"v")
    broll = tmp_path / "cutaway.mp4"
    broll.write_bytes(b"v")
    mystery = tmp_path / "mystery.mp3"
    mystery.write_bytes(b"a")

    resp = client.post(
        "/projects/proj4/ingest",
        json={
            "assets": [
                {"path": str(a_roll), "role": "a_roll"},
                {"path": str(broll), "role": "b_roll"},
                {"path": str(mystery), "role": "auto"},
            ],
            "direction": "fast and snappy editing",
        },
    )
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert "aroll/main.mp4" in files or "aroll\\main.mp4" in files
    assert "broll/cutaway.mp4" in files or "broll\\cutaway.mp4" in files
    assert "mystery.mp3" in files

    ingest_dir = api_main.PROJECTS_DIR / "proj4" / "ingest"
    assert (ingest_dir / "aroll" / "main.mp4").is_file()
    assert (ingest_dir / "broll" / "cutaway.mp4").is_file()
    assert (ingest_dir / "mystery.mp3").is_file()
    assert (ingest_dir / "direction.txt").read_text() == "fast and snappy editing"


def test_ingest_accepts_quoted_windows_path(client, tmp_path):
    """Windows Explorer's "Copy as path" wraps paths containing spaces in
    literal double quotes - ingest must strip them instead of reporting
    'file not found' for an existing file."""
    client.post("/projects", params={"name": "quoted"})
    media = tmp_path / "my clip.mp4"
    media.write_bytes(b"v")

    resp = client.post(
        "/projects/quoted/ingest",
        json={"assets": [{"path": f'"{media}"', "role": "auto"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["files"] == ["my clip.mp4"]
    assert (api_main.PROJECTS_DIR / "quoted" / "ingest" / "my clip.mp4").is_file()


def test_browse_lists_dirs_and_media_files(client, tmp_path):
    """The file-picker endpoint lists subdirectories and media files only."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"v")
    (tmp_path / "readme.xyz").write_text("not media", encoding="utf-8")

    resp = client.get("/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(tmp_path)
    dir_names = [d["name"] for d in body["dirs"]]
    assert "sub" in dir_names
    assert [f["name"] for f in body["files"]] == ["clip.mp4"]
    assert body["files"][0]["size"] == 1


def test_browse_rejects_non_directory(client, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"v")
    resp = client.get("/browse", params={"path": str(f)})
    assert resp.status_code == 400


def test_ingest_rejects_unknown_role(client, tmp_path):
    client.post("/projects", params={"name": "proj5"})
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"v")

    resp = client.post(
        "/projects/proj5/ingest",
        json={"assets": [{"path": str(f), "role": "thumbnail"}]},
    )
    assert resp.status_code == 400


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires a real ffmpeg/ffprobe install")
def test_run_writes_results_where_status_and_download_expect_them(client, tmp_path):
    """Regression test: run_project() used to run the pipeline against the
    server's global output_dir instead of PROJECTS_DIR, so /status and
    /download could never find the results it had just produced."""
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    client.post("/projects", params={"name": "e2e"})
    ingest_resp = client.post("/projects/e2e/ingest", json={"video_path": str(video)})
    assert ingest_resp.status_code == 200

    run_resp = client.post("/projects/e2e/run")
    assert run_resp.status_code == 200

    # The run registry keeps the full event log for websocket replay.
    state = api_main._RUNS["e2e"]
    assert state.status == "done"
    stages = [e["stage"] for e in state.events if e.get("status") == "progress"]
    assert "ingest" in stages and "conform" in stages
    assert state.events[-1]["status"] == "done"

    status_resp = client.get("/projects/e2e/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["done"] is True
    assert "e2e.fcpxml" in body["outputs"]

    download_resp = client.get("/projects/e2e/download/e2e.fcpxml")
    assert download_resp.status_code == 200
    assert b"<fcpxml" in download_resp.content


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires a real ffmpeg/ffprobe install")
def test_run_extracts_multiple_embedded_audio_streams(client, tmp_path):
    """A video with multiple embedded audio streams (OBS-style multi-track:
    mic on one stream, gameplay on another) gets each stream extracted as an
    independent asset, so they can be classified and placed separately."""
    video = tmp_path / "obs.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880",
            "-t",
            "2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    client.post("/projects", params={"name": "multi"})
    ingest_resp = client.post("/projects/multi/ingest", json={"video_path": str(video)})
    assert ingest_resp.status_code == 200

    run_resp = client.post("/projects/multi/run")
    assert run_resp.status_code == 200

    pdir = api_main.PROJECTS_DIR / "multi"
    assert (pdir / "obs_stream0.mp3").is_file()
    assert (pdir / "obs_stream1.mp3").is_file()

    # Extracted streams are registered as assets and get classified roles.
    manifest = json.loads((pdir / "assets.json").read_text(encoding="utf-8"))
    stream_assets = [a for a in manifest["assets"] if "_stream" in a["path"]]
    assert len(stream_assets) == 2
    assert all(a["role"] != "auto" for a in stream_assets)
    # Voice normalization repoints audio_path at the baked stream file.
    assert manifest["audio_path"].endswith("_stream0_voicenorm.m4a")


def test_ws_run_reattaches_instead_of_duplicating(client, tmp_path, monkeypatch):
    """Reconnecting mid-run (tab closed, StrictMode remount) must replay the
    run's event history and attach to it - not launch a second pipeline."""
    import threading

    release = threading.Event()
    started = []

    def fake_pipeline(settings_copy, name, on_progress=None, fresh=False, gate=None):
        started.append(name)
        if on_progress:
            on_progress({"status": "progress", "stage": "pass1", "detail": "step0"})
        release.wait(timeout=10)
        if on_progress:
            on_progress({"status": "progress", "stage": "pass1", "detail": "step1"})
        return {"xml_path": tmp_path / "x.fcpxml", "output_dir": tmp_path}

    monkeypatch.setattr(api_main, "run_pipeline", fake_pipeline)
    client.post("/projects", params={"name": "wsre"})
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    client.post("/projects/wsre/ingest", json={"assets": [{"path": str(media)}]})

    with client.websocket_connect("/ws/wsre") as ws1:
        ws1.send_json({"action": "run"})
        first = ws1.receive_json()
        assert first["detail"] == "step0"
        # socket closes here - mid-run

    with client.websocket_connect("/ws/wsre") as ws2:
        ws2.send_json({"action": "run"})
        replayed = ws2.receive_json()
        assert replayed["detail"] == "step0"  # history replayed
        release.set()
        seen = []
        while True:
            msg = ws2.receive_json()
            seen.append(msg)
            if msg["status"] == "done":
                break

    assert started == ["wsre"]  # one underlying run only
    assert [m.get("detail") for m in seen if m.get("status") == "progress"] == ["step1"]
    assert seen[-1]["status"] == "done"
