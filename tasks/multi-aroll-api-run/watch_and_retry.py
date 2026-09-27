"""Wait for a working heavy-model key, then rerun Pass 3 for sts2-review-final.

Current blueprint (06:12) was produced by flash-lite under a quota storm:
10:18 timeline (>9:00 cap), 59% coverage (<60%), zero secondary punch-ins.
Free-tier gemini-3.8-flash resets its 20/day cap ~09:00 CET; the paid keys
may also recover if billing gets topped up. This watcher probes both every
15 min; the first key that answers 200 triggers a Pass-3-only rerun via
pipeline resume (blueprint.json is deleted -> stages downstream redo;
narrative/visual/STT/proxies replay from checkpoints).

The old blueprint is backed up first; if the rerun crashes, it's restored
so the project is never left without a conformable blueprint.

Run: .venv/Scripts/python.exe tasks/multi-aroll-api-run/watch_and_retry.py
Log: tasks/multi-aroll-api-run/watch_and_retry.log
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "output" / "sts2-review-final"
LOG = Path(__file__).parent / "watch_and_retry.log"
PROBE_MODEL = "gemini-3.8-flash"
PROBE_BODY = {"contents": [{"role": "user", "parts": [{"text": "Say OK"}]}]}
POLL_S = 900          # 15 min
DEADLINE_H = 6        # give up ~12:15 local if nothing recovers


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _key(name: str) -> str | None:
    env = (REPO / ".env").read_text(encoding="utf-8")
    m = re.search(rf'^{name}="?([^"\n]+)', env, re.M)
    return m.group(1).strip().strip('"') if m else None


def probe(api_key: str | None, model: str) -> bool:
    if not api_key:
        return False
    try:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent",
            params={"key": api_key},
            json=PROBE_BODY,
            timeout=45,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def main() -> int:
    LOG.write_text("", encoding="utf-8")
    deadline = time.time() + DEADLINE_H * 3600
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        paid_main = probe(_key("GEMINI_PAID_KEY_MAIN"), PROBE_MODEL)
        paid = probe(_key("GEMINI_PAID_KEY"), PROBE_MODEL)
        free_38 = probe(_key("GEMINI_FREE_KEY"), PROBE_MODEL)
        log(
            f"probe {attempt}: paid_main={paid_main} paid={paid} "
            f"free+3.8={free_38}"
        )
        if paid_main or paid or free_38:
            log("heavy-model capacity detected - rerunning Pass 3")
            return rerun_pass3()
        time.sleep(POLL_S)
    log("deadline hit - keeping the flash-lite blueprint")
    return 0


def rerun_pass3() -> int:
    blueprint = PROJECT / "blueprint.json"
    backup = blueprint.with_suffix(".attempt1.json")
    shutil.copy2(blueprint, backup)
    # Downstream artifacts regenerate on resume; delete so nothing stale wins.
    for name in (
        "blueprint.json", "sts2-review-final.fcpxml", "voice_guide.wav",
        "music_bed_ducked.wav", "sfx_bed.wav",
    ):
        (PROJECT / name).unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "pack", ignore_errors=True)
    proc = subprocess.run(
        [sys.executable, "-m", "directorcut.cli", "run",
         "-p", "sts2-review-final", "-y"],
        cwd=REPO,
        env={**__import__("os").environ,
             "GEMINI_BACKEND": "api", "DIRECTORCUT_ENV": "prod",
             "DIRECTORCUT_BACKEND_FALLBACK": "antigravity",
             "GEMINI_MODEL_CHAIN_DIRECTOR":
                 "gemini-3.8-flash,gemini-flash-lite-latest",
             "GEMINI_MODEL_CHAIN_NARRATIVE":
                 "gemini-flash-lite-latest,gemini-3.8-flash",
             "GEMINI_MODEL_CHAIN_AUDIO":
                 "gemini-flash-lite-latest,gemini-3.8-flash",
             "GEMINI_MODEL_CHAIN_VISUAL": "gemini-flash-lite-latest",
             "GEMINI_MODEL_CHAIN_INTERPRET": "gemini-flash-lite-latest",
             "DIRECTORCUT_FCPXML_TARGET": "resolve"},
        capture_output=True, text=True,
    )
    tail = (proc.stdout or "")[-3000:]
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(tail + "\n")
    if proc.returncode != 0 or not blueprint.exists():
        log(f"rerun FAILED (rc={proc.returncode}) - restoring backup")
        shutil.copy2(backup, blueprint)
        return 1
    log("rerun OK - new blueprint written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
