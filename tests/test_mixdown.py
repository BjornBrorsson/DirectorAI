"""Mixdown (Resolve-proof audio beds) + resolve-target conform tests."""

import json
import math
import re
import shutil
import struct
import subprocess
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from directorcut.conformer import mixdown
from directorcut.conformer.fcpxml import build_fcpxml
from directorcut.conformer.mixdown import render_mixdown
from directorcut.models import AssemblyBlueprint, Cut, TimelineMeta

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _cut(track, source_file, timeline_in, timeline_out, source_in, source_out):
    return Cut(
        track=track,
        source_file=source_file,
        timeline_in=timeline_in,
        timeline_out=timeline_out,
        source_in=source_in,
        source_out=source_out,
    )


def _tc(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}"


class _FakeProc:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_ffmpeg_run(calls, tmp_path):
    """subprocess.run replacement: records argv and writes a stub wav output."""

    def _run(cmd, capture_output=False, text=False, check=False, **kw):
        calls.append(list(cmd))
        out = Path(cmd[-1])
        if out.suffix == ".wav":
            out.write_bytes(b"RIFF" + b"\0" * 40)  # existence is what matters
        return _FakeProc()

    return _run


def _mixdown_blueprint(tmp_path):
    a, b = tmp_path / "take_a.mp4", tmp_path / "take_b.mp4"
    music, sfx = tmp_path / "ost.mp3", tmp_path / "hit.mp3"
    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="p", target_fps=30.0),
        cuts=[
            _cut("V1_A1_PRIMARY", a, "00:00:00.000", "00:00:02.000",
                 "00:00:10.000", "00:00:12.000"),
            _cut("V1_A1_PRIMARY", b, "00:00:02.000", "00:00:05.000",
                 "00:00:20.000", "00:00:23.000"),
            _cut("V1_A1_PRIMARY", a, "00:00:05.000", "00:00:07.000",
                 "00:00:30.000", "00:00:32.000"),
            _cut("A2_MUSIC", music, "00:00:00.000", "00:00:07.000",
                 "00:00:00.000", "00:00:07.000"),
            _cut("A3_SFX", sfx, "00:00:02.000", "00:00:03.000",
                 "00:00:00.000", "00:00:01.000"),
        ],
    )


def test_mixdown_builds_multi_source_voice_guide(tmp_path, monkeypatch):
    """Each distinct V1 source_file becomes one ffmpeg input fanned out with
    asplit; every cut is atrimmed to its source range and adelayed to its
    timeline_in."""
    calls = []
    monkeypatch.setattr(mixdown.subprocess, "run", _fake_ffmpeg_run(calls, tmp_path))
    monkeypatch.setattr(mixdown, "find_ffmpeg", lambda settings=None: "ffmpeg")
    monkeypatch.setattr(
        mixdown, "run_ffprobe", lambda *a, **k: "0\n"  # every file has audio
    )

    blueprint = _mixdown_blueprint(tmp_path)
    beds = render_mixdown(blueprint, tmp_path / "out")

    assert set(beds) == {"A2_MUSIC", "A3_SFX"}

    guide = (tmp_path / "out" / "voice_guide.ffgraph").read_text(encoding="utf-8")
    # Two distinct A-roll sources -> two asplit fan-outs (2 cuts + 1 cut).
    assert guide.count("asplit=2") == 1
    assert guide.count("asplit=1") == 1
    # First cut: source 10-12s at timeline 0 -> atrim 10..12, adelay 0.
    assert "atrim=start=10.000:end=12.000" in guide
    assert "adelay=0|0" in guide
    # Second source's cut: source 20-23s at timeline 2s -> adelay 2000.
    assert "atrim=start=20.000:end=23.000" in guide
    assert "adelay=2000|2000" in guide
    assert "anullsrc=r=48000" in guide

    music = (tmp_path / "out" / "music_bed.ffgraph").read_text(encoding="utf-8")
    assert "sidechaincompress=threshold=0.015:ratio=12" in music
    assert "volume=-8.0dB" in music
    # Voice guide is appended as the last (sidechain) input.
    assert "[bed][1:a]sidechaincompress" in music

    sfx = (tmp_path / "out" / "sfx_bed.ffgraph").read_text(encoding="utf-8")
    assert "adelay=2000|2000" in sfx
    assert "volume=-9.0dB" in sfx
    assert "sidechain" not in sfx

    # Three renders ran: voice guide + music bed + sfx bed.
    assert len(calls) == 3
    assert Path(calls[0][-1]).name == "voice_guide.wav"
    assert Path(calls[1][-1]).name == "music_bed_ducked.wav"
    assert Path(calls[2][-1]).name == "sfx_bed.wav"


def test_mixdown_skips_empty_lanes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mixdown.subprocess, "run", _fake_ffmpeg_run(calls, tmp_path))
    monkeypatch.setattr(mixdown, "find_ffmpeg", lambda settings=None: "ffmpeg")
    monkeypatch.setattr(mixdown, "run_ffprobe", lambda *a, **k: "0\n")

    a = tmp_path / "take.mp4"
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="p", target_fps=30.0),
        cuts=[
            _cut("V1_A1_PRIMARY", a, "00:00:00.000", "00:00:02.000",
                 "00:00:00.000", "00:00:02.000"),
        ],
    )
    assert render_mixdown(blueprint, tmp_path / "out") == {}
    assert calls == []


def test_resolve_target_emits_single_bed_clips_no_params(tmp_path, monkeypatch):
    """Resolve-target conform: per-cut A2/A3 children are replaced by ONE
    bed clip per lane, anchored to the first spine clip at parent start;
    no <param> elements anywhere."""
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml.run_ffprobe",
        lambda args, settings=None: json.dumps(
            {
                "format": {"duration": "7.0"},
                "streams": [
                    {"codec_type": "video"},
                    {"codec_type": "audio", "channels": 2, "sample_rate": "48000"},
                ],
            }
        ),
    )
    blueprint = _mixdown_blueprint(tmp_path)
    music_bed = tmp_path / "music_bed_ducked.wav"
    sfx_bed = tmp_path / "sfx_bed.wav"

    xml = build_fcpxml(
        blueprint,
        target="resolve",
        beds={"A2_MUSIC": music_bed, "A3_SFX": sfx_bed},
    )

    assert "<param" not in xml
    root = ET.fromstring(xml)
    spine = root.find(".//spine")
    first_clip = list(spine)[0]
    bed_children = [c for c in first_clip if c.tag == "audio"]
    assert len(bed_children) == 2
    by_lane = {c.get("lane"): c for c in bed_children}
    # Parent start = first cut's source_in (10s @30fps); beds sit at media
    # position 0 with duration = probed bed length.
    assert by_lane["-2"].get("offset") == "300/30s"
    assert by_lane["-2"].get("start") == "0/30s"
    assert by_lane["-2"].get("duration") == "210/30s"
    assert by_lane["-3"].get("offset") == "300/30s"

    # The original music/SFX assets were replaced by the beds.
    names = [a.get("name") for a in root.findall(".//asset")]
    assert "music_bed_ducked.wav" in names
    assert "sfx_bed.wav" in names
    assert "ost.mp3" not in names
    assert "hit.mp3" not in names


def test_resolve_target_without_bed_keeps_plain_audio(tmp_path, monkeypatch):
    """A lane with cuts but no rendered bed still emits per-cut <audio>
    (un-ducked beats silent) but never <param> keyframes."""
    monkeypatch.setattr(
        "directorcut.conformer.fcpxml.run_ffprobe",
        lambda args, settings=None: json.dumps(
            {
                "format": {"duration": "7.0"},
                "streams": [{"codec_type": "audio"}],
            }
        ),
    )
    blueprint = _mixdown_blueprint(tmp_path)
    xml = build_fcpxml(blueprint, target="resolve", beds=None)
    assert "<param" not in xml
    assert 'lane="-2"' in xml  # per-cut fallback survived


# --- Real-ffmpeg verification ------------------------------------------------


def _write_tone_wav(path: Path, segments: list[tuple[float, float]], freq=440.0,
                    total=6.0, rate=48000):
    """Mono wav: sine in each (start,end) segment, silence elsewhere."""
    frames = bytearray()
    cursor = 0.0
    amp = int(0.4 * 32767)

    def tone(dur):
        n = int(dur * rate)
        return b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(n)
        )

    for s, e in segments:
        frames += b"\0" * 2 * int((s - cursor) * rate)
        frames += tone(e - s)
        cursor = e
    frames += b"\0" * 2 * int((total - cursor) * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _integrated_lufs(path: Path, start: float, dur: float) -> float:
    """ffmpeg ebur128 measurement of a segment; last 'I:' wins."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-ss", str(start), "-t", str(dur), "-i", str(path),
            "-af", "ebur128", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    )
    hits = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", proc.stderr)
    assert hits, proc.stderr[-500:]
    return float(hits[-1])


@pytest.mark.skipif(not HAS_FFMPEG, reason="requires real ffmpeg/ffprobe")
def test_ducked_bed_drops_at_least_12db_under_voice(tmp_path):
    """AC: the sidechain-ducked music bed measures >=12 dB lower under the
    voice than in the gaps (real ffmpeg, synthesized fixtures)."""
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    # Voice talks 0-1.5s and 3-4.5s; the spine places those two ranges
    # back-to-back (0-1.5, 1.5-3.0). Music is a constant tone across 0-6s.
    _write_tone_wav(voice, [(0.0, 1.5), (3.0, 4.5)], freq=330.0)
    _write_tone_wav(music, [(0.0, 6.0)], freq=660.0)

    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="p", target_fps=30.0),
        cuts=[
            _cut("V1_A1_PRIMARY", voice, _tc(0.0), _tc(1.5), _tc(0.0), _tc(1.5)),
            _cut("V1_A1_PRIMARY", voice, _tc(1.5), _tc(3.0), _tc(3.0), _tc(4.5)),
            _cut("A2_MUSIC", music, _tc(0.0), _tc(6.0), _tc(0.0), _tc(6.0)),
        ],
    )
    beds = render_mixdown(blueprint, tmp_path)
    bed = beds["A2_MUSIC"]

    under_voice = _integrated_lufs(bed, 0.4, 0.9)
    in_gap = _integrated_lufs(bed, 4.2, 1.5)  # release (500ms) fully decayed
    assert in_gap - under_voice >= 12.0
