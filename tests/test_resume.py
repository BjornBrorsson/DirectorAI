"""Pipeline resume / stage checkpointing + STT transcript caching.

These tests stub the stage functions on the pipeline module (ingest,
transcribe_audio, analyze_visual_proxy, ...) so no ffmpeg, no model calls,
and no real .env values are needed - what is exercised is the checkpoint
bookkeeping itself.
"""

import json
from pathlib import Path

import pytest

import directorcut.pipeline as pipeline
from directorcut.config import Settings
from directorcut.elevenlabs_client import ElevenLabsClient
from directorcut.models import (
    Asset,
    AssetRole,
    EnergyLevel,
    IngestManifest,
    NarrativeNode,
    ThematicTag,
    VisualEvent,
    VisualState,
)
from directorcut.narrative.stt import transcribe_audio


def _node(node_id="N_001"):
    return NarrativeNode(
        node_id=node_id,
        time_start="00:00:00.000",
        time_end="00:00:05.000",
        spoken_text="hello world",
        thematic_tag=ThematicTag.EXPOSITION,
        energy_level=EnergyLevel.MEDIUM,
    )


def _event(event_id="V_001", source=None):
    return VisualEvent(
        event_id=event_id,
        time_start="00:00:01.000",
        time_end="00:00:03.000",
        classification=VisualState.STATE_ACTION,
        visual_description="action",
        source_file=source,
    )


@pytest.fixture
def stubbed_pipeline(tmp_path, monkeypatch):
    """A pipeline whose stages are stubbed but whose checkpointing is real.

    Returns (settings, calls, ingest_dir, output_dir). `calls` counts how
    often each stage function ran across all runs in the test.
    """
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    video = ingest_dir / "clip.mp4"
    video.write_bytes(b"fake-video-v1")
    output_dir = tmp_path / "output"

    settings = Settings(
        _env_file=None,
        env="dev",
        ingest_dir=ingest_dir,
        output_dir=output_dir,
        # These tests stub AI stages but use fake media bytes, so the Resolve
        # mixdown's real ffmpeg would fail on them; the fcpx conform path is
        # pure XML and keeps the checkpoint semantics under test.
        fcpxml_target="fcpx",
    )
    manifest = IngestManifest(
        project_name="proj",
        video_path=video.resolve(),
        duration_seconds=10.0,
        assets=[
            Asset(
                path=video.resolve(),
                role=AssetRole.A_ROLL,
                duration_seconds=10.0,
            )
        ],
    )

    calls = {"ingest": 0, "stt": 0, "tag": 0, "pass2": 0}

    def _touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
        return path

    monkeypatch.setattr(
        pipeline,
        "ingest",
        lambda *a, **k: calls.__setitem__("ingest", calls["ingest"] + 1)
        or manifest,
    )
    monkeypatch.setattr(pipeline, "interpret_assets", lambda m, s: m)
    monkeypatch.setattr(
        pipeline,
        "extract_audio",
        lambda src, out: _touch(out / f"{src.stem}_audio.mp3"),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_visual_proxy",
        lambda src, out: _touch(out / f"{src.stem}_visual.mp4"),
    )

    def _fake_stt(audio_path, settings, cache_dir=None):
        calls["stt"] += 1
        return [_node()]

    monkeypatch.setattr(pipeline, "transcribe_audio", _fake_stt)
    monkeypatch.setattr(
        pipeline,
        "tag_narrative",
        lambda nodes, s, direction=None: (
            calls.__setitem__("tag", calls["tag"] + 1) or nodes
        ),
    )
    return settings, calls, ingest_dir, output_dir


def test_kill_after_pass1_resume_reuses_stt_and_tags(stubbed_pipeline, monkeypatch):
    """Crash in Pass 2 -> re-run resumes with STT+tags loaded, Pass 2 redone."""
    settings, calls, ingest_dir, output_dir = stubbed_pipeline

    def _boom(*a, **k):
        calls["pass2"] += 1
        raise RuntimeError("simulated crash mid-Pass-2")

    monkeypatch.setattr(pipeline, "analyze_visual_proxy", _boom)
    with pytest.raises(RuntimeError):
        pipeline.run_pipeline(settings, "proj")

    pdir = output_dir / "proj"
    # Pass-1 checkpoint landed before the crash.
    assert (pdir / "run_state.json").exists()
    assert (pdir / "assets.json").exists()
    assert (pdir / "narrative.json").exists()
    assert calls == {"ingest": 1, "stt": 1, "tag": 1, "pass2": 1}

    def _ok(proxy, duration, settings, source_file=None, on_chunk=None, checkpoint_path=None):
        calls["pass2"] += 1
        return [_event(source=source_file)]

    monkeypatch.setattr(pipeline, "analyze_visual_proxy", _ok)
    result = pipeline.run_pipeline(settings, "proj")

    assert (result["xml_path"]).exists()
    # ingest, STT, and tagging were all replayed from checkpoints.
    assert calls == {"ingest": 1, "stt": 1, "tag": 1, "pass2": 2}


def test_fresh_forces_full_rerun(stubbed_pipeline, monkeypatch):
    settings, calls, ingest_dir, output_dir = stubbed_pipeline
    monkeypatch.setattr(
        pipeline,
        "analyze_visual_proxy",
        lambda *a, **k: calls.__setitem__("pass2", calls["pass2"] + 1)
        or [_event()],
    )
    pipeline.run_pipeline(settings, "proj")
    assert calls["stt"] == 1

    # Auto-resume would reuse everything; --fresh must redo every stage.
    pipeline.run_pipeline(settings, "proj", fresh=True)
    assert calls == {"ingest": 2, "stt": 2, "tag": 2, "pass2": 2}


def test_changed_ingest_invalidates_checkpoints(stubbed_pipeline, monkeypatch):
    """Editing an ingest file busts the manifest hash -> stages re-run."""
    settings, calls, ingest_dir, output_dir = stubbed_pipeline
    monkeypatch.setattr(
        pipeline,
        "analyze_visual_proxy",
        lambda *a, **k: calls.__setitem__("pass2", calls["pass2"] + 1)
        or [_event()],
    )
    pipeline.run_pipeline(settings, "proj")
    assert calls["stt"] == 1

    (ingest_dir / "clip.mp4").write_bytes(b"fake-video-v2-changed")
    pipeline.run_pipeline(settings, "proj")
    assert calls == {"ingest": 2, "stt": 2, "tag": 2, "pass2": 2}


def test_pass2_chunk_checkpoint_loses_only_inflight_chunk(tmp_path, monkeypatch):
    """A crash mid-asset replays finished chunks from the JSONL checkpoint."""
    import directorcut.visual.chunked as vc

    settings = Settings(_env_file=None, env="test", gemini_backend="api")
    proxy = tmp_path / "p.mp4"
    proxy.write_bytes(b"x")
    frames = [("00:00:00.000", b"f")] * 400
    monkeypatch.setattr(
        vc, "load_visual_proxy", lambda p, fps=1.0: iter(frames)
    )

    calls = []
    kinds = [VisualState.STATE_ACTION, VisualState.STATE_UI, VisualState.STATE_DEATH]

    def fake_classify(chunk, frames, settings, source_file=None, **_kw):
        calls.append(chunk.index)
        if calls == [0, 1]:
            raise RuntimeError("simulated crash in chunk 2")
        return [
            VisualEvent(
                event_id=f"V_{chunk.index:03d}",
                time_start="00:00:01.000",
                time_end="00:00:03.000",
                # Distinct classifications keep the merge from collapsing the
                # three chunk results into one.
                classification=kinds[chunk.index % len(kinds)],
                visual_description="d",
                source_file=chunk.source_path,
            )
        ]

    monkeypatch.setattr(vc, "classify_visual", fake_classify)

    ckpt = tmp_path / "chunks.jsonl"
    # API chunk size ~144 frames -> 400s = 3 chunks; die inside chunk index 1.
    with pytest.raises(RuntimeError):
        vc.analyze_visual_proxy(proxy, 400.0, settings, checkpoint_path=ckpt)
    assert calls == [0, 1]

    # Chunk 0 was checkpointed; only chunks 1+ re-analyze on resume.
    lines = ckpt.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["chunk_index"] == 0

    events = vc.analyze_visual_proxy(proxy, 400.0, settings, checkpoint_path=ckpt)
    assert calls == [0, 1, 1, 2]
    assert len(events) == 3


def test_stt_cache_reuse_across_pass1_runs(tmp_path, monkeypatch):
    """Two _transcript_sources-driven Pass-1 runs hit the STT client once."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    audio = tmp_path / "vo.mp3"
    audio.write_bytes(b"audio-bytes")
    manifest = IngestManifest(
        project_name="p",
        assets=[
            Asset(path=audio, role=AssetRole.VOICEOVER, duration_seconds=5.0)
        ],
    )
    settings = Settings(
        _env_file=None, env="prod", elevenlabs_api_key="el-test-key"
    )

    calls = []

    def fake_stt(self, path):
        calls.append(path)
        return {"words": [{"text": "hi.", "start": 0.0, "end": 0.4, "type": "word"}]}

    monkeypatch.setattr(ElevenLabsClient, "speech_to_text", fake_stt)

    for _run in range(2):
        for audio_path, _src in pipeline._transcript_sources(manifest, out_dir):
            nodes = transcribe_audio(
                audio_path, settings, cache_dir=out_dir / "stt_cache"
            )
            assert nodes is not None

    assert len(calls) == 1
    assert list((out_dir / "stt_cache").glob("*.json"))


def test_stt_cache_key_covers_model(tmp_path, monkeypatch):
    """A different STT model id re-transcribes the same audio bytes."""
    audio = tmp_path / "vo.mp3"
    audio.write_bytes(b"audio-bytes")
    cache_dir = tmp_path / "stt_cache"
    calls = []

    def fake_stt(self, path):
        calls.append(path)
        return {"words": [{"text": "hi.", "start": 0.0, "end": 0.4, "type": "word"}]}

    monkeypatch.setattr(ElevenLabsClient, "speech_to_text", fake_stt)

    s1 = Settings(
        _env_file=None,
        env="prod",
        elevenlabs_api_key="el-test-key",
        elevenlabs_stt_model="scribe_v1",
    )
    s2 = Settings(
        _env_file=None,
        env="prod",
        elevenlabs_api_key="el-test-key",
        elevenlabs_stt_model="scribe_v2_experimental",
    )
    transcribe_audio(audio, s1, cache_dir=cache_dir)
    transcribe_audio(audio, s1, cache_dir=cache_dir)
    assert len(calls) == 1
    transcribe_audio(audio, s2, cache_dir=cache_dir)
    assert len(calls) == 2
    assert len(list(cache_dir.glob("*.json"))) == 2


def test_visual_fps_stride_sampling(tmp_path, monkeypatch):
    """DIRECTORCUT_VISUAL_FPS < 1 sends every k-th frame and tells the model
    the real sampling interval."""
    import directorcut.visual.chunked as vc

    settings = Settings(
        _env_file=None,
        env="test",
        gemini_backend="api",
        visual_fps=0.5,
    )
    proxy = tmp_path / "p.mp4"
    proxy.write_bytes(b"x")
    frames = [("t", bytes([i])) for i in range(10)]
    monkeypatch.setattr(vc, "load_visual_proxy", lambda p, fps=1.0: iter(frames))

    seen = {}

    def fake_classify(chunk, frames, settings, source_file=None, sample_interval_s=1.0):
        seen["frames"] = frames
        seen["interval"] = sample_interval_s
        return []

    monkeypatch.setattr(vc, "classify_visual", fake_classify)
    vc.analyze_visual_proxy(proxy, 10.0, settings)

    assert seen["interval"] == 2.0
    assert len(seen["frames"]) == 5  # every other frame of the 1fps cache


def test_pass2_dedupes_canonical_twin_assets(stubbed_pipeline, monkeypatch, tmp_path):
    """One recording registered under two paths (dual-role hardlink) gets a
    single visual analysis; events are cloned per registered source_file."""
    import os

    settings, calls, ingest_dir, output_dir = stubbed_pipeline
    src = ingest_dir / "clip.mp4"
    twin = ingest_dir / "clip_twin.mp4"
    os.link(src, twin)
    manifest = IngestManifest(
        project_name="proj",
        video_path=src.resolve(),
        duration_seconds=10.0,
        assets=[
            Asset(path=src.resolve(), role=AssetRole.A_ROLL, duration_seconds=10.0),
            Asset(path=twin.resolve(), role=AssetRole.B_ROLL, duration_seconds=10.0),
        ],
    )
    monkeypatch.setattr(pipeline, "ingest", lambda *a, **k: manifest)

    analyzed = []

    def _ok(proxy, duration, settings, source_file=None, on_chunk=None, checkpoint_path=None):
        analyzed.append(source_file)
        return [_event(source=source_file)]

    monkeypatch.setattr(pipeline, "analyze_visual_proxy", _ok)

    pipeline.run_pipeline(settings, "proj")

    assert len(analyzed) == 1
    vis = json.loads((output_dir / "proj" / "visual.json").read_text())
    srcs = {Path(e["source_file"]).name for e in vis}
    assert srcs == {"clip.mp4", "clip_twin.mp4"}
