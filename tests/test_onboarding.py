"""Tests for `directorcut setup` (user config) + `directorcut demo`."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from directorcut.cli import cli
from directorcut.config import Settings, user_config_path

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect the user config dir + move cwd off the repo .env."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("DIRECTORCUT_CONFIG_DIR", str(cfg_dir))
    for var in ("GEMINI_MODEL", "GEMINI_PAID_KEY_MAIN", "DIRECTORCUT_ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return cfg_dir


def test_setup_writes_user_config_never_repo(isolated_config, tmp_path):
    runner = CliRunner()
    answers = (
        "\n".join(
            [
                "",  # GEMINI_FREE_KEY_MAIN (skip)
                "",  # GEMINI_FREE_KEY (skip)
                "gk-main",  # GEMINI_PAID_KEY_MAIN
                "",  # GEMINI_PAID_KEY (skip)
                "el-key",  # ELEVENLABS_API_KEY
                "",  # ELEVENLABS_VOICE_ID (skip)
                "api",  # GEMINI_BACKEND
                "",  # GEMINI_MODEL (accept default)
                "",  # GEMINI_MODEL_FAST (skip)
                "auto",  # DIRECTORCUT_STT_BACKEND
                "small",  # DIRECTORCUT_WHISPER_MODEL
                "prod",  # DIRECTORCUT_ENV
            ]
        )
        + "\n"
    )
    result = runner.invoke(cli, ["setup"], input=answers)

    assert result.exit_code == 0, result.output
    cfg = isolated_config / "config.env"
    assert cfg.exists()
    text = cfg.read_text(encoding="utf-8")
    assert "GEMINI_PAID_KEY_MAIN=gk-main" in text
    assert "ELEVENLABS_API_KEY=el-key" in text
    assert "DIRECTORCUT_STT_BACKEND=auto" in text
    assert "DIRECTORCUT_ENV=prod" in text
    # Skipped values land as commented placeholders.
    assert "# GEMINI_PAID_KEY=" in text
    # The wizard must never write into the repo/cwd .env.
    assert not (tmp_path / ".env").exists()


def test_setup_blank_keeps_existing(isolated_config):
    cfg = isolated_config / "config.env"
    cfg.write_text("GEMINI_PAID_KEY_MAIN=old-main\n", encoding="utf-8")

    runner = CliRunner()
    answers = "\n".join([""] * 6 + ["api", "", "", "whisper", "tiny", "dev"]) + "\n"
    result = runner.invoke(cli, ["setup"], input=answers)

    assert result.exit_code == 0, result.output
    text = cfg.read_text(encoding="utf-8")
    assert "GEMINI_PAID_KEY_MAIN=old-main" in text
    assert "DIRECTORCUT_STT_BACKEND=whisper" in text
    # The stored key is kept but never printed back (not even as a prompt
    # default) - that's the secret-leak invariant.
    assert "old-main" not in result.output


def test_user_config_precedence(isolated_config):
    """process env > repo .env > user config."""
    user_config_path().write_text(
        "GEMINI_MODEL=usercfg-model\nGEMINI_PAID_KEY_MAIN=pk-user\n",
        encoding="utf-8",
    )

    s = Settings()
    assert s.gemini_model == "usercfg-model"
    assert s.gemini_paid_key_main == "pk-user"

    Path(".env").write_text("GEMINI_MODEL=repo-model\n", encoding="utf-8")
    assert Settings().gemini_model == "repo-model"


def test_process_env_beats_user_config(isolated_config, monkeypatch):
    user_config_path().write_text("GEMINI_MODEL=usercfg-model\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_MODEL", "proc-model")
    assert Settings().gemini_model == "proc-model"


def test_env_file_none_disables_user_config(isolated_config):
    """Settings(_env_file=None) stays fully isolated (tests rely on it)."""
    user_config_path().write_text("GEMINI_MODEL=usercfg-model\n", encoding="utf-8")
    s = Settings(_env_file=None)
    assert s.gemini_model == "gemini-3.7-flash"


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires a real ffmpeg/ffprobe install")
def test_demo_runs_offline(tmp_path, monkeypatch):
    for var in (
        "GEMINI_API_KEY",
        "GEMINI_PAID_KEY_MAIN",
        "GEMINI_PAID_KEY",
        "GEMINI_FREE_KEY",
        "ELEVENLABS_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    workdir = tmp_path / "demo"

    runner = CliRunner()
    result = runner.invoke(cli, ["demo", "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    xml_path = workdir / "output" / "demo" / "demo.fcpxml"
    assert xml_path.exists()
    assert "FCPXML written to:" in result.output
    xml_text = xml_path.read_text(encoding="utf-8")
    assert "<?xml" in xml_text
    assert "file:///" in xml_text
