"""FastAPI app and WebSocket endpoint."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from directorcut.config import Settings
from directorcut.ingest import AUDIO_EXTS, IMAGE_EXTS, SCRIPT_EXTS, VIDEO_EXTS
from directorcut.log import get_logger, setup_logging
from directorcut.models import AssemblyBlueprint, NarrativeNode, VisualEvent
from directorcut.pipeline import _write_json, run_pipeline

logger = get_logger(__name__)

settings = Settings()
setup_logging(settings.log_level, secrets=settings.configured_secrets())

PROJECTS_DIR = settings.output_dir / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Project names become directory names on disk, so they must be restricted to
# a safe slug - this is what stops a request like `?name=C:\Windows\Temp\pwn`
# (or `../..`) from escaping PROJECTS_DIR. `name` is validated centrally here
# rather than per-endpoint so every route that touches the filesystem is
# covered, including query-param usages that FastAPI's path-routing alone
# would not constrain.
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _validate_project_name(name: str) -> str:
    if not _PROJECT_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Project name must be 1-64 characters: letters, digits, '_' or '-' only.",
        )
    return name


def _project_dir(name: str) -> Path:
    _validate_project_name(name)
    p = PROJECTS_DIR / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _clean_path(raw: str) -> Path:
    """Normalize a user-supplied filesystem path.

    Windows Explorer's "Copy as path" wraps the value in literal double
    quotes, and shell-pasted paths may carry single quotes - strip one
    matching outer pair, then expand `~` and %ENV% vars so all common
    spellings resolve to the same file.
    """
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return Path(os.path.expandvars(os.path.expanduser(cleaned)))


_MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | SCRIPT_EXTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API starting")
    yield
    logger.info("API stopping")


app = FastAPI(title="DirectorCut-AI API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/settings")
def get_settings():
    return {
        "gemini_model": settings.gemini_model,
        "gemini_backend": settings.gemini_backend,
        "elevenlabs_configured": settings.safe_elevenlabs_key is not None,
        "log_level": settings.log_level,
        "env": settings.env,
    }


@app.get("/browse")
def browse(path: str | None = None):
    """List a server-side directory so the UI can offer a real file picker.

    The API and browser share a filesystem (local-first tool), so the picker
    navigates actual directories and returns absolute paths the ingest
    endpoint can copy from directly.
    """
    base = _clean_path(path) if path else Path.home()
    if not base.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {base}")
    try:
        entries = list(base.iterdir())
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Cannot list {base}: {exc}"
        ) from exc

    dirs: list[dict] = []
    files: list[dict] = []
    for entry in entries:
        try:
            if entry.is_dir():
                if not entry.name.startswith("."):
                    dirs.append({"name": entry.name, "path": str(entry)})
            elif entry.is_file() and entry.suffix.lower() in _MEDIA_EXTS:
                files.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "size": entry.stat().st_size,
                    }
                )
        except OSError:
            # Broken symlinks / ACL-restricted entries shouldn't kill listing.
            continue
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    parent = base.parent
    return {
        "path": str(base),
        "parent": str(parent) if parent != base else None,
        "dirs": dirs,
        "files": files,
    }


@app.post("/projects")
def create_project(name: str):
    _project_dir(name)
    return {"id": name, "name": name, "status": "created"}


@app.get("/projects")
def list_projects():
    return [
        {
            "id": p.name,
            "name": p.name,
            "status": "done" if (p / f"{p.name}.fcpxml").exists() else (
                "ready" if any((p / "ingest").glob("*")) else "idle"
            ),
        }
        for p in PROJECTS_DIR.iterdir()
        if p.is_dir()
    ]


@app.get("/projects/{name}/status")
def project_status(name: str):
    pdir = _project_dir(name)
    fcpxml = pdir / f"{name}.fcpxml"
    state = _RUNS.get(name)
    return {
        "id": name,
        "done": fcpxml.exists(),
        "outputs": [f.name for f in pdir.iterdir() if f.is_file()],
        # Stage the run is paused at for review, if any - lets a reloaded
        # page re-show the gate UI without replaying the WS history.
        "awaiting_gate": state.awaiting_gate if state else None,
    }


# Canonical ingest subdirectory per role - these names are exactly what
# directorcut.ingest._ROLE_DIRS recognizes, so files land pre-classified.
_ROLE_SUBDIRS = {
    "a_roll": "aroll",
    "b_roll": "broll",
    "voiceover": "voiceover",
    "music": "music",
    "sfx": "sfx",
    "image": "images",
    "script": "scripts",
}


class AssetInput(BaseModel):
    """One file to copy into the project's ingest dir, with its role."""

    path: str
    # "auto" drops the file at the ingest root so the interpretation pass
    # classifies it; any other role lands in that role's subdirectory.
    role: str = "auto"


class IngestRequest(BaseModel):
    """Absolute paths to files already present on the server's filesystem.

    This is a local-first tool: the browser and the API server share the
    same filesystem, so we validate + copy by path rather than accepting
    multipart file uploads. `video_path`/`audio_path`/`script_path` are the
    legacy single-file shape; `assets` is the multi-file form.
    """

    video_path: str | None = None
    audio_path: str | None = None
    script_path: str | None = None
    assets: list[AssetInput] = []
    direction: str | None = None


@app.post("/projects/{name}/ingest")
def ingest_project(name: str, request: IngestRequest):
    pdir = _project_dir(name)
    ingest_dir = pdir / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    to_copy: list[tuple[str, str, str]] = []  # (field_name, raw_path, role)
    if request.video_path:
        to_copy.append(("video_path", request.video_path, "a_roll"))
    if request.audio_path:
        to_copy.append(("audio_path", request.audio_path, "voiceover"))
    if request.script_path:
        to_copy.append(("script_path", request.script_path, "script"))
    for asset in request.assets:
        role = asset.role.lower()
        if role != "auto" and role not in _ROLE_SUBDIRS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{asset.role}'. Expected one of: "
                f"auto, {', '.join(sorted(_ROLE_SUBDIRS))}.",
            )
        to_copy.append(("assets[]", asset.path, role))

    copied = []
    for field_name, raw_path, role in to_copy:
        if not raw_path:
            continue
        src = _clean_path(raw_path)
        if not src.is_file():
            raise HTTPException(
                status_code=400, detail=f"{field_name}: file not found: {src}"
            )
        dest_dir = ingest_dir / _ROLE_SUBDIRS[role] if role != "auto" else ingest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.error("Failed to copy %s for project %s: %s", src, name, exc)
            raise HTTPException(
                status_code=400, detail=f"{field_name}: could not copy file: {exc}"
            ) from exc
        copied.append(str(dest.relative_to(ingest_dir)))

    if not copied:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    if request.direction and request.direction.strip():
        (ingest_dir / "direction.txt").write_text(
            request.direction.strip(), encoding="utf-8"
        )

    logger.info("Ingested %d file(s) for project %s: %s", len(copied), name, copied)
    return {"id": name, "files": copied}


class _RunState:
    """One project's pipeline run, with every emitted event kept for replay.

    A 2h ingest can take an hour+; clients disconnect, tabs close, React
    StrictMode remounts sockets. Keeping the event log here lets any new
    subscriber replay the history and reattach instead of launching a
    duplicate run.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status = "running"  # running | done | error
        self.started = time.time()
        # Review-gate state: the stage the run is paused at (None = not
        # gated/waiting) and the event the resume endpoint sets to release it.
        # A fresh Event per gate (reassigned in the gate hook) avoids
        # clear-then-set races when a run pauses at several stages.
        self.awaiting_gate: str | None = None
        self.resume_event = threading.Event()


_RUNS: dict[str, _RunState] = {}
_RUNS_LOCK = threading.Lock()

# Review gates: which pass checkpoints pause the run per gate_mode. The gated
# stage names match the pipeline's emit() stage names and each maps onto the
# checkpoint JSON the pipeline has just committed when the gate fires.
GATE_MODES = ("auto", "pass1", "pass2", "pass3", "all")
_GATEABLE_STAGES = ("pass1", "pass2", "pass3")
_GATE_STAGES: dict[str, frozenset] = {
    "auto": frozenset(),
    "pass1": frozenset({"pass1"}),
    "pass2": frozenset({"pass2"}),
    "pass3": frozenset({"pass3"}),
    "all": frozenset(_GATEABLE_STAGES),
}
_STAGE_ARTIFACTS = {
    "pass1": "narrative.json",
    "pass2": "visual.json",
    "pass3": "blueprint.json",
}


def _make_gate(state: _RunState, gate_mode: str, name: str):
    """Build the run_pipeline `gate` hook for a review-gated run.

    The pipeline invokes it right after each stage checkpoint lands; for a
    gated stage it emits an `awaiting_gate` event and blocks the pipeline
    thread until the resume endpoint sets `state.resume_event`. Any edits
    POSTed to the stage artifact while paused are picked up because the
    pipeline reloads the checkpoint JSON after the gate returns.
    """
    gated = _GATE_STAGES[gate_mode]

    def gate(stage: str) -> None:
        if stage not in gated:
            return
        state.resume_event = threading.Event()
        state.awaiting_gate = stage
        state.events.append({"status": "awaiting_gate", "stage": stage})
        logger.info("Run %s paused at review gate %s", name, stage)
        state.resume_event.wait()
        state.awaiting_gate = None
        state.events.append(
            {
                "status": "progress",
                "stage": stage,
                "detail": "review gate approved - resuming",
            }
        )

    return gate


def _start_run(
    name: str, ingest_dir: Path, gate_mode: str = "auto", fresh: bool = False
) -> _RunState:
    """Launch run_pipeline on a daemon thread and return its shared state.

    The pipeline must NEVER run on the event loop - a single run would
    freeze every endpoint (health, browse, other websockets) for its whole
    duration.
    """
    state = _RunState()

    def on_progress(evt: dict) -> None:
        state.events.append(evt)

    def worker() -> None:
        try:
            # run_pipeline() writes to `<output_dir>/<project_name>`; without
            # this override it uses the server's global output_dir instead of
            # PROJECTS_DIR, so /status and /download would never find results.
            settings_copy = settings.model_copy(
                update={"ingest_dir": ingest_dir, "output_dir": PROJECTS_DIR}
            )
            # "auto" attaches no hook at all - the pipeline then also skips
            # the post-stage artifact reload, keeping ungated runs identical
            # to before.
            result = run_pipeline(
                settings_copy,
                name,
                on_progress=on_progress,
                fresh=fresh,
                gate=(
                    _make_gate(state, gate_mode, name)
                    if gate_mode != "auto"
                    else None
                ),
            )
            state.events.append(
                {
                    "status": "done",
                    "xml_path": str(result["xml_path"]),
                    "output_dir": str(result["output_dir"]),
                    # Recorded spend this run (costs.json totals) - None on
                    # runs that predated cost accounting.
                    "cost_usd": (result.get("costs") or {}).get("cost_usd"),
                }
            )
            state.status = "done"
        except Exception as exc:  # noqa: BLE001 - report to subscribers, don't lose it
            logger.exception("Pipeline run failed for project %s", name)
            state.events.append({"status": "error", "error": str(exc)})
            state.status = "error"

    threading.Thread(target=worker, daemon=True, name=f"pipeline-{name}").start()
    return state


def _get_or_start_run(
    name: str, gate_mode: str = "auto", fresh: bool = False
) -> _RunState | None:
    """Return the live run for a project, starting one if needed.

    Returns None when the project has no ingest files. `gate_mode` only
    applies when a new run is launched - an already-live run keeps whatever
    gates it was started with.
    """
    ingest_dir = _project_dir(name) / "ingest"
    if not any(ingest_dir.iterdir() if ingest_dir.exists() else []):
        return None
    with _RUNS_LOCK:
        state = _RUNS.get(name)
        if state is None or state.status != "running":
            state = _start_run(name, ingest_dir, gate_mode=gate_mode, fresh=fresh)
            _RUNS[name] = state
        return state


async def _stream_run_events(websocket: WebSocket, state: _RunState) -> None:
    """Replay the run's event history, then live-tail until it terminates."""
    idx = 0
    try:
        while True:
            while idx < len(state.events):
                await websocket.send_text(json.dumps(state.events[idx]))
                idx += 1
            if state.status != "running" and idx >= len(state.events):
                break
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        # Client vanished mid-run - the run itself continues server-side and
        # can be reattached to by reconnecting.
        pass


class RunRequest(BaseModel):
    """Optional body for POST /projects/{name}/run.

    `gate_mode` pauses the run for review after the given pass checkpoints:
    "auto" never pauses; "pass1"/"pass2"/"pass3" pause after that stage;
    "all" pauses at all three. While paused, GET /stage/{pass} returns the
    artifact and POST /stage/{pass}/resume (optional JSON edits) releases it.
    """

    gate_mode: str = "auto"
    fresh: bool = False


@app.post("/projects/{name}/run")
async def run_project(name: str, request: RunRequest | None = None):
    gate_mode = request.gate_mode if request else "auto"
    if gate_mode not in _GATE_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"gate_mode must be one of {', '.join(GATE_MODES)}.",
        )
    fresh = request.fresh if request else False
    state = _get_or_start_run(name, gate_mode=gate_mode, fresh=fresh)
    if state is None:
        return PlainTextResponse("No ingest files for project.", status_code=400)
    # A gated run pauses rather than finishing - return the gate state so the
    # caller can fetch the artifact and resume instead of blocking forever.
    while state.status == "running" and state.awaiting_gate is None:
        await asyncio.sleep(0.5)
    if state.awaiting_gate is not None:
        return {
            "id": name,
            "status": "awaiting_gate",
            "stage": state.awaiting_gate,
        }
    if state.status == "error":
        error = next(
            (e["error"] for e in reversed(state.events) if e.get("status") == "error"),
            "pipeline failed",
        )
        raise HTTPException(status_code=500, detail=error)
    done = next(
        e for e in reversed(state.events) if e.get("status") == "done"
    )
    return {
        "id": name,
        "xml_path": done["xml_path"],
        "output_dir": done["output_dir"],
    }


def _stage_artifact_path(name: str, stage: str) -> tuple[Path, str]:
    """Resolve a gateable stage to its checkpoint artifact on disk."""
    pdir = _project_dir(name)
    artifact = _STAGE_ARTIFACTS.get(stage)
    if artifact is None:
        raise HTTPException(
            status_code=400,
            detail=f"stage must be one of {', '.join(_GATEABLE_STAGES)}.",
        )
    return pdir / artifact, artifact


@app.get("/projects/{name}/stage/{stage}")
def get_stage_artifact(name: str, stage: str):
    """Return the checkpoint artifact a gated run is paused on for review."""
    path, artifact = _stage_artifact_path(name, stage)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"{artifact} has not been produced yet."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"unreadable {artifact}: {exc}"
        ) from exc
    payload = {"id": name, "stage": stage, "artifact": artifact, "data": data}
    if stage == "pass3":
        # Human-readable cut listing (same shape as `directorcut cut list`) -
        # the review the v1 UI shows instead of raw blueprint JSON alone.
        try:
            from directorcut.cutops import format_rows, list_cuts

            payload["listing"] = format_rows(list_cuts(path.parent))
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.debug("blueprint listing unavailable: %s", exc)
            payload["listing"] = []
    return payload


class ResumeRequest(BaseModel):
    """Optional replacement JSON for the stage artifact before resuming.

    `edits` replaces the whole artifact file: an array of nodes/events for
    pass1/pass2, a blueprint object for pass3. Omitted = approve as-is.
    """

    edits: Any = None


def _apply_stage_edits(path: Path, stage: str, edits: object) -> None:
    """Validate `edits` against the stage schema and swap the artifact."""
    try:
        if stage == "pass3":
            if not isinstance(edits, dict):
                raise TypeError("expected a blueprint JSON object")
            AssemblyBlueprint.model_validate(edits)
        else:
            if not isinstance(edits, list):
                raise TypeError("expected a JSON array")
            model = NarrativeNode if stage == "pass1" else VisualEvent
            for item in edits:
                model.model_validate(item)
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid {stage} edits: {exc}"
        ) from exc
    _write_json(path, json.dumps(edits, indent=2))


@app.post("/projects/{name}/stage/{stage}/resume")
def resume_stage(name: str, stage: str, request: ResumeRequest | None = None):
    """Release a run paused at `stage`, optionally applying artifact edits."""
    path, _artifact = _stage_artifact_path(name, stage)
    state = _RUNS.get(name)
    if state is None or state.awaiting_gate != stage:
        raise HTTPException(
            status_code=409,
            detail=f"no run for '{name}' is waiting at gate '{stage}'.",
        )
    if request is not None and request.edits is not None:
        _apply_stage_edits(path, stage, request.edits)
    state.resume_event.set()
    logger.info("Resuming %s from gate %s", name, stage)
    return {"id": name, "status": "resumed", "stage": stage}


@app.get("/projects/{name}/download/{filename}")
def download(name: str, filename: str):
    pdir = _project_dir(name).resolve()
    target = (pdir / filename).resolve()
    if not target.is_relative_to(pdir):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    if not target.exists() or not target.is_file():
        return PlainTextResponse("File not found.", status_code=404)
    return FileResponse(target)


@app.websocket("/ws/{name}")
async def ws(websocket: WebSocket, name: str):
    try:
        _validate_project_name(name)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("action") == "run":
                # _get_or_start_run returns the EXISTING run when one is
                # already live, so reconnecting (tab closed, StrictMode
                # remount) reattaches instead of launching a duplicate
                # multi-hour pipeline.
                gate_mode = msg.get("gate_mode") or "auto"
                if gate_mode not in _GATE_STAGES:
                    await websocket.send_text(
                        json.dumps({"error": f"unknown gate_mode: {gate_mode}"})
                    )
                    continue
                state = _get_or_start_run(name, gate_mode=gate_mode)
                if state is None:
                    await websocket.send_text(json.dumps({"error": "No ingest files"}))
                    continue
                await _stream_run_events(websocket, state)
            else:
                await websocket.send_text(json.dumps({"status": "unknown action"}))
    except WebSocketDisconnect:
        logger.info("WebSocket %s disconnected", name)


# Serve built frontend as the lowest-priority mount.
frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
