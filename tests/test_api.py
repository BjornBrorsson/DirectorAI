import shutil
import subprocess

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
    assert resp.json()["files"] == ["clip.mp4"]


def test_ingest_endpoint_rejects_missing_file(client, tmp_path):
    client.post("/projects", params={"name": "proj3"})
    missing = tmp_path / "does_not_exist.mp4"

    resp = client.post(
        "/projects/proj3/ingest",
        json={"video_path": str(missing)},
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

    status_resp = client.get("/projects/e2e/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["done"] is True
    assert "e2e.fcpxml" in body["outputs"]

    download_resp = client.get("/projects/e2e/download/e2e.fcpxml")
    assert download_resp.status_code == 200
    assert b"<fcpxml" in download_resp.content
