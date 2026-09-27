"""Typed settings and configuration for DirectorCut-AI."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import ENV_FILE_SENTINEL
from pydantic_settings.sources.types import DotenvType


def _clean_key(value: str | None) -> str:
    """Normalize a configured key: empty string for unset/placeholder values."""
    if not value or value == "your_api_key_here":
        return ""
    return value


def user_config_dir() -> Path:
    """Per-user config directory used by `directorcut setup`.

    Windows: `%APPDATA%/DirectorCut`; POSIX: `$XDG_CONFIG_HOME/directorcut`
    or `~/.config/directorcut`. `DIRECTORCUT_CONFIG_DIR` overrides outright
    (tests, portable installs). Keys must live HERE, never in the repo .env,
    so a packaged/source-less install still has somewhere safe to put them.
    """
    override = os.environ.get("DIRECTORCUT_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "DirectorCut"
        return Path.home() / "AppData" / "Roaming" / "DirectorCut"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "directorcut"


def user_config_path() -> Path:
    """Path of the per-user config.env written by `directorcut setup`."""
    return user_config_dir() / "config.env"


def _dotenv_quote(value: str) -> str:
    """Quote a value for dotenv when it could break `KEY=value` parsing."""
    if any(c in value for c in (" ", "\t", "#", "'", '"')):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def write_user_config(
    values: Mapping[str, str], path: Path | None = None
) -> Path:
    """Write KEY=value lines to the user config file; returns the path.

    Atomic (tmp+rename) like the pipeline's checkpoint writes, and
    chmod 600 where the platform supports it - this file holds API keys.
    Empty values are written as commented `# KEY=` placeholders so the file
    doubles as documentation of what's configurable.
    """
    path = path or user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DirectorCut user configuration - written by `directorcut setup`.",
        "# Precedence: process env > repo .env > this file.",
    ]
    for key, value in values.items():
        if value:
            lines.append(f"{key}={_dotenv_quote(value)}")
        else:
            lines.append(f"# {key}=")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    # Windows ACLs don't map chmod; best-effort only.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def read_user_config(path: Path | None = None) -> dict[str, str]:
    """Existing user-config KEY=value pairs (setup uses them as defaults)."""
    path = path or user_config_path()
    if not path.exists():
        return {}
    return {
        k: v for k, v in dotenv_values(path).items() if isinstance(v, str)
    }


class Settings(BaseSettings):
    """Application settings loaded from environment / .env / user config.

    Precedence (highest first): process env vars > repo `.env` (or the
    `--config` file) > the per-user config.env written by
    `directorcut setup` (`user_config_path()`). pydantic-settings applies
    later `env_file` entries over earlier ones, so the user config sits
    FIRST in the default list and is overridden by a repo .env.
    """

    def __init__(
        self, _env_file: DotenvType | None = ENV_FILE_SENTINEL, **values: Any
    ) -> None:
        if _env_file == ENV_FILE_SENTINEL:
            _env_file = (user_config_path(), ".env")
        super().__init__(_env_file=_env_file, **values)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Without this, fields whose `alias` doesn't happen to equal the
        # field name upper-cased (e.g. `env` -> `DIRECTORCUT_ENV`, not
        # `ENV`) silently ignore field-name kwargs like `Settings(env=...)`
        # - the value quietly falls back to the default instead of raising,
        # which is easy to miss. `populate_by_name=True` lets callers use
        # either the Python field name or the env var alias.
        populate_by_name=True,
    )

    # Key pool (API backend only - the CLI/agy backends carry their own auth):
    # GEMINI_FREE_KEY_MAIN + GEMINI_FREE_KEY are the free-tier keys (used
    # first, in that order), GEMINI_PAID_KEY_MAIN the primary paid key and
    # GEMINI_PAID_KEY the secondary paid key - the safety net once free
    # capacity is exhausted. gemini_api_key stays the backwards-compatible
    # "primary key" field; its alias list leads with GEMINI_PAID_KEY_MAIN
    # so an explicitly configured main key wins.
    # repr=False keeps key values out of repr()/traceback dumps.
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GEMINI_PAID_KEY_MAIN", "GEMINI_API_KEY", "GEMINI_PAID_KEY"
        ),
        repr=False,
    )
    gemini_paid_key_main: str = Field(default="", alias="GEMINI_PAID_KEY_MAIN", repr=False)
    gemini_paid_key: str = Field(default="", alias="GEMINI_PAID_KEY", repr=False)
    gemini_free_key_main: str = Field(
        default="", alias="GEMINI_FREE_KEY_MAIN", repr=False
    )
    gemini_free_key: str = Field(default="", alias="GEMINI_FREE_KEY", repr=False)
    gemini_model: str = Field(default="gemini-3.7-flash", alias="GEMINI_MODEL")
    # Cheap model for high-volume structured classification (Pass 2 visual
    # chunks, asset interpretation). Falls back to gemini_model when unset.
    gemini_model_fast: str | None = Field(default=None, alias="GEMINI_MODEL_FAST")
    # Ordered per-task failover chains, comma-separated model ids. On
    # persistent quota (429) or repeated 5xx the client falls through to the
    # next model; unset -> the single model_for(task) model, as before.
    gemini_model_chain_visual: str | None = Field(
        default=None, alias="GEMINI_MODEL_CHAIN_VISUAL"
    )
    gemini_model_chain_interpret: str | None = Field(
        default=None, alias="GEMINI_MODEL_CHAIN_INTERPRET"
    )
    gemini_model_chain_narrative: str | None = Field(
        default=None, alias="GEMINI_MODEL_CHAIN_NARRATIVE"
    )
    gemini_model_chain_director: str | None = Field(
        default=None, alias="GEMINI_MODEL_CHAIN_DIRECTOR"
    )
    gemini_model_chain_audio: str | None = Field(
        default=None, alias="GEMINI_MODEL_CHAIN_AUDIO"
    )
    # "api" calls the HTTP generateContent endpoint; "cli" shells out to the
    # `gemini` CLI in non-interactive mode; "antigravity" shells out to the
    # `agy` CLI headless mode (both use whatever auth the CLI has).
    gemini_backend: str = Field(default="api", alias="GEMINI_BACKEND")
    # Last-resort failover for the api backend: when every key x model
    # pairing is exhausted, retry the call through this backend instead of
    # failing. "antigravity" is the only meaningful value today (the agy
    # CLI carries its own auth, so it survives API-key outages).
    backend_fallback: str | None = Field(
        default=None, alias="DIRECTORCUT_BACKEND_FALLBACK"
    )
    gemini_cli_bin: str | None = Field(default=None, alias="GEMINI_CLI_BIN")
    # CLI model names don't necessarily match API model ids; only passed as
    # `-m` when explicitly set, otherwise the CLI default is used.
    gemini_cli_model: str | None = Field(default=None, alias="GEMINI_CLI_MODEL")
    antigravity_cli_bin: str | None = Field(default=None, alias="ANTIGRAVITY_CLI_BIN")
    # `agy` model slugs differ from API ids (e.g. "gemini-3.8-flash-medium";
    # see `agy models`); only passed as `--model` when explicitly set.
    antigravity_cli_model: str | None = Field(default=None, alias="ANTIGRAVITY_CLI_MODEL")
    # Reasoning effort passthrough (low|medium|high); low is worth setting for
    # structured classification calls - agentic loops are slow otherwise.
    antigravity_cli_effort: str | None = Field(default=None, alias="ANTIGRAVITY_CLI_EFFORT")
    # Headless `agy` runs default to a 5-minute ceiling; 15m covers the
    # long visual-logging prompts without disabling the guard entirely.
    antigravity_print_timeout: str = Field(default="15m", alias="ANTIGRAVITY_PRINT_TIMEOUT")
    # Headless runs can't prompt for permissions; the agent needs `command`
    # to inspect media files, so auto-approval is on by default. It only
    # applies inside the throwaway media workspace the client creates.
    antigravity_skip_permissions: bool = Field(
        default=True, alias="ANTIGRAVITY_SKIP_PERMISSIONS"
    )
    # Max frames sent per agy call: the agent reads media via tool calls and
    # multi-frame sets are tiled into ONE contact sheet, so a max-capacity
    # Pass-2 chunk (~3400 frames on a 2h proxy) would become an unreadable
    # mega-image. Over the cap, frames are sampled evenly with the stride
    # reported in the prompt so time estimates stay anchored.
    antigravity_media_frame_cap: int = Field(
        default=16, alias="ANTIGRAVITY_MEDIA_FRAME_CAP"
    )
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY", repr=False)
    elevenlabs_music_model: str = Field(default="music_v2", alias="ELEVENLABS_MUSIC_MODEL")
    # Scribe speech-to-text model; scribe_v1 is the broadly-available one.
    elevenlabs_stt_model: str = Field(default="scribe_v1", alias="ELEVENLABS_STT_MODEL")
    # TTS voice for spoken pickup lines (patching a flubbed line without a
    # re-shoot). Empty until a voice is cloned/picked in the ElevenLabs UI.
    elevenlabs_voice_id: str = Field(default="", alias="ELEVENLABS_VOICE_ID")
    # Speech-to-text backend for unscripted footage. "elevenlabs" = Scribe
    # (paid, needs key+network); "whisper" = local faster-whisper (free,
    # offline - optional `local-stt` extra); "auto" picks elevenlabs when a
    # key is configured, whisper otherwise.
    stt_backend: Literal["elevenlabs", "whisper", "auto"] = Field(
        default="auto", alias="DIRECTORCUT_STT_BACKEND"
    )
    # faster-whisper model id/size + CTranslate2 compute type; int8 keeps
    # CPU transcription practical for A-roll-length audio.
    whisper_model: str = Field(default="small", alias="DIRECTORCUT_WHISPER_MODEL")
    whisper_compute_type: str = Field(
        default="int8", alias="DIRECTORCUT_WHISPER_COMPUTE_TYPE"
    )
    ffmpeg_path: str | None = Field(default=None, alias="FFMPEG_PATH")
    ffprobe_path: str | None = Field(default=None, alias="FFPROBE_PATH")
    ingest_dir: Path = Field(default=Path("./ingest"), alias="INGEST_DIR")
    output_dir: Path = Field(default=Path("./output"), alias="OUTPUT_DIR")
    # FCPXML flavour. "resolve" (default) bakes music/SFX beds via the
    # mixdown stage and emits zero <param> keyframes - DaVinci Resolve
    # ignores volume keyframeAnimation entirely (verified 2026-09-22).
    # "fcpx" keeps the per-cut <param name="volume"> ducking, which is
    # legal FCPXML for Final Cut Pro. "premiere" emits the same XML shape
    # as "fcpx" (Premiere Pro imports FCPXML) - its param/transform support
    # is unverified, see docs/wiki/nle_targets.md for the known quirks.
    fcpxml_target: Literal["resolve", "fcpx", "premiere"] = Field(
        default="resolve", alias="DIRECTORCUT_FCPXML_TARGET"
    )
    # Ingest-time A-roll reframe: portrait/rotated or non-16:9 A-roll is
    # cropped+scaled into a flat 1920x1080 h264 file under
    # output/<project>/media/ so Resolve never sees rotation side-data or
    # pillarbox framing (it mis-applies adjust-transform on those -
    # verified 2026-09-22). Set false to keep original media untouched.
    reframe_aroll: bool = Field(default=True, alias="DIRECTORCUT_REFRAME_AROLL")
    # Ingest-time voice normalization: remuxes each A-roll/voiceover with
    # highpass->compand->loudnorm(-16 LUFS/-1.5 dBTP)->aac192k audio
    # (video stream copied, rotation preserved). Files already inside the
    # -18..-15 LUFS band are skipped - double-normalizing hurts.
    normalize_voice: bool = Field(default=False, alias="DIRECTORCUT_NORMALIZE_VOICE")
    # Pass-2 frame sampling density. Frames are the dominant API cost
    # (~350 tokens each at 360p); B-roll relevance picks don't need 1fps -
    # 0.2 (one frame per 5s) is ~5x cheaper and usually loses nothing. The
    # 1fps proxy is unchanged; sampling happens at classify time.
    visual_fps: float = Field(default=1.0, alias="DIRECTORCUT_VISUAL_FPS", gt=0.0, le=1.0)
    # Post-director hard targets (production finding: the director ignored
    # "7-9 minutes / 60% B-roll" until the numbers were fed back). Unset ->
    # targets are parsed out of the direction text instead; these settings
    # take precedence when present. `target_length_s` accepts a range
    # ("420-540", "7:00-9:00", "420 to 540") or a single aim ("480" = +-10%).
    # `broll_coverage_min` is a fraction 0..1 ("60" is read as 60%).
    target_length_s: str | float | None = Field(
        default=None, alias="DIRECTORCUT_TARGET_LENGTH_S"
    )
    broll_coverage_min: float = Field(
        default=0.0, alias="DIRECTORCUT_BROLL_COVERAGE_MIN"
    )
    # Hard run spend cap in USD. Metered calls (Gemini tokens, ElevenLabs
    # minutes) accumulate into output/<project>/costs.json via
    # safety.ledger; once the running total exceeds this the next metered
    # call raises BudgetExceeded and the run aborts with a clear message
    # (v1 = abort, not pause-for-approval). Unset/None/0 disables the cap.
    cost_cap_usd: float | None = Field(
        default=None, alias="DIRECTORCUT_COST_CAP_USD"
    )
    env: str = Field(default="dev", alias="DIRECTORCUT_ENV")
    log_level: str = Field(default="INFO", alias="DIRECTORCUT_LOG_LEVEL")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    @field_validator("ingest_dir", "output_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> object:  # noqa: ANN001
        # Same trap as IngestManifest._coerce_path: leave str for pydantic's
        # own str -> Path coercion so JSON-mode round-trips still validate.
        return value if isinstance(value, (str, Path)) else Path(value)

    @property
    def dev_mode(self) -> bool:
        """Return True when running in development or test mode."""
        return self.env in ("dev", "test", "development")

    @property
    def safe_api_key(self) -> str | None:
        """Return the API key if present, else None."""
        key = self.gemini_api_key
        return key if key and key != "your_api_key_here" else None

    def model_for(
        self,
        task: Literal["visual", "interpret", "narrative", "director", "audio"],
    ) -> str:
        """Return the Gemini model for a pipeline task.

        The high-volume classification passes (Pass 2 visual chunks, asset
        interpretation) use `gemini_model_fast` when configured; everything
        else - and all tasks when it's unset - uses `gemini_model`.
        """
        if task in ("visual", "interpret"):
            return self.gemini_model_fast or self.gemini_model
        return self.gemini_model

    def model_chain(self, task: str) -> list[str]:
        """Return the ordered model fallback chain for a task.

        `GEMINI_MODEL_CHAIN_<TASK>` (comma-separated) defines the full chain;
        when unset the chain is just the single `model_for(task)` model, so
        behaviour is unchanged for existing configs.
        """
        raw = getattr(self, f"gemini_model_chain_{task}", None)
        if raw:
            chain = [m.strip() for m in raw.split(",") if m.strip()]
            if chain:
                return chain
        return [self.model_for(task)]  # type: ignore[arg-type]

    def key_chain(self, task: str) -> list[str]:
        """Return the ordered API-key failover chain for a task.

        Key policy (2026-09-27): free-tier first everywhere - burn
        GEMINI_FREE_KEY_MAIN until it rate-limits, then GEMINI_FREE_KEY as
        the free backup, then paid (MAIN then secondary) only when free
        capacity is gone. Empty and duplicate entries are dropped, so a
        single configured key yields a one-entry chain identical to the
        old single-key behaviour.
        """
        main = _clean_key(self.gemini_paid_key_main) or _clean_key(
            self.gemini_api_key
        )
        paid = _clean_key(self.gemini_paid_key)
        free_main = _clean_key(self.gemini_free_key_main)
        free = _clean_key(self.gemini_free_key)
        order = [free_main, free, main, paid]
        chain: list[str] = []
        for key in order:
            if key and key not in chain:
                chain.append(key)
        return chain

    def configured_secrets(self) -> list[str]:
        """Return every configured credential value, for log redaction."""
        return [
            v
            for v in (
                self.gemini_api_key,
                self.gemini_paid_key_main,
                self.gemini_paid_key,
                self.gemini_free_key_main,
                self.gemini_free_key,
                self.elevenlabs_api_key,
            )
            if v and v != "your_api_key_here"
        ]

    @property
    def safe_elevenlabs_key(self) -> str | None:
        """Return the ElevenLabs API key if present, else None."""
        key = self.elevenlabs_api_key
        return key if key and key != "your_api_key_here" else None

    @property
    def resolved_stt_backend(self) -> Literal["elevenlabs", "whisper"]:
        """Effective STT backend - `auto` = Scribe when keyed, else whisper."""
        if self.stt_backend in ("elevenlabs", "whisper"):
            return self.stt_backend
        return "elevenlabs" if self.safe_elevenlabs_key else "whisper"
