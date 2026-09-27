"""Media prep tests: rotation/aspect probing, reframe bake, voice normalize."""

import json
from pathlib import Path

from directorcut import media_prep
from directorcut.media_prep import (
    VideoGeometry,
    measure_integrated_loudness,
    needs_reframe,
    prepare_aroll_media,
    probe_geometry,
    reframe_filter,
)
from directorcut.models import Asset, AssetRole, IngestManifest


def _settings(tmp_path, **over):
    from directorcut.config import Settings

    base = {
        "_env_file": None,
        "gemini_api_key": "",
        "ingest_dir": tmp_path / "ingest",
        "output_dir": tmp_path / "output",
        "env": "test",
    }
    base.update(over)
    return Settings(**base)


def _probe_json(width, height, rotation=None, tag_rotate=None):
    stream = {"width": width, "height": height}
    if rotation is not None:
        stream["side_data_list"] = [{"side_data_type": "Display Matrix",
                                     "rotation": rotation}]
    if tag_rotate is not None:
        stream["tags"] = {"rotate": str(tag_rotate)}
    return json.dumps({"streams": [stream]})


# --- probe_geometry / needs_reframe / reframe_filter -------------------------


def test_probe_reads_display_matrix_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media_prep, "run_ffprobe",
        lambda *a, **k: _probe_json(3840, 2160, rotation=-90),
    )
    geom = probe_geometry(tmp_path / "aroll.mp4")
    assert geom == VideoGeometry(3840, 2160, -90)
    # A 90-degree display rotation swaps the effective frame to portrait.
    assert geom.effective == (2160, 3840)


def test_probe_reads_legacy_rotate_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media_prep, "run_ffprobe",
        lambda *a, **k: _probe_json(1920, 1080, tag_rotate="90"),
    )
    geom = probe_geometry(tmp_path / "aroll.mp4")
    assert geom.rotation == 90
    assert geom.effective == (1080, 1920)


def test_probe_returns_none_on_failure(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise media_prep.FFmpegError("nope")

    monkeypatch.setattr(media_prep, "run_ffprobe", _boom)
    assert probe_geometry(tmp_path / "broken.mp4") is None


def test_needs_reframe_matrix():
    # Flat 16:9, no rotation: untouched.
    assert not needs_reframe(VideoGeometry(1920, 1080, None))
    # Any nonzero display rotation must be baked away.
    assert needs_reframe(VideoGeometry(1920, 1080, 180))
    # Portrait effective frame needs the crop.
    assert needs_reframe(VideoGeometry(3840, 2160, 90))
    # Native portrait without rotation metadata still pillarboxes.
    assert needs_reframe(VideoGeometry(1080, 1920, None))
    # 4:3 counts as off-target too.
    assert needs_reframe(VideoGeometry(1440, 1080, None))


def test_reframe_filter_matches_verified_recipe():
    """The StS2 run used crop=2160:1215:0:1024,scale=1920:1080 on a
    3840x2160/rot90 file - this reproduces that math (even-rounded crop)."""
    geom = VideoGeometry(3840, 2160, -90)
    vf = reframe_filter(geom)
    assert vf.startswith("crop=2160:121")  # 2160*9/16 ~= 1215 (even-rounded)
    assert ":0:1024," in vf
    assert vf.endswith("scale=1920:1080:flags=lanczos")


def test_reframe_filter_centers_wide_sources():
    geom = VideoGeometry(2560, 1080, None)
    assert reframe_filter(geom) == "crop=1920:1080:320:0,scale=1920:1080:flags=lanczos"


def test_reframe_filter_scale_only_when_ar_already_ok():
    """Rotation-only case: decode autorotates, so scale alone strips the
    side-data without cropping."""
    geom = VideoGeometry(1920, 1080, 180)
    assert reframe_filter(geom) == "scale=1920:1080:flags=lanczos"


# --- loudness measurement ----------------------------------------------------


def test_measure_loudness_parses_ebur128_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")
    stderr = (
        "[Parsed_ebur128_0 @ 0x0] Summary:\n\n"
        "  Integrated loudness:\n"
        "    I:         -26.1 LUFS\n"
        "    Threshold: -36.5 LUFS\n"
    )

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    P.stderr = stderr
    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: P())
    assert measure_integrated_loudness(tmp_path / "a.mp4") == -26.1


def test_measure_loudness_none_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")

    class P:
        returncode = 1
        stdout = ""
        stderr = "no audio"

    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: P())
    assert measure_integrated_loudness(tmp_path / "silent.mp4") is None


# --- prepare_aroll_media -----------------------------------------------------


def _manifest(tmp_path, assets, direction=None):
    return IngestManifest(
        project_name="p",
        assets=assets,
        direction=direction,
    )


def _fake_bake(commands):
    """subprocess.run fake for _bake: records argv, 'creates' the output."""

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(cmd, **kw):
        commands.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"baked")
        return P()

    return _run


def test_prepare_reframes_rotated_aroll(tmp_path, monkeypatch):
    src = tmp_path / "ingest" / "aroll" / "phone.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.A_ROLL)]
    )
    settings = _settings(tmp_path)

    monkeypatch.setattr(
        media_prep, "run_ffprobe",
        lambda *a, **k: _probe_json(3840, 2160, rotation=-90),
    )
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")
    commands = []
    monkeypatch.setattr(media_prep.subprocess, "run", _fake_bake(commands))

    media_dir = tmp_path / "output" / "p" / "media"
    out, changed = prepare_aroll_media(manifest, media_dir, settings)

    assert changed
    asset = out.assets[0]
    assert asset.path == media_dir / "phone_reframed.mp4"
    assert asset.derived_from == src.resolve()
    cmd = commands[0]
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("crop=2160:121") and "scale=1920:1080" in vf
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"  # audio kept


def test_prepare_skips_clean_16x9_aroll(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.A_ROLL)]
    )
    monkeypatch.setattr(
        media_prep, "run_ffprobe",
        lambda *a, **k: _probe_json(1920, 1080),
    )
    calls = []
    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: calls.append(a))

    out, changed = prepare_aroll_media(
        manifest, tmp_path / "media", _settings(tmp_path)
    )
    assert not changed
    assert out.assets[0].path == src.resolve()
    assert calls == []


def test_prepare_respects_config_opt_out(tmp_path, monkeypatch):
    src = tmp_path / "phone.mp4"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.A_ROLL)]
    )
    monkeypatch.setattr(
        media_prep, "run_ffprobe",
        lambda *a, **k: _probe_json(3840, 2160, rotation=-90),
    )
    calls = []
    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: calls.append(a))

    settings = _settings(tmp_path, reframe_aroll=False)
    out, changed = prepare_aroll_media(manifest, tmp_path / "media", settings)
    assert not changed and calls == []

    # Direction-level opt-out ("keep original framing") works too.
    settings = _settings(tmp_path)
    manifest2 = _manifest(
        tmp_path,
        [Asset(path=src.resolve(), role=AssetRole.A_ROLL)],
        direction="Please keep original framing - no reframe.",
    )
    out, changed = prepare_aroll_media(manifest2, tmp_path / "media", settings)
    assert not changed and calls == []


def test_prepare_normalizes_quiet_voice(tmp_path, monkeypatch):
    src = tmp_path / "aroll.mp4"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.A_ROLL)]
    )
    monkeypatch.setattr(
        media_prep, "run_ffprobe", lambda *a, **k: _probe_json(1920, 1080)
    )
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")

    class P:
        def __init__(self, stderr=""):
            self.returncode = 0
            self.stdout = ""
            self.stderr = stderr

    commands = []

    def _run(cmd, **kw):
        commands.append(list(cmd))
        if "ebur128" in cmd:
            return P(stderr="Summary:\n    I:         -26.4 LUFS\n")
        Path(cmd[-1]).write_bytes(b"norm")
        return P()

    monkeypatch.setattr(media_prep.subprocess, "run", _run)

    settings = _settings(tmp_path, normalize_voice=True)
    out, changed = prepare_aroll_media(manifest, tmp_path / "media", settings)

    assert changed
    asset = out.assets[0]
    assert asset.path.name == "aroll_voicenorm.mp4"
    # commands also captures the ebur128 measure + ingest probes; the bake
    # is the one writing the _voicenorm output.
    bake = next(c for c in commands if c[-1].endswith("_voicenorm.mp4"))
    af = bake[bake.index("-af") + 1]
    assert "highpass=f=80" in af
    assert "compand=" in af
    assert "loudnorm=I=-16:TP=-1.5" in af
    assert bake[bake.index("-c:v") + 1] == "copy"  # video stream copied
    assert bake[bake.index("-c:a") + 1] == "aac"
    assert bake[bake.index("-b:a") + 1] == "192k"


def test_prepare_skips_normalize_when_in_band(tmp_path, monkeypatch):
    """A file already at -16.x LUFS must NOT be normalized again."""
    src = tmp_path / "aroll.mp4"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.A_ROLL)]
    )
    monkeypatch.setattr(
        media_prep, "run_ffprobe", lambda *a, **k: _probe_json(1920, 1080)
    )
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")

    class P:
        returncode = 0
        stdout = ""
        stderr = "Summary:\n    I:         -16.6 LUFS\n"

    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: P())

    settings = _settings(tmp_path, normalize_voice=True)
    out, changed = prepare_aroll_media(manifest, tmp_path / "media", settings)
    assert not changed
    assert out.assets[0].path == src.resolve()


def test_prepare_leaves_broll_alone(tmp_path, monkeypatch):
    src = tmp_path / "broll.mp4"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.B_ROLL)]
    )
    calls = []
    monkeypatch.setattr(media_prep.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(
        media_prep, "run_ffprobe", lambda *a, **k: _probe_json(3840, 2160, -90)
    )
    settings = _settings(tmp_path, normalize_voice=True)
    out, changed = prepare_aroll_media(manifest, tmp_path / "media", settings)
    assert not changed and calls == []
    assert out.assets[0].path == src.resolve()


def test_prepare_heals_missing_bake(tmp_path, monkeypatch):
    """On resume the media/ dir may be gone: rebake from derived_from."""
    orig = tmp_path / "phone.mp4"
    orig.write_bytes(b"fake")
    baked = tmp_path / "media" / "phone_reframed.mp4"
    asset = Asset(
        path=baked.resolve(),  # does not exist on disk
        role=AssetRole.A_ROLL,
        derived_from=orig.resolve(),
    )
    manifest = _manifest(tmp_path, [asset])
    monkeypatch.setattr(
        media_prep, "run_ffprobe", lambda *a, **k: _probe_json(3840, 2160, -90)
    )
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")
    commands = []
    monkeypatch.setattr(media_prep.subprocess, "run", _fake_bake(commands))

    out, changed = prepare_aroll_media(
        manifest, tmp_path / "media", _settings(tmp_path)
    )
    # Baked file recreated, path unchanged (same deterministic name).
    assert commands  # ffmpeg re-ran
    assert out.assets[0].path == baked.resolve()
    assert out.assets[0].derived_from == orig.resolve()


def test_prepare_normalizes_audio_only_voiceover(tmp_path, monkeypatch):
    """A quiet voiceover MP3 (no video stream) must normalize too - the bake
    used to hardcode `-map 0:v:0` which ffmpeg rejects on audio-only input."""
    from directorcut.models import AssetRole

    src = tmp_path / "voiceover.mp3"
    src.write_bytes(b"fake")
    manifest = _manifest(
        tmp_path, [Asset(path=src.resolve(), role=AssetRole.VOICEOVER)]
    )
    monkeypatch.setattr(media_prep, "find_ffmpeg", lambda s=None: "ffmpeg")

    class P:
        def __init__(self, stderr=""):
            self.returncode = 0
            self.stdout = ""
            self.stderr = stderr

    commands = []

    def _run(cmd, **kw):
        commands.append(list(cmd))
        if "ebur128" in cmd:
            return P(stderr="Summary:\n    I:         -27.0 LUFS\n")
        Path(cmd[-1]).write_bytes(b"norm")
        return P()

    monkeypatch.setattr(media_prep.subprocess, "run", _run)

    settings = _settings(tmp_path, normalize_voice=True)
    out, changed = prepare_aroll_media(manifest, tmp_path / "media", settings)

    assert changed
    assert out.assets[0].path.name == "voiceover_voicenorm.m4a"
    bake = next(c for c in commands if c[-1].endswith("_voicenorm.m4a"))
    # No video map at all - `0:v:0` would kill ffmpeg on an audio-only file.
    assert "0:v:0" not in bake
    assert "0:a:0" in bake
    assert "-c:v" not in bake
    assert "loudnorm=I=-16" in bake[bake.index("-af") + 1]
