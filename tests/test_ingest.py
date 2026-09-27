from pathlib import Path

import pytest

from directorcut import ingest as ingest_module
from directorcut.ingest import ingest
from directorcut.models import AssetRole


def _make_ingest_files(base_dir):
    (base_dir / "gameplay.mp4").write_bytes(b"fake video bytes")
    (base_dir / "voiceover.mp3").write_bytes(b"fake audio bytes")
    (base_dir / "script.txt").write_text("Hello world.", encoding="utf-8")


def _stub_probes(monkeypatch):
    monkeypatch.setattr(ingest_module, "_probe_video_info", lambda path: (12.0, 30.0))
    monkeypatch.setattr(ingest_module, "_probe_duration", lambda path: 7.5)
    monkeypatch.setattr(ingest_module, "_probe_audio_stream_count", lambda path: 0)


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


def test_probe_audio_stream_count_parses_streams(monkeypatch):
    def fake_run_ffprobe(args, settings=None):
        assert args[args.index("-select_streams") + 1] == "a"
        return "1\n2\n3\n"

    monkeypatch.setattr(ingest_module, "run_ffprobe", fake_run_ffprobe)

    assert ingest_module._probe_audio_stream_count(Path("obs.mp4")) == 3


def test_probe_audio_stream_count_falls_back_on_failure(monkeypatch):
    def raising_run_ffprobe(_args, settings=None):
        raise ingest_module.FFmpegError("ffprobe not found")

    monkeypatch.setattr(ingest_module, "run_ffprobe", raising_run_ffprobe)

    assert ingest_module._probe_audio_stream_count(Path("x.mp4")) == 0


def test_ingest_records_video_audio_stream_count(tmp_path, monkeypatch):
    """A multi-track capture records its stream count so the pipeline can
    extract each embedded stream as an independent asset."""
    _stub_probes(monkeypatch)
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    (ingest_dir / "obs.mp4").write_bytes(b"fake video bytes")
    monkeypatch.setattr(ingest_module, "_probe_audio_stream_count", lambda path: 3)

    manifest = ingest(ingest_dir)

    assert manifest.assets[0].audio_stream_count == 3


def test_ingest_role_subdirectories(tmp_path, monkeypatch):
    """Files under role-named subdirs come in pre-classified; a flat file
    with no hint stays AUTO for the interpretation pass."""
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    for sub in ("broll", "music", "sfx", "images", "scripts"):
        (d / sub).mkdir(parents=True)
    (d / "main.mp4").write_bytes(b"v")
    (d / "broll" / "cutaway.mp4").write_bytes(b"v")
    (d / "music" / "bed.mp3").write_bytes(b"a")
    (d / "sfx" / "boom.mp3").write_bytes(b"a")
    (d / "images" / "card.png").write_bytes(b"i")
    (d / "scripts" / "lines.txt").write_text("Hi.", encoding="utf-8")

    m = ingest(d)
    roles = {a.path.name: a.role for a in m.assets}

    assert roles["main.mp4"] == AssetRole.AUTO
    assert roles["cutaway.mp4"] == AssetRole.B_ROLL
    assert roles["bed.mp3"] == AssetRole.MUSIC
    assert roles["boom.mp3"] == AssetRole.SFX
    assert roles["card.png"] == AssetRole.IMAGE
    assert roles["lines.txt"] == AssetRole.SCRIPT
    # Legacy fields still resolve: AUTO video becomes the primary.
    assert m.video_path.name == "main.mp4"
    assert m.script_path.name == "lines.txt"


def test_ingest_filename_prefixes(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "broll_cutaway.mp4").write_bytes(b"v")
    (d / "aroll_main.mp4").write_bytes(b"v")
    (d / "sfx_whoosh.mp3").write_bytes(b"a")

    m = ingest(d)
    roles = {a.path.name: a.role for a in m.assets}

    assert roles["broll_cutaway.mp4"] == AssetRole.B_ROLL
    assert roles["aroll_main.mp4"] == AssetRole.A_ROLL
    assert roles["sfx_whoosh.mp3"] == AssetRole.SFX
    assert m.video_path.name == "aroll_main.mp4"


def test_ingest_incompatible_role_hint_falls_back_to_auto(tmp_path, monkeypatch):
    """`music_video.mp4` is a video file - it can't take the MUSIC role."""
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "music_video.mp4").write_bytes(b"v")

    m = ingest(d)
    assert m.assets[0].role == AssetRole.AUTO


def test_ingest_reads_direction_file(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"v")
    (d / "direction.txt").write_text("fast and snappy editing", encoding="utf-8")

    m = ingest(d)

    assert m.direction == "fast and snappy editing"
    # The direction file itself must not become a script asset.
    assert all(a.path.name != "direction.txt" for a in m.assets)


def test_ingest_direction_param_overrides_file(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"v")
    (d / "direction.txt").write_text("from file", encoding="utf-8")

    m = ingest(d, direction="from arg")
    assert m.direction == "from arg"


def test_ingest_skips_unsupported_files(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"v")
    (d / "Thumbs.db").write_bytes(b"junk")

    m = ingest(d)
    assert len(m.assets) == 1


def test_ingest_rejects_no_visual_assets(tmp_path, monkeypatch):
    _stub_probes(monkeypatch)
    d = tmp_path / "ingest"
    d.mkdir()
    (d / "only_audio.mp3").write_bytes(b"a")

    with pytest.raises(ValueError):
        ingest(d)
