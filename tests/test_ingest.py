from pathlib import Path

from directorcut import ingest as ingest_module
from directorcut.ingest import ingest


def _make_ingest_files(base_dir):
    (base_dir / "gameplay.mp4").write_bytes(b"fake video bytes")
    (base_dir / "voiceover.mp3").write_bytes(b"fake audio bytes")
    (base_dir / "script.txt").write_text("Hello world.", encoding="utf-8")


def test_ingest_returns_absolute_paths(tmp_path, monkeypatch):
    """Regression test: ingest() used to return paths relative to cwd, which
    produced invalid `file://` URIs in the generated FCPXML."""
    ingest_root = tmp_path / "workspace"
    ingest_root.mkdir()
    (ingest_root / "ingest").mkdir()
    _make_ingest_files(ingest_root / "ingest")

    monkeypatch.chdir(ingest_root)
    monkeypatch.setattr(
        ingest_module, "_probe_video_info", lambda path: (12.0, 30.0)
    )

    manifest = ingest(Path("ingest"), "smoketest")

    assert manifest.video_path.is_absolute()
    assert manifest.audio_path.is_absolute()
    assert manifest.script_path.is_absolute()
    assert manifest.duration_seconds == 12.0
    assert manifest.target_fps == 30.0


def test_probe_video_info_parses_fraction_fps(monkeypatch):
    def fake_run_ffprobe(_args, settings=None):
        return "30000/1001\n12.345000\n"

    monkeypatch.setattr(ingest_module, "run_ffprobe", fake_run_ffprobe)

    duration, fps = ingest_module._probe_video_info(Path("dummy.mp4"))

    assert duration == 12.345
    assert abs(fps - 29.97) < 0.01


def test_probe_video_info_falls_back_on_failure(monkeypatch):
    def raising_run_ffprobe(_args, settings=None):
        raise ingest_module.FFmpegError("ffprobe not found")

    monkeypatch.setattr(ingest_module, "run_ffprobe", raising_run_ffprobe)

    duration, fps = ingest_module._probe_video_info(Path("dummy.mp4"))

    assert duration == 0.0
    assert fps == 60.0
