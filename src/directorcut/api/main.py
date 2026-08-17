"""FastAPI app and WebSocket endpoint."""

from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from directorcut.config import Settings
from directorcut.log import get_logger, setup_logging
from directorcut.pipeline import run_pipeline

logger = get_logger(__name__)

settings = Settings()
setup_logging(settings.log_level)

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
        "log_level": settings.log_level,
        "env": settings.env,
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
    return {
        "id": name,
        "done": fcpxml.exists(),
        "outputs": [f.name for f in pdir.iterdir() if f.is_file()],
    }


class IngestRequest(BaseModel):
    """Absolute paths to files already present on the server's filesystem.

    This is a local-first tool: the browser and the API server share the
    same filesystem, so we validate + copy by path rather than accepting
    multipart file uploads.
    """

    video_path: str
    audio_path: str | None = None
    script_path: str | None = None


@app.post("/projects/{name}/ingest")
def ingest_project(name: str, request: IngestRequest):
    pdir = _project_dir(name)
    ingest_dir = pdir / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    fields = {
        "video_path": request.video_path,
        "audio_path": request.audio_path,
        "script_path": request.script_path,
    }
    copied = []
    for field_name, raw_path in fields.items():
        if not raw_path:
            continue
        src = Path(raw_path)
        if not src.is_file():
            raise HTTPException(
                status_code=400, detail=f"{field_name}: file not found: {raw_path}"
            )
        dest = ingest_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.error("Failed to copy %s for project %s: %s", src, name, exc)
            raise HTTPException(
                status_code=400, detail=f"{field_name}: could not copy file: {exc}"
            ) from exc
        copied.append(dest.name)

    if not copied:
        raise HTTPException(status_code=400, detail="At least one file (video_path) is required.")

    logger.info("Ingested %d file(s) for project %s: %s", len(copied), name, copied)
    return {"id": name, "files": copied}


@app.post("/projects/{name}/run")
async def run_project(name: str):
    ingest_dir = _project_dir(name) / "ingest"
    if not any(ingest_dir.iterdir() if ingest_dir.exists() else []):
        return PlainTextResponse("No ingest files for project.", status_code=400)
    # run_pipeline() writes to `<output_dir>/<project_name>`; without this
    # override it uses the server's global output_dir instead of
    # PROJECTS_DIR, so /status and /download would never find the results.
    settings_copy = settings.model_copy(
        update={"ingest_dir": ingest_dir, "output_dir": PROJECTS_DIR}
    )
    result = run_pipeline(settings_copy, name)
    return {
        "id": name,
        "xml_path": str(result["xml_path"]),
        "output_dir": str(result["output_dir"]),
    }


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
                ingest_dir = _project_dir(name) / "ingest"
                if not any(ingest_dir.iterdir() if ingest_dir.exists() else []):
                    await websocket.send_text(json.dumps({"error": "No ingest files"}))
                    continue
                settings_copy = settings.model_copy(
                    update={"ingest_dir": ingest_dir, "output_dir": PROJECTS_DIR}
                )
                await websocket.send_text(json.dumps({"status": "starting"}))
                try:
                    result = run_pipeline(settings_copy, name)
                except Exception as exc:  # noqa: BLE001 - report to client, don't crash the socket
                    logger.exception("Pipeline run failed for project %s", name)
                    await websocket.send_text(json.dumps({"status": "error", "error": str(exc)}))
                    continue
                await websocket.send_text(
                    json.dumps({"status": "done", "xml_path": str(result["xml_path"])})
                )
            else:
                await websocket.send_text(json.dumps({"status": "unknown action"}))
    except WebSocketDisconnect:
        logger.info("WebSocket %s disconnected", name)


# Serve built frontend as the lowest-priority mount.
frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
