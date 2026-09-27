"""Tests for the Resolve-native timeline build script generator.

`emit_resolve_script` only writes script TEXT - no Resolve is involved - so
these assert on the generated source: the embedded plan data (record/source
frames, track indices, mediaType) and the API call sequence in the runtime
body.
"""

import json
import re
from pathlib import Path

from directorcut.conform_resolve import emit_resolve_script
from directorcut.models import AssemblyBlueprint, Cut, TimelineMeta


def _cut(track, source_file, timeline_in, timeline_out, source_in, source_out):
    return Cut(
        track=track,
        source_file=Path(source_file),
        timeline_in=timeline_in,
        timeline_out=timeline_out,
        source_in=source_in,
        source_out=source_out,
    )


def _blueprint(tmp_path, cuts, name="proj"):
    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name=name, target_fps=60.0),
        cuts=cuts,
    )


def _plan(script_text):
    """Extract the embedded PLAN = [...] JSON block from generated source."""
    match = re.search(r"^PLAN = (\[.*?\])\n\n", script_text, re.M | re.S)
    assert match, "PLAN data block missing from generated script"
    return json.loads(match.group(1))


def _media(script_text):
    match = re.search(r"^MEDIA = (\[.*?\])\n", script_text, re.M | re.S)
    assert match, "MEDIA data block missing from generated script"
    return json.loads(match.group(1))


def test_v1_cut_emits_clipinfo_row_with_source_and_record_frames(tmp_path):
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "V1_A1_PRIMARY",
                tmp_path / "clip.mp4",
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            )
        ],
    )
    out = emit_resolve_script(blueprint, None, tmp_path / "build_timeline.py")
    text = out.read_text(encoding="utf-8")

    (row,) = _plan(text)
    assert row["file"] == str(tmp_path / "clip.mp4")
    assert row["trackType"] == "video"
    assert row["trackIndex"] == 1
    assert row["mediaType"] is None  # spine clips keep embedded audio
    assert row["record"] == 0
    assert row["in"] == 600  # 10s * 60fps
    assert row["out"] == 900  # 15s * 60fps

    # Runtime body drives the real API calls in the expected order.
    body = text[text.index("def _get_resolve") :]
    for needle in (
        'dvr_script.scriptapp("Resolve")',
        "GetProjectManager().GetCurrentProject()",
        "GetMediaPool()",
        "pool.ImportMedia",
        "pool.CreateEmptyTimeline(TIMELINE_NAME)",
        "project.SetCurrentTimeline(timeline)",
        'timeline.SetStartTimecode("00:00:00:00")',
        "pool.AppendToTimeline([clip_info])",
    ):
        assert needle in body, needle
    assert body.index("ImportMedia") < body.index(
        "CreateEmptyTimeline"
    ) < body.index("AppendToTimeline")


def test_v2_overlay_is_video_only_on_track_2(tmp_path):
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "V1_A1_PRIMARY",
                tmp_path / "a.mp4",
                "00:00:00.000",
                "00:00:10.000",
                "00:00:00.000",
                "00:00:10.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                tmp_path / "b.mp4",
                "00:00:02.000",
                "00:00:06.000",
                "00:01:00.000",
                "00:01:04.000",
            ),
        ],
    )
    text = emit_resolve_script(
        blueprint, None, tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    rows = {r["label"]: r for r in _plan(text)}
    overlay = rows["b.mp4"]
    assert overlay["trackIndex"] == 2
    assert overlay["mediaType"] == 1  # picture only - no stray overlay audio
    assert overlay["record"] == 120  # 2s * 60fps
    assert (overlay["in"], overlay["out"]) == (3600, 3840)


def test_audio_only_v1_lands_on_audio_track_1(tmp_path):
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "V1_A1_PRIMARY",
                tmp_path / "vo.mp3",
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            )
        ],
    )
    text = emit_resolve_script(
        blueprint, None, tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    (row,) = _plan(text)
    assert row["trackType"] == "audio"
    assert row["trackIndex"] == 1
    assert row["mediaType"] == 2


def test_unbedded_music_and_sfx_cuts_get_levels_and_audio_tracks(tmp_path):
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "A2_MUSIC",
                tmp_path / "music.mp3",
                "00:00:00.000",
                "00:00:30.000",
                "00:00:00.000",
                "00:00:30.000",
            ),
            _cut(
                "A3_SFX",
                tmp_path / "boom.mp3",
                "00:00:05.000",
                "00:00:06.000",
                "00:00:00.000",
                "00:00:01.000",
            ),
        ],
    )
    text = emit_resolve_script(
        blueprint, None, tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    rows = {r["label"]: r for r in _plan(text)}
    music, sfx = rows["music.mp3"], rows["boom.mp3"]
    assert (music["trackIndex"], music["mediaType"]) == (2, 2)
    assert (sfx["trackIndex"], sfx["mediaType"]) == (3, 2)
    # Flat trims for un-bedded clips, applied best-effort via SetProperty.
    assert music["levelDb"] < 0 and sfx["levelDb"] < 0
    assert "SetProperty" in text


def test_rendered_beds_replace_per_cut_audio(tmp_path):
    """Mirrors the FCPXML resolve target: a rendered bed = one timeline-aligned
    clip at record 0; the lane's per-cut clips are dropped entirely."""
    (tmp_path / "music_bed_ducked.wav").write_bytes(b"wav")
    (tmp_path / "sfx_bed.wav").write_bytes(b"wav")
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "A2_MUSIC",
                tmp_path / "audio" / "m1.mp3",
                "00:00:00.000",
                "00:00:10.000",
                "00:00:00.000",
                "00:00:10.000",
            ),
            _cut(
                "A3_SFX",
                tmp_path / "audio" / "s1.mp3",
                "00:00:04.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:01.000",
            ),
        ],
    )
    text = emit_resolve_script(
        blueprint, None, tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    rows = _plan(text)
    labels = {r["label"] for r in rows}
    assert labels == {"music_bed_ducked.wav", "sfx_bed.wav"}  # per-cut rows gone
    for row in rows:
        assert row["record"] == 0 and row["in"] == 0
        assert row["out"] is None  # runtime reads media length via GetClipProperty
        assert row["levelDb"] is None  # trims/ducking baked into the wav
    assert {r["trackIndex"] for r in rows} == {2, 3}
    # The beds must be in the media pool import list even though nothing else
    # references them.
    media = _media(text)
    assert str(tmp_path / "music_bed_ducked.wav") in media
    assert str(tmp_path / "sfx_bed.wav") in media


def test_extra_assets_and_project_name_are_embedded(tmp_path):
    blueprint = _blueprint(tmp_path, [], name="my-project")
    extra = tmp_path / "unused_broll.mp4"
    text = emit_resolve_script(
        blueprint, [extra], tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    assert 'TIMELINE_NAME = "my-project"' in text
    assert "FPS = 60.0" in text
    assert str(extra) in _media(text)
    assert _plan(text) == []


def test_cli_conform_writes_build_script(tmp_path, monkeypatch):
    """`directorcut conform <project>` emits output/<project>/build_timeline.py."""
    from click.testing import CliRunner

    from directorcut.cli import cli

    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")  # never touch the real .env
    output_dir = tmp_path / "output"
    project_dir = output_dir / "proj"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "V1_A1_PRIMARY",
                tmp_path / "a.mp4",
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            )
        ],
        name="proj",
    )
    (project_dir / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )

    result = CliRunner().invoke(
        cli, ["-c", str(empty_env), "conform", "proj"]
    )
    assert result.exit_code == 0, result.output
    script = project_dir / "build_timeline.py"
    assert script.is_file()
    assert "AppendToTimeline" in script.read_text(encoding="utf-8")


def test_generated_script_is_syntactically_valid_python(tmp_path):
    """The emitted file must compile - it ships standalone to users."""
    blueprint = _blueprint(
        tmp_path,
        [
            _cut(
                "V1_A1_PRIMARY",
                tmp_path / "a.mp4",
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                tmp_path / "b.mp4",
                "00:00:01.000",
                "00:00:03.000",
                "00:00:00.000",
                "00:00:02.000",
            ),
        ],
    )
    text = emit_resolve_script(
        blueprint, None, tmp_path / "build_timeline.py"
    ).read_text(encoding="utf-8")
    compile(text, "build_timeline.py", "exec")
