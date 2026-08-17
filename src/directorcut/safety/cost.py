"""Cost estimator."""

from __future__ import annotations

from directorcut.log import get_logger
from directorcut.token_budget import estimate_video_tokens

logger = get_logger(__name__)

# Gemini 3.7 Flash-ish placeholder pricing (per 1M tokens).
PRICE_INPUT_PER_1M = 0.15
PRICE_OUTPUT_PER_1M = 0.60


def estimate_cost(duration_seconds: float, fps: float = 1.0) -> float:
    """Estimate API cost for a single run based on the real source duration.

    `duration_seconds` should come from the ingest manifest (probed via
    FFprobe), not a hardcoded assumption, so the estimate reflects the actual
    project rather than a fixed 90-minute placeholder.
    """
    frame_count = max(1, int(duration_seconds * fps))
    video_tokens = estimate_video_tokens(frame_count)
    # Three passes: narrative, visual, assembly.
    input_tokens = video_tokens * 3
    # Conservative output estimate: 10% of input.
    output_tokens = int(input_tokens * 0.1)
    input_cost = (input_tokens / 1_000_000) * PRICE_INPUT_PER_1M
    output_cost = (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_1M
    total = input_cost + output_cost
    logger.info(
        "Cost estimate: input=%d output=%d total=$%.4f",
        input_tokens,
        output_tokens,
        total,
    )
    return total
