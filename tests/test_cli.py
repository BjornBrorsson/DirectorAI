import shutil
import subprocess

import pytest
from click.testing import CliRunner

from directorcut.cli import cli

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_synthetic_clip(path):
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
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires a real ffmpeg/ffprobe install")
def test_run_dry_run_does_not_crash(tmp_path, monkeypatch):
    """Regression test: `directorcut run --dry-run` used to crash immediately
    with a TypeError from a broken check_token_budget(settings) call."""
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    _make_synthetic_clip(ingest_dir / "clip.mp4")
    (ingest_dir / "script.txt").write_text("Hello there. This is a test.", encoding="utf-8")

    output_dir = tmp_path / "output"
    monkeypatch.setenv("INGEST_DIR", str(ingest_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("DIRECTORCUT_ENV", "dev")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--dry-run", "-p", "clitest"])

    assert result.exit_code == 0, result.output
    assert "Estimated API cost" in result.output


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires a real ffmpeg/ffprobe install")
def test_run_end_to_end_produces_fcpxml(tmp_path, monkeypatch):
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    _make_synthetic_clip(ingest_dir / "clip.mp4")
    (ingest_dir / "script.txt").write_text(
        "First we boot up the game. Why didn't they tell me?! I bet I can beat it.",
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    monkeypatch.setenv("INGEST_DIR", str(ingest_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("DIRECTORCUT_ENV", "dev")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "-y", "-p", "clitest"])

    assert result.exit_code == 0, result.output
    xml_path = output_dir / "clitest" / "clitest.fcpxml"
    assert xml_path.exists()
    xml_text = xml_path.read_text(encoding="utf-8")
    assert "<?xml" in xml_text
    assert "file:///" in xml_text
