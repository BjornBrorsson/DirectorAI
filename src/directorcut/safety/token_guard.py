"""Token threshold monitoring."""

from __future__ import annotations

from pathlib import Path

from directorcut.exceptions import BudgetError
from directorcut.log import get_logger
from directorcut.token_budget import SAFE_INPUT_TOKENS, estimate_video_tokens

logger = get_logger(__name__)


def check_token_budget(
    proxy_path: Path,
    frame_count: int,
    max_tokens: int = SAFE_INPUT_TOKENS,
) -> None:
    """Raise BudgetError if a single chunk would exceed the token ceiling."""
    estimated = estimate_video_tokens(frame_count)
    logger.info("Estimated tokens for %s: %d", proxy_path, estimated)
    if estimated > max_tokens:
        raise BudgetError(
            f"Proxy {proxy_path} would require {estimated} tokens, "
            f"exceeding ceiling {max_tokens}. Enable chunking or lower resolution."
        )
