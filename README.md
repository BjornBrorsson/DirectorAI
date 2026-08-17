# DirectorCut-AI

Local-first, cloud-intelligent rough-cut editor for gameplay and narrative video. Takes raw capture + voiceover + optional script and produces a DaVinci Resolve-ready FCPXML timeline.

## Quick start

1. Install [FFmpeg](https://ffmpeg.org) and add it to your PATH.
2. Copy `.env.example` to `.env` and add your `GEMINI_API_KEY`.
3. Install Python dependencies:
   ```bash
   python -m venv .venv
   # Windows
   .\.venv\Scripts\pip install -e ".[dev]"
   # macOS/Linux
   .venv/bin/pip install -e ".[dev]"
   ```
4. Place source files in `ingest/`:
   - one video file (`mp4`, `mov`, `mkv`)
   - optional voiceover audio
   - optional script `.txt`
5. Run the pipeline:
   ```bash
   .\.venv\Scripts\directorcut run
   ```
   Output goes to `output/<project>/`.

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
3. Open `http://127.0.0.1:8000` in your browser.

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
