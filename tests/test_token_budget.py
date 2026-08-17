from directorcut.token_budget import estimate_video_tokens


def test_estimate_video_tokens():
    assert estimate_video_tokens(5400) > 0
