"""Patient retry loop for smoke step (c): _AssemblyPlan on the director model.

gemini-3.8-flash was returning persistent 503s while flash-lite worked.
Poll every 60s for up to ~25 minutes; print progress; exit 0 on first success.
"""

from __future__ import annotations

import logging
import sys
import time

from directorcut.assembly.director import _AssemblyPlan
from directorcut.config import Settings
from directorcut.gemini_client import GeminiClient, GeminiError

PROMPT = (
    "Return an assembly plan with exactly one cut: track V1_A1_PRIMARY, "
    'source_file "take1.mp4", timeline_in 00:00:00.000, timeline_out '
    "00:00:05.000, source_in 00:00:10.000, source_out 00:00:15.000. "
    "No audio cues."
)


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    settings = Settings()
    client = GeminiClient(settings, max_retries=2, base_delay=2.0)
    model = settings.model_for("director")
    deadline = time.time() + 25 * 60
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            t0 = time.time()
            plan = client.call(
                prompt=PROMPT, media=None, response_model=_AssemblyPlan, model=model
            )
            print(
                f"[{attempt}] {model} OK in {time.time() - t0:.0f}s -> "
                f"{len(plan.cuts)} cut(s)",
                flush=True,
            )
            for c in plan.cuts:
                print(f"    {c.track} {c.source_file} {c.timeline_in}-{c.timeline_out}")
            return 0
        except GeminiError as exc:
            msg = str(exc).splitlines()[0][:100]
            print(f"[{attempt}] {model} fail: {msg} - sleeping 60s", flush=True)
            time.sleep(60)
    print("gave up after 25 min", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
