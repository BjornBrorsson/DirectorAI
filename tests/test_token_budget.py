from directorcut.safety.token_guard import check_token_budget
from directorcut.token_budget import (
    SAFE_INPUT_TOKENS,
    chunk_proxy,
    estimate_video_tokens,
)


def test_estimate_video_tokens():
    assert estimate_video_tokens(5400) > 0


def test_chunk_at_capacity_stays_under_ceiling(tmp_path):
    """Regression: frames_per_chunk used to be computed with the truncated
    int(276.15) tokens-per-frame, so a max-size chunk estimated ~950,568
    tokens - just over the 950,000 ceiling - and borderline videos (e.g. a
    ~1h42m source) died at check_token_budget on the FIRST chunk."""
    proxy = tmp_path / "long_visual.mp4"
    proxy.write_bytes(b"v")
    # 6136s ~ the real 1h42m recording that hit this in production.
    chunks = chunk_proxy(proxy, 6136.0, fps=1.0)
    assert len(chunks) == 2
    for chunk in chunks:
        assert chunk.estimated_tokens <= SAFE_INPUT_TOKENS
        check_token_budget(chunk.source_path, chunk.frame_count)  # must not raise


def test_chunk_proxy_splits_long_video(tmp_path):
    proxy = tmp_path / "v.mp4"
    proxy.write_bytes(b"v")
    chunks = chunk_proxy(proxy, 100.0, fps=1.0, max_tokens=1000)
    assert len(chunks) > 1
    assert chunks[0].start_time == 0.0
    assert chunks[-1].end_time == 100.0
