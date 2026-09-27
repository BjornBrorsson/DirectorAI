# Feature: Web Frontend

> Status: Draft | Last verified: 2026-09-18

## What it does

React + Vite + TypeScript UI over the FastAPI backend: create a project, add any number of files with a per-file role dropdown (including "Auto — let AI decide"), write free-text direction, monitor the pipeline over WebSocket, and download the FCPXML + JSON artifacts.

## Where the code lives

* `frontend/src/components/UploadForm.tsx` — dynamic asset rows + direction textarea + `FileBrowser` modal; posts `{assets: [{path, role}], direction}` to `/projects/{name}/ingest`
* `frontend/src/components/Settings.tsx` — shows `gemini_backend` + ElevenLabs configured status
* `directorcut.api.main` — FastAPI app; `IngestRequest` accepts both the new `assets[]` shape and the legacy `video_path`/`audio_path`/`script_path` fields
* `directorcut.api.main::browse` — `GET /browse?path=` lists dirs + media files for the file-picker modal (`~L98`)
* `directorcut.api.main::_clean_path` — strips surrounding quotes, expands `~`/`%ENV%` before `Path()` (`~L55`)

## Keys & config

* Backend: `GEMINI_BACKEND=api|cli|antigravity`, `GEMINI_API_KEY`, `GEMINI_CLI_BIN`, `GEMINI_CLI_MODEL`, `ANTIGRAVITY_CLI_*`, `ELEVENLABS_API_KEY`, `ELEVENLABS_MUSIC_MODEL`.
* Frontend env: `VITE_API_BASE_URL` pointing to the local backend server (dev proxy: `/api` → `127.0.0.1:8000`).

## Gotchas

* Role "auto" files land at the ingest root and are classified by the interpretation pass; explicit roles land in canonical subdirs (`aroll/`, `broll/`, …) that `ingest()` recognizes.
* The `direction` field is persisted as `ingest/direction.txt` — also usable from the CLI.
* Keep secrets (API keys) on the backend; `/settings` only exposes `elevenlabs_configured` as a boolean.
* `npm run lint` has no ESLint config yet — the build's `tsc -b` is the effective typecheck.
* The file picker is server-side browsing (`/browse`), not a browser-native `<input type=file>` — a web app can't see local absolute paths, and ingest copies by path.
* Windows "Copy as path" produces literal `"C:\…"` strings; `_clean_path` strips them — without it every pasted path reported "file not found".
* Pipeline runs live in the `_RUNS` registry on daemon threads — never on the event loop (a 2h run would freeze every endpoint). `run_pipeline(on_progress=...)` emits `{stage, detail, elapsed_s}` events; WS clients replay history + live-tail, so reconnecting mid-run reattaches instead of duplicating a multi-hour job.

## Review gates between passes (v1 — 2026-09-23)

* `POST /projects/{name}/run` accepts `{gate_mode: "auto"|"pass1"|"pass2"|"pass3"|"all", fresh}`; the WS `{"action": "run"}` message accepts `gate_mode` too (UploadForm sends it via `?gate=` on the progress route). `auto` = no pauses (identical to before — no gate hook is even attached).
* Mechanism: `run_pipeline` grew a `gate(stage)` hook (`pipeline.py::_run_gate`) invoked right after each pass checkpoint JSON lands. The API's hook (`api.main::_make_gate`) appends an `{"status": "awaiting_gate", "stage"}` event and blocks the pipeline thread on a fresh `threading.Event`; `POST /projects/{name}/stage/{pass}/resume` sets it. After the hook returns the pipeline **reloads the artifact from disk**, so edits POSTed while paused flow into the rest of the run.
* `GET /projects/{name}/stage/{pass}` returns `{artifact, data}` — narrative nodes / visual events / blueprint JSON; pass3 additionally carries `listing` (the `cut list`-style V1 preview). `POST .../resume` accepts `{edits: <JSON>}` which replaces the artifact file wholesale (schema-validated; invalid edits → 400 and the gate stays held). Resume without a live gate → 409.
* Frontend v1: `Progress.tsx` `GatePanel` — banner + artifact JSON textarea + "Approve & continue" / "Apply edits & continue". While paused, `awaiting_gate` is the last event in the WS stream; the panel disappears as soon as the resumed run emits again.
* Not in v1: reject+**rerun** a single pass without redoing earlier ones (needs mid-run stage invalidation — the checkpoint system already supports re-run-from-artifact on a fresh process, so a `rerun` flag could delete the stage JSON + markers and relaunch), gate for the ingest/interpret stage, and per-field (non-JSON) editing UI.
