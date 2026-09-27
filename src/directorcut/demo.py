"""Offline demo: synthesize fixture media and run ingest -> FCPXML.

`directorcut demo` gives a first-run user (or an eval on a clean machine) a
real end-to-end pipeline run with zero keys and zero network: a ~5s
testsrc2+sine clip is rendered via `ffmpeg -f lavfi`, a tiny script fixture
is written, and the normal pipeline runs in dev mode so every model/API
call resolves to its stub. ffmpeg/ffprobe still must be on PATH - they are
deliberately external (see directorcut.spec / docs/wiki/packaging.md).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError
from directorcut.ffmpeg import run_ffmpeg
from directorcut.log import get_logger

logger = get_logger(__name__)

_DEMO_SECONDS = 5

_DEMO_SCRIPT = (
    "This is a DirectorCut demo clip. "
    "The pipeline just edited it without any API keys. "
    "Check the timeline in your editor!"
)


def _render_clip(clip: Path) -> None:
    """Render the demo A-roll via lavfi testsrc2+sine (libx264, mpeg4 fallback)."""
    base = [
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        str(_DEMO_SECONDS),
        "-pix_fmt",
        "yuv420p",
        "-shortest",
    ]
    try:
        run_ffmpeg([*base, "-c:v", "libx264", "-c:a", "aac", str(clip)])
    except FFmpegError:
        # Minimal ffmpeg builds (e.g. some distro packages) ship no libx264 -
        # mpeg4-in-mp4 is everywhere and fine for a 5s fixture.
        logger.info("libx264 unavailable - demo clip falls back to mpeg4")
        run_ffmpeg([*base, "-c:v", "mpeg4", "-c:a", "aac", str(clip)])


def prepare_demo_ingest(ingest_dir: Path) -> Path:
    """Synthesize the demo A-roll clip + script fixture; returns the clip."""
    ingest_dir.mkdir(parents=True, exist_ok=True)
    clip = ingest_dir / "aroll_demo.mp4"
    if not clip.exists():
        _render_clip(clip)
    script = ingest_dir / "script.txt"
    if not script.exists():
        script.write_text(_DEMO_SCRIPT, encoding="utf-8")
    return clip


def run_demo(workdir: Path | None, project: str = "demo") -> dict:
    """Run ingest -> FCPXML fully offline under `workdir` (or a temp dir).

    The result dict is `run_pipeline`'s plus `workdir` (kept so the printed
    output path stays valid - the caller decides whether to clean up).
    """
    workdir = workdir or Path(tempfile.mkdtemp(prefix="directorcut-demo-"))
    ingest_dir = workdir / "ingest"
    output_dir = workdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    prepare_demo_ingest(ingest_dir)
    # env="dev" makes every model/API call a stub before any key is read, so
    # ambient keys can never leak the demo onto the network. _env_file=None
    # keeps .env/user-config out; explicit ingest/output dirs override env.
    # (The sibling key fields can't be passed as kwargs together - a
    # pydantic-settings alias-restore quirk raises KeyError - so isolation
    # relies on dev_mode short-circuiting every paid call, which it does.)
    settings = Settings(
        _env_file=None,
        env="dev",
        ingest_dir=ingest_dir,
        output_dir=output_dir,
    )
    from directorcut.pipeline import run_pipeline

    result = run_pipeline(settings, project)
    result["workdir"] = workdir
    return result
