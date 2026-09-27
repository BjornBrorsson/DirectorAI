from pathlib import Path

from directorcut.proxy import audio as audio_module
from directorcut.proxy.audio import extract_audio_tracks


def test_extract_audio_tracks_maps_each_stream(tmp_path, monkeypatch):
    """Every embedded stream gets its own ffmpeg -map extraction so no
    track is silently downmixed or dropped."""
    calls = []

    def fake_run_ffmpeg(args, settings=None):
        calls.append(args)
        Path(args[-1]).write_bytes(b"mp3 bytes")

    monkeypatch.setattr(audio_module, "run_ffmpeg", fake_run_ffmpeg)
    source = tmp_path / "obs.mp4"
    source.write_bytes(b"v")

    outs = extract_audio_tracks(source, tmp_path / "out", 3)

    assert [o.name for o in outs] == [
        "obs_stream0.mp3",
        "obs_stream1.mp3",
        "obs_stream2.mp3",
    ]
    assert len(calls) == 3
    for i, args in enumerate(calls):
        assert args[args.index("-map") + 1] == f"0:a:{i}"
        assert "-vn" in args


def test_extract_audio_tracks_skips_existing(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = (out_dir / "obs_stream0.mp3").resolve()
    existing.write_bytes(b"cached")

    calls = []
    monkeypatch.setattr(
        audio_module, "run_ffmpeg", lambda args, settings=None: calls.append(args)
    )
    source = tmp_path / "obs.mp4"
    source.write_bytes(b"v")

    outs = extract_audio_tracks(source, out_dir, 2)

    assert outs[0] == existing
    assert len(calls) == 1  # only stream1 still needs extraction
    assert calls[0][calls[0].index("-map") + 1] == "0:a:1"
