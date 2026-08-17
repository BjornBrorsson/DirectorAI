"""Visual proxy ingestion."""

from __future__ import annotations

from pathlib import Path

import cv2

from directorcut.log import get_logger
from directorcut.utils import format_timecode

logger = get_logger(__name__)


def load_visual_proxy(proxy_path: Path, fps: float = 1.0):
    """Yield (timestamp, frame_bytes) for each frame in the proxy video."""
    cap = cv2.VideoCapture(str(proxy_path))
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        timestamp = frame_idx / fps
        _, buf = cv2.imencode(".jpg", frame)
        yield format_timecode(timestamp), buf.tobytes()
        frame_idx += 1
    cap.release()
