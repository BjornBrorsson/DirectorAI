# Feature: Packaging & First-Run Onboarding

> Status: Draft | Last verified: 2026-09-23

## What it does

Makes DirectorCut installable/runnable for non-developers: a PyInstaller
one-file build, a `directorcut setup` wizard that collects keys + backend
and model preferences into a per-user config file (never the repo `.env`),
and a `directorcut demo` command that runs ingest→FCPXML fully offline on
synthesized fixture media.

## Where the code lives

* `directorcut.spec` (repo root) — PyInstaller 6.x spec; entry
  `src/directorcut/cli.py`, bundles `docs/` into the binary. Build:
  `pip install . && pip install pyinstaller && pyinstaller directorcut.spec`
  → `dist/directorcut.exe`. `collect_submodules("directorcut")` covers the
  CLI's lazy stage imports and uvicorn's `"directorcut.api.main:app"` string
  import; `collect_submodules("uvicorn")` covers its dynamic loop/protocol
  modules.
* `directorcut.cli` `setup` — click prompts for `GEMINI_PAID_KEY_MAIN` /
  `GEMINI_PAID_KEY` / `GEMINI_FREE_KEY` / `ELEVENLABS_API_KEY` (hidden
  input, never echoed), `GEMINI_BACKEND`, `GEMINI_MODEL`,
  `GEMINI_MODEL_FAST`, `DIRECTORCUT_STT_BACKEND` (+`DIRECTORCUT_WHISPER_MODEL`
  unless `elevenlabs`), `DIRECTORCUT_ENV` (defaults `prod` once a Gemini key
  exists - dev mode stubs ignore keys). Blank input keeps the existing
  user-config value on re-runs (`src/directorcut/cli.py`).
* `directorcut.cli` `demo` (`--workdir`, `-p/--project`) — synthesizes a
  ~5s `testsrc2`+`sine` mp4 + `script.txt` via `ffmpeg -f lavfi`, runs
  `run_pipeline` with a dev-mode `Settings(_env_file=None)` (all model/API
  calls stubbed, ambient keys untouched), prints the FCPXML path
  (`src/directorcut/demo.py`).
* `directorcut.config.user_config_dir()/user_config_path()` — Windows:
  `%APPDATA%/DirectorCut/config.env`; POSIX: `$XDG_CONFIG_HOME/directorcut`
  or `~/.config/directorcut`; `DIRECTORCUT_CONFIG_DIR` overrides.
* `directorcut.config.write_user_config()/read_user_config()` — atomic
  tmp+rename write, chmod 600 best-effort, empty values become commented
  `# KEY=` placeholders.

## Keys & config

* Settings precedence (highest→lowest): **process env > repo `.env` /
  `--config` file > user config.env**. Implemented by `Settings.__init__`
  defaulting `_env_file` to `(user_config_path(), ".env")` -
  pydantic-settings applies later files over earlier ones. `--config` or
  `_env_file=None` opts out entirely (tests rely on the latter).
* ffmpeg/ffprobe are NEVER bundled - they must be on `PATH` (or
  `FFMPEG_PATH`/`FFPROBE_PATH`); `directorcut.ffmpeg._find_binary` also
  checks common install dirs.

## Gotchas

* **Keys live in the user config dir, not the repo.** The setup wizard only
  writes `config.env` under `user_config_dir()`; the secrets-scan test
  (`tests/test_secrets_hygiene.py`) would flag a committed `.env` anyway.
* **faster-whisper is excluded from the binary** (`excludes` in the spec):
  ctranslate2/onnxruntime/av add ~1GB. The frozen CLI supports the
  `elevenlabs` STT backend; for a whisper-capable binary remove the excludes
  and rebuild after `pip install faster-whisper`.
* **`Settings(_env_file=None)` + sibling key kwargs raise KeyError** -
  pydantic-settings 2.15 alias-restore quirk when `gemini_api_key`
  (AliasChoices contains `GEMINI_PAID_KEY_MAIN`) is combined with the
  `gemini_*_key*` field-name kwargs. Don't pass them together (see
  `demo.py` comment).
* Binary-build status: see the ticket Work Log for whether the PyInstaller
  output was verified on this machine.
