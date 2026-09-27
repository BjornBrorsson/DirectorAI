"""Creator export pack: captions.srt/.vtt, chapters.txt, thumbs/ (ticket 2).

All projects are fabricated - blueprint/narrative/assets JSON written by
hand, STT cache seeded with a content-hashed entry, and ffmpeg thumbnail
calls monkeypatched - so no media decode or API access is needed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import directorcut.export_pack as ep
from directorcut.config import Settings
from directorcut.models import (
    AssemblyBlueprint,
    Asset,
    AssetRole,
    Cut,
    EnergyLevel,
    IngestManifest,
    NarrativeNode,
    ThematicTag,
    TimelineMeta,
)
from directorcut.utils import format_timecode


def _tc(s: float) -> str:
    return format_timecode(s)


def _cut(track, src: Path, tl_in, tl_out, src_in, src_out) -> Cut:
    return Cut(
        track=track,
        source_file=src,
        timeline_in=_tc(tl_in),
        timeline_out=_tc(tl_out),
        source_in=_tc(src_in),
        source_out=_tc(src_out),
    )


def _node(nid, start, end, text, tag, energy, source=None) -> NarrativeNode:
    return NarrativeNode(
        node_id=nid,
        time_start=_tc(start),
        time_end=_tc(end),
        spoken_text=text,
        thematic_tag=tag,
        energy_level=energy,
        source_file=source,
    )


def _write(project_dir: Path, name: str, payload) -> None:
    if isinstance(payload, str):
        (project_dir / name).write_text(payload, encoding="utf-8")
    else:
        (project_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _seed_stt_cache(project_dir: Path, audio: Path, words: list[dict]) -> None:
    """Write the content-addressed Scribe cache entry load_cached_words reads."""
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    cache = project_dir / "stt_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{digest}_scribe_v1.json").write_text(
        json.dumps({"words": words}), encoding="utf-8"
    )


@pytest.fixture
def project(tmp_path):
    """A fabricated project dir: blueprint + narrative + STT cache.

    Timeline: cut1 A[10-20]->tl 0-10, cut2 A[30-40]->tl 10-20 (both covered
    by EXPOSITION nodes), cut3 B[0-70]->tl 20-90 (EMOTIONAL_SPIKE HIGH),
    cut4 A[50-60]->tl 90-100 (short EXPOSITION run - folds into the spike
    chapter), plus one V2 overlay. A has a cached STT transcript; B doesn't.
    """
    src_a = tmp_path / "A.mp4"
    src_b = tmp_path / "B.mp4"
    src_a.write_bytes(b"video-a")
    src_b.write_bytes(b"video-b")
    project_dir = tmp_path / "output" / "proj"
    project_dir.mkdir(parents=True)

    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="proj"),
        cuts=[
            _cut("V1_A1_PRIMARY", src_a, 0, 10, 10, 20),
            _cut("V1_A1_PRIMARY", src_a, 10, 20, 30, 40),
            _cut("V1_A1_PRIMARY", src_b, 20, 90, 0, 70),
            _cut("V1_A1_PRIMARY", src_a, 90, 100, 50, 60),
            _cut("V2_OVERLAY_BROLL", src_b, 2, 6, 0, 4),
        ],
    )
    nodes = [
        _node(
            "N_001", 10.0, 20.0, "welcome to the show",
            ThematicTag.EXPOSITION, EnergyLevel.MEDIUM, src_a,
        ),
        _node(
            "N_002", 30.0, 40.0, "and now the setup",
            ThematicTag.EXPOSITION, EnergyLevel.MEDIUM, src_a,
        ),
        _node(
            "N_003", 0.0, 10.0, "this is the big moment!",
            ThematicTag.EMOTIONAL_SPIKE, EnergyLevel.HIGH, src_b,
        ),
        _node(
            "N_004", 50.0, 60.0, "closing thoughts",
            ThematicTag.EXPOSITION, EnergyLevel.LOW, src_a,
        ),
    ]
    manifest = IngestManifest(
        project_name="proj",
        assets=[
            Asset(path=src_a, role=AssetRole.A_ROLL, duration_seconds=60.0),
            Asset(path=src_b, role=AssetRole.B_ROLL, duration_seconds=90.0),
        ],
        duration_seconds=150.0,
    )

    _write(project_dir, "blueprint.json", blueprint.model_dump(mode="json"))
    _write(project_dir, "narrative.json", [n.model_dump(mode="json") for n in nodes])
    _write(project_dir, "assets.json", manifest.model_dump(mode="json"))

    # Word timing for A.mp4 only (via its extracted stream0 audio proxy);
    # B.mp4 deliberately has none, so its cut must fall back to node text.
    audio_a = project_dir / "A_stream0.mp3"
    audio_a.write_bytes(b"audio-a")
    words = [
        {"text": "welcome", "start": 10.2, "end": 10.6, "type": "word"},
        {"text": "to", "start": 10.7, "end": 10.9, "type": "word"},
        {"text": "the", "start": 11.0, "end": 11.2, "type": "word"},
        {"text": "show.", "start": 11.3, "end": 11.8, "type": "word"},
        # Between cut2 (ends src 40) and cut4 (starts src 50): outside every
        # cut's source range - must not appear in captions.
        {"text": "outside", "start": 45.0, "end": 45.5, "type": "word"},
    ]
    _seed_stt_cache(project_dir, audio_a, words)
    settings = Settings(
        _env_file=None, env="test", elevenlabs_stt_model="scribe_v1"
    )
    return project_dir, settings, src_a, src_b


def _no_ffmpeg(monkeypatch):
    """Make thumbnail grabs deterministic: record argv, write a fake jpeg."""
    calls = []

    def fake_ffmpeg(args, settings=None):
        calls.append(args)
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"jpeg")

    monkeypatch.setattr(ep, "run_ffmpeg", fake_ffmpeg)
    return calls


def test_captions_use_word_timing_when_cached(project, monkeypatch):
    project_dir, settings, _a, _b = project
    _no_ffmpeg(monkeypatch)
    written = ep.write_export_pack(project_dir, settings=settings)
    names = {p.name for p in written}
    assert {"captions.srt", "captions.vtt", "chapters.txt"} <= names

    srt = (project_dir / "pack" / "captions.srt").read_text(encoding="utf-8")
    # Words group into one cue: src 10.2-11.8 on cut1 -> tl 0.2-1.8.
    assert "00:00:00,200 --> 00:00:01,800" in srt
    assert "welcome to the show." in srt
    # Word at src 45s is outside every cut - never captioned.
    assert "outside" not in srt

    vtt = (project_dir / "pack" / "captions.vtt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT")
    # VTT uses dot-millisecond timecodes, SRT commas.
    assert "00:00:00.200 --> 00:00:01.800" in vtt


def test_captions_fall_back_to_node_text_without_word_timing(project, monkeypatch):
    project_dir, settings, _a, _b = project
    _no_ffmpeg(monkeypatch)
    ep.write_export_pack(project_dir, settings=settings)
    srt = (project_dir / "pack" / "captions.srt").read_text(encoding="utf-8")
    # Cut3 covers node N_003 ("this is the big moment!") src 0-10 -> tl 20-30.
    assert "this is the big moment!" in srt
    assert "00:00:20,000 --> 00:00:30,000" in srt
    # And cut2's covered node (no words inside its 30-40 source range).
    assert "and now the setup" in srt


def test_chapters_merge_same_tag_runs_and_fold_short(project, monkeypatch):
    project_dir, settings, _a, _b = project
    _no_ffmpeg(monkeypatch)
    ep.write_export_pack(project_dir, settings=settings)
    lines = (project_dir / "pack" / "chapters.txt").read_text().splitlines()
    # cuts 1+2 merge into one EXPOSITION chapter at 0; cut3's 70s spike run
    # is a second chapter at 00:00:20; cut4's 10s exposition run folds into
    # it (< _MIN_CHAPTER_S) instead of fragmenting the list.
    assert len(lines) == 2
    assert lines[0].startswith("00:00:00 ")
    assert "welcome to the show" in lines[0]
    assert lines[1].startswith("00:00:20 ")
    assert "big moment" in lines[1]


def test_thumbnails_prefer_high_energy_spike(project, monkeypatch):
    project_dir, settings, _a, src_b = project
    calls = _no_ffmpeg(monkeypatch)
    written = ep.write_export_pack(project_dir, settings=settings)
    thumbs = sorted((project_dir / "pack" / "thumbs").glob("*.jpg"))
    # HIGH spike + backfill -> at least _THUMB_MIN picks, capped at 5.
    assert 3 <= len(thumbs) <= 5
    assert thumbs == [p for p in written if p.suffix == ".jpg"]
    # First grab is the N_003 midpoint (src 5s) on B.mp4.
    first = calls[0]
    assert "-ss" in first and f"{5.0:.3f}" in first
    assert str(src_b) in first


def test_thumbnails_survive_ffmpeg_failures(project, monkeypatch):
    project_dir, settings, _a, _b = project
    from directorcut.exceptions import FFmpegError

    calls = []

    def flaky(args, settings=None):
        if not calls:
            calls.append(args)
            raise FFmpegError("boom")
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"jpeg")

    monkeypatch.setattr(ep, "run_ffmpeg", flaky)
    written = ep.write_export_pack(project_dir, settings=settings)
    # The failed first pick is skipped; the rest still write.
    thumbs = [p for p in written if p.suffix == ".jpg"]
    assert len(thumbs) >= 2


def test_no_blueprint_writes_nothing(tmp_path):
    assert ep.write_export_pack(tmp_path) == []
    assert not (tmp_path / "pack").exists()


def test_unscripted_marker_nodes_get_tag_titles(tmp_path, monkeypatch):
    """Coverage-marker nodes ("Unscripted segment ...") can't be titles."""
    src = tmp_path / "A.mp4"
    src.write_bytes(b"v")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    blueprint = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="p"),
        cuts=[_cut("V1_A1_PRIMARY", src, 0, 10, 0, 10)],
    )
    nodes = [
        _node(
            "S1_001", 0, 10, "Unscripted segment 00:00:00-00:00:10",
            ThematicTag.EXPOSITION, EnergyLevel.LOW, src,
        )
    ]
    _write(project_dir, "blueprint.json", blueprint.model_dump(mode="json"))
    _write(project_dir, "narrative.json", [n.model_dump(mode="json") for n in nodes])
    monkeypatch.setattr(
        ep,
        "run_ffmpeg",
        lambda args, settings=None: Path(args[-1]).write_bytes(b"j"),
    )
    ep.write_export_pack(project_dir)
    text = (project_dir / "pack" / "chapters.txt").read_text()
    assert "Unscripted" not in text
    assert "Exposition" in text
