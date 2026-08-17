# Feature: Web Frontend

> Status: Draft | Last verified: 2026-08-17

## What it does

Provides a browser-based UI for managing DirectorCut-AI projects: upload raw assets, monitor pipeline progress, review generated cuts, adjust settings, and download the resulting FCPXML.

## Where the code will live

*To be added once the stack is chosen.*

## Candidate stacks

| Stack | Pros | Cons |
|---|---|---|
| React + Vite + TypeScript | Rich ecosystem, easy hot-reload, strong typing | More dependencies, build step |
| Next.js (static export) | SSG/SSR option, file routing, API routes if needed | Heavier for a simple dashboard |
| Vue 3 + Vite | Lightweight, fast to scaffold | Smaller ecosystem than React |
| HTMX + FastAPI templates | Minimal JS, server-driven | Less interactive for real-time progress |

## Recommendation

Start with **React + Vite + TypeScript** if real-time progress and rich cut review are desired; use **FastAPI + HTMX** if the goal is a thin administrative shell around the Python pipeline.

## Keys & config

* Same backend settings as CLI (`GEMINI_API_KEY`, `FFMPEG_PATH`, etc.).
* Frontend env: `VITE_API_BASE_URL` pointing to the local backend server.

## Gotchas

* File uploads for large video need chunked upload or direct filesystem path selection (since the app is local-first, a file-picker that returns an absolute path may be enough on desktop).
* Real-time progress requires WebSocket or Server-Sent Events from the backend.
* Keep secrets (API keys) on the backend; never expose them to the browser.
