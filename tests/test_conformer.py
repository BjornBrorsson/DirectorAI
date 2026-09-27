import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from directorcut.conformer.fcpxml import _validate_spine, build_fcpxml
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


def test_primary_clip_is_asset_clip_with_probed_asset(tmp_path, monkeypatch):
    """Regression: Resolve imported every clip as a frozen frame-1 still with
    no audio - <video> elements are video-only and assets without
    duration/format can't be seeked past frame 0."""
    video = tmp_path / "video.mp4"
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml.run_ffprobe",
        lambda args, settings=None: json.dumps(
            {
                "format": {"duration": "6136.316667"},
                "streams": [
                    {"codec_type": "video"},
                    {"codec_type": "audio", "channels": 2, "sample_rate": "48000"},
                ],
            }
        ),
    )
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                video,
                "00:00:00.000",
                "00:00:15.000",
                "00:03:45.000",
                "00:04:00.000",
            )
        ],
    )
    xml = build_fcpxml(blueprint)

    assert "<asset-clip" in xml  # carries the file's embedded audio
    assert 'start="13500/60s"' in xml
    # The asset element must declare the media's real length + frame rate.
    assert 'duration="368179/60s"' in xml
    assert 'format="r1"' in xml
    assert 'hasAudio="1"' in xml
    assert 'hasVideo="1"' in xml
    # Resolve expects the sequence/clip metadata its own exports carry:
    # tcStart=0 keeps spine offsets inside the timeline (without it Resolve
    # places clips by source time -> hour+ of dead space, seen 2026-09-20).
    assert 'tcStart="0s"' in xml
    assert 'audioRate="48000"' in xml
    assert 'audioSources="1"' in xml
    assert 'enabled="1"' in xml


def test_sequence_declares_duration_and_tcstart(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml.run_ffprobe",
        lambda args, settings=None: json.dumps(
            {
                "format": {"duration": "100.0"},
                "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            }
        ),
    )
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                video,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            )
        ],
    )
    xml = build_fcpxml(blueprint)

    seq_line = next(line for line in xml.splitlines() if "<sequence" in line)
    assert 'duration="300/60s"' in seq_line
    assert 'tcStart="0s"' in seq_line
    assert "<!DOCTYPE fcpxml>" in xml


def test_audio_asset_gets_duration_but_no_format(tmp_path, monkeypatch):
    audio = tmp_path / "voice.mp3"
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml.run_ffprobe",
        lambda args, settings=None: json.dumps(
            {
                "format": {"duration": "10.0"},
                "streams": [{"codec_type": "audio"}],
            }
        ),
    )
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "A2_MUSIC",
                audio,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            )
        ],
    )
    xml = build_fcpxml(blueprint)

    asset_line = next(
        line for line in xml.splitlines() if 'name="voice.mp3"' in line
    )
    assert 'hasVideo="0"' in asset_line
    assert 'hasAudio="1"' in asset_line
    assert 'duration="600/60s"' in asset_line
    assert "format=" not in asset_line


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


def test_music_lane_attached_with_ducking(tmp_path):
    """A2_MUSIC becomes a connected <audio lane="-2"> with volume keyframes
    dipping it while the primary track plays."""
    voiceover = tmp_path / "voiceover.mp3"
    music = tmp_path / "bed.mp3"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:10.000",
                "00:00:00.000",
                "00:00:10.000",
            ),
            _cut(
                "A2_MUSIC",
                music,
                "00:00:00.000",
                "00:00:20.000",
                "00:00:00.000",
                "00:00:20.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    assert 'lane="-2"' in xml
    assert "<keyframeAnimation>" in xml
    assert 'value="-12"' in xml


def test_sfx_lane_and_no_duck_without_overlap(tmp_path):
    """An SFX entirely outside the voice range still lands on lane -3 but
    gets no ducking keyframes."""
    voiceover = tmp_path / "voiceover.mp3"
    hit = tmp_path / "hit.mp3"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            ),
            _cut(
                "A3_SFX",
                hit,
                "00:00:30.000",
                "00:00:32.000",
                "00:00:00.000",
                "00:00:02.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    assert 'lane="-3"' in xml
    # No overlap -> no volume animation on the SFX clip.
    assert "keyframeAnimation" not in xml


def test_image_broll_emits_video_ref(tmp_path):
    """A still image on V2 must be a <video> clip, not <audio>."""
    voiceover = tmp_path / "voiceover.mp3"
    still = tmp_path / "card.png"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:05.000",
                "00:00:00.000",
                "00:00:05.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                still,
                "00:00:01.000",
                "00:00:03.000",
                "00:00:00.000",
                "00:00:02.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    assert "card.png" in xml
    # Mid-clip overlay gets a real parent-timebase offset (the parent's
    # start is 0, so 1s into a 60fps clip = 60/60s).
    assert 'offset="60/60s"' in xml


def test_connected_clip_offset_uses_parent_timebase(tmp_path):
    """Regression: a connected clip's offset lives in its parent's timebase
    (parent `start` + local offset), not parent-local. Resolve placed
    overlays minutes early when we emitted only the local offset."""
    voiceover = tmp_path / "voiceover.mp3"
    overlay = tmp_path / "overlay.mp4"
    stray = tmp_path / "stray.mp4"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=30.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:10.000",
                "00:00:20.000",
                "00:01:00.000",
                "00:01:10.000",
            ),
            _cut(
                "V2_OVERLAY_BROLL",
                overlay,
                "00:00:12.000",
                "00:00:14.000",
                "00:00:30.000",
                "00:00:32.000",
            ),
            # Outside every primary clip -> anchored to a <gap>.
            _cut(
                "V2_OVERLAY_BROLL",
                stray,
                "00:01:00.000",
                "00:01:02.000",
                "00:00:00.000",
                "00:00:02.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint)
    root = ET.fromstring(xml)

    attached = root.find(".//video[@lane='1']")
    assert attached is not None
    # parent start (60s) + local offset (2s) at 30fps.
    assert attached.get("offset") == "1860/30s"

    gap_child = root.find(".//gap/video")
    assert gap_child is not None
    # Gaps have no `start` (implicitly 0), so children keep a local offset.
    assert gap_child.get("offset") == "0/30s"


# --- Spine <video> guardrail -------------------------------------------------


def test_validate_spine_rejects_bare_video():
    """A <video> element directly on the spine must fail loudly - Resolve
    renders it as a frozen, misplaced still (verified 2026-09-22)."""
    spine = ET.Element("spine")
    ET.SubElement(spine, "video", ref="a1", offset="0s")
    with pytest.raises(ConformError):
        _validate_spine(spine)


def test_validate_spine_rejects_laneless_nested_video():
    """A connected <video> without a lane can't be placed unambiguously."""
    spine = ET.Element("spine")
    clip = ET.SubElement(spine, "asset-clip", ref="a1", offset="0s")
    ET.SubElement(clip, "video", ref="a2", offset="0s")
    with pytest.raises(ConformError):
        _validate_spine(spine)


def test_validate_spine_accepts_connected_video_with_lane():
    spine = ET.Element("spine")
    clip = ET.SubElement(spine, "asset-clip", ref="a1", offset="0s")
    ET.SubElement(clip, "video", ref="a2", lane="1", offset="0s")
    ET.SubElement(clip, "audio", ref="a3", lane="-2", offset="0s")
    _validate_spine(spine)  # must not raise


def test_premiere_target_shares_fcpx_ducking_shape(tmp_path):
    """`premiere` emits the fcpx document shape: per-cut audio with volume
    keyframeAnimation, no baked beds. Premiere import itself is unverified
    (docs/wiki/nle_targets.md lists the TODOs) - this pins the XML flavour."""
    voiceover = tmp_path / "voiceover.mp3"
    music = tmp_path / "bed.mp3"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="test", target_fps=60.0),
        cuts=[
            _cut(
                "V1_A1_PRIMARY",
                voiceover,
                "00:00:00.000",
                "00:00:10.000",
                "00:00:00.000",
                "00:00:10.000",
            ),
            _cut(
                "A2_MUSIC",
                music,
                "00:00:00.000",
                "00:00:20.000",
                "00:00:00.000",
                "00:00:20.000",
            ),
        ],
    )
    xml = build_fcpxml(blueprint, target="premiere")
    assert 'lane="-2"' in xml
    assert "<keyframeAnimation>" in xml
    assert 'value="-12"' in xml


def test_premiere_is_an_accepted_config_target():
    from directorcut.config import Settings

    s = Settings(_env_file=None, fcpxml_target="premiere")
    assert s.fcpxml_target == "premiere"


def test_build_fcpxml_fails_if_spine_tag_regresses(tmp_path, monkeypatch):
    """Simulate a future regression that re-introduces spine <video> output -
    build_fcpxml must raise ConformError rather than emit it."""
    video = tmp_path / "video.mp4"
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml._spine_clip_tag", lambda cut: "video"
    )
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
    with pytest.raises(ConformError):
        build_fcpxml(blueprint)
