# DirectorCut-AI

Local-first, cloud-intelligent rough-cut editor. Dump your raw production files — A-roll, B-roll, voiceover, music, SFX, images, scripts — add optional plain-text direction, and get a DaVinci Resolve-ready FCPXML timeline with overlays, music/SFX placement, and automatic audio ducking.

## Quick start

1. Install [FFmpeg](https://ffmpeg.org) and add it to your PATH.
2. Copy `.env.example` to `.env` and add your `GEMINI_API_KEY` — or set `GEMINI_BACKEND=cli` to use an authenticated `gemini` CLI install instead.
3. Optionally add `ELEVENLABS_API_KEY` for AI-generated SFX and music.
4. Install Python dependencies:
   ```bash
   python -m venv .venv
   # Windows
   .\.venv\Scripts\pip install -e ".[dev]"
   # macOS/Linux
   .venv/bin/pip install -e ".[dev]"
   ```
5. Dump files into `ingest/` — organize by role subfolders or filename prefixes, or just drop files and let the AI interpret them:
   ```
   ingest/
     aroll/main.mp4        # or aroll_main.mp4
     broll/cutaway.mp4     # or broll_cutaway.mp4
     voiceover.mp3
     music/bed.mp3
     sfx/hit.mp3
     images/title.png
     scripts/script.txt
     direction.txt         # "fast and snappy editing"
   ```
6. Run the pipeline:
   ```bash
   .\.venv\Scripts\directorcut run --direction "fast and snappy editing"
   ```
   Output goes to `output/<project>/` (FCPXML + narrative/visual/blueprint/assets JSON + generated audio + `costs.json` spend ledger + `pack/` creator export: captions, chapters, thumbnails).

   Post-run blueprint edits:
   ```bash
   .\.venv\Scripts\directorcut cut list <project>          # numbered cut list
   .\.venv\Scripts\directorcut cut patch <project> "drop cut 54"   # natural-language edit
   .\.venv\Scripts\directorcut cut undo <project>          # revert the last patch
   ```
   Set `DIRECTORCUT_COST_CAP_USD` to hard-cap spend per project (run aborts
   cleanly on breach; raising the cap and re-running resumes).

## Web UI

1. Build the frontend once:
   ```bash
   cd frontend
   npm install
   npm run build
   ```
2. Start the API server:
   ```bash
   .\.venv\Scripts\directorcut web
   ```
3. Open `http://127.0.0.1:8000` — add files with per-file role pickers (or "Auto — let AI decide") and write direction in the text box.

## Development

- Lint: `ruff check src`
- Type check: `mypy src/directorcut`
- Tests: `pytest`

## Project structure

```
directorcut-ai/
├── src/directorcut/    # Python pipeline
├── frontend/           # React + Vite + TypeScript UI
├── tests/              # pytest suite
├── docs/               # Wiki, roadmap, and agentic workspace docs
├── tasks/              # Markdown ticket board
└── ingest/             # Default input directory (gitignored)
```

## License

MIT — see [LICENSE](LICENSE).
