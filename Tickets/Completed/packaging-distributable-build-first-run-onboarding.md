# Packaging: distributable build + first-run onboarding

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High (product) |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

To sell to actual people: PyInstaller/portable build, no dev env;
first-run wizard collecting keys + backend + model prefs (stored in
user config dir, not repo .env); sane defaults so a creator gets a
timeline without reading docs. Includes secrets-in-config handling and
a no-key demo mode using stubs.

## Acceptance Criteria

- [ ] Clean machine installs and runs ingest->fcpxml demo with bundled fixtures
- [ ] Keys entered in onboarding never written to repo
- [ ] Demo mode produces a valid FCPXML offline

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-23: implemented. `directorcut.spec` at repo root (PyInstaller 6.x
  one-file): entry `src/directorcut/cli.py`, bundles `docs/`,
  `collect_submodules("directorcut"|"uvicorn")` for the lazy stage imports +
  uvicorn's string app import; ffmpeg/ffprobe stay external on PATH
  (documented); faster-whisper stack excluded (binary size) with a comment
  on re-enabling. `directorcut setup` wizard writes
  `%APPDATA%/DirectorCut/config.env` / `~/.config/directorcut/config.env`
  (`DIRECTORCUT_CONFIG_DIR` override) via `config.write_user_config` -
  atomic, 0600 best-effort, skipped answers become `# KEY=` comments; blank
  input keeps existing values; keys never printed.
  `Settings.__init__` defaults `_env_file` to `(user_config_path(), ".env")`
  -> precedence process env > repo .env/`--config` > user config;
  `_env_file=None` still disables dotenv entirely.
  `directorcut demo` (`--workdir`, `-p`) synthesizes ~5s testsrc2+sine mp4 +
  script.txt via `ffmpeg -f lavfi` (libx264, mpeg4 fallback), runs
  ingest->fcpxml with `Settings(_env_file=None, env="dev")` (all model/API
  calls stubbed), prints the FCPXML path (`src/directorcut/demo.py`).
  Verified real: `pip install pyinstaller` -> 6.22.3;
  `pyinstaller directorcut.spec` -> `dist/directorcut.exe` (~77MB; first
  build ~6min, rebuild ~100s); frozen `demo` ran end-to-end ->
  `tasks/packaging-demo/output/demo/demo.fcpxml`; frozen `setup` wrote
  config.env over piped stdin; `--help` lists all commands.
  NOT verified: clean-machine install (no dev env present to simulate).
  Quirks found: `click.prompt(hide_input=True)` hangs on piped stdin on
  Windows (win_getpass reads the console via msvcrt) - hidden only when
  `sys.stdin.isatty()`; `Settings(_env_file=None, gemini_api_key=...,
  gemini_paid_key_main=...)` raises KeyError (pydantic-settings 2.15
  alias-restore overlap - AliasChoices contains GEMINI_PAID_KEY_MAIN) -
  avoided in demo.py, noted in `docs/wiki/packaging.md`.
  Tests: `tests/test_onboarding.py` (CliRunner wizard, config precedence,
  `demo` e2e on real ffmpeg). Docs: `docs/wiki/packaging.md` (new),
  `cli_client.md` updated.
