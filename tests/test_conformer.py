from pathlib import Path

import pytest

from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.exceptions import ConformError
from directorcut.models import (
    AssemblyBlueprint,
    Cut,
    TimelineMeta,
)


def _cut(track, source_file, timeline_in, timeline_out, source_in, source_out):
    return Cut(
        track=track,
        source_file=source_file,
        timeline_in=timeline_in,
        timeline_out=timeline_out,
        source_in=source_in,
        source_out=source_out,
    )


def test_build_fcpxml(tmp_path):
    video = tmp_path / "video.mp4"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                video,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            )
        ],
    )
    xml = build_fcpxml(blueprint)
    assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
    assert "fcpxml" in xml
    assert "video" in xml


def test_build_fcpxml_rejects_relative_paths():
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                Path("relative/video.mp4"),
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            )
        ],
    )
    with pytest.raises(ConformError):
        build_fcpxml(blueprint)


def test_primary_track_uses_audio_source(tmp_path):
    """An audio-only primary source should be emitted as <audio>, not <video>."""
    voiceover = tmp_path / "voiceover.mp3"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            )
        ],
    )
    xml = build_fcpxml(blueprint)
    assert "<audio " in xml
    assert "<video " not in xml


def test_broll_clip_uses_lane_attribute(tmp_path):
    """A B-roll cut starting alongside a primary clip is nested with lane=1."""
    voiceover = tmp_path / "voiceover.mp3"
    gameplay = tmp_path / "gameplay.mp4"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                gameplay,
                "00:00:00.000",
                "00:00:02.000",
                "00:01:00.000",
                "00:01:02.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    assert 'lane="1"' in xml


def test_assets_deduplicated_when_same_file(tmp_path):
    """No separate voiceover: primary and B-roll share the same source file."""
    gameplay = tmp_path / "gameplay.mp4"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                gameplay,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:10.000",
                "00:00:15.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                gameplay,
                "00:00:00.000",
                "00:00:02.000",
                "00:01:00.000",
                "00:01:02.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    assert xml.count("<asset ") == 1
