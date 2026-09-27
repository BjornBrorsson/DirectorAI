"""Final overnight validation run: sts2-review-final.

Launches the full pipeline on the take2 ingest tree with:
- key routing per user policy (free first for lite calls, PAID_KEY_MAIN
  for heavy, agy CLI as the last-resort backend)
- per-task model chains so a quota/503 storm degrades to flash-lite
  instead of dying

Run from repo root:
    .venv/Scripts/python.exe tasks/multi-aroll-api-run/final_run.py

Log: tasks/multi-aroll-api-run/final_run.log
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOG = Path(__file__).parent / "final_run.log"

ENV = {
    **os.environ,
    "GEMINI_BACKEND": "api",
    "DIRECTORCUT_ENV": "prod",
    "DIRECTORCUT_BACKEND_FALLBACK": "antigravity",
    # Chain order rationale (2026-09-23): both paid keys were 402 at launch,
    # and free-tier gemini-3.8-flash is past its 20/day cap until ~9am CET.
    # Lite-first on the BATCHED calls skips ~10min of dead retries per call;
    # the director keeps 3.8-first because its quota may reset mid-run and
    # it only makes one call anyway.
    "GEMINI_MODEL_CHAIN_DIRECTOR": "gemini-3.8-flash,gemini-flash-lite-latest",
    "GEMINI_MODEL_CHAIN_NARRATIVE": "gemini-flash-lite-latest,gemini-3.8-flash",
    "GEMINI_MODEL_CHAIN_AUDIO": "gemini-flash-lite-latest,gemini-3.8-flash",
    "GEMINI_MODEL_CHAIN_VISUAL": "gemini-flash-lite-latest",
    "GEMINI_MODEL_CHAIN_INTERPRET": "gemini-flash-lite-latest",
    "DIRECTORCUT_FCPXML_TARGET": "resolve",
}


def main() -> int:
    cmd = [
        sys.executable, "-m", "directorcut.cli",
        # No --fresh: auto-resume reuses the checkpoints the first attempt
        # already wrote (proxies, media bake, stt_cache, assets.json).
        "run", "-p", "sts2-review-final", "-y",
    ]
    with LOG.open("w", encoding="utf-8", buffering=1) as log:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            env=ENV,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(f"pid={proc.pid} log={LOG}", flush=True)
        return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
