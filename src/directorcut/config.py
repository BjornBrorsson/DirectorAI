"""Typed settings and configuration for DirectorCut-AI."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env."""

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

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.7-flash", alias="GEMINI_MODEL")
    ffmpeg_path: str | None = Field(default=None, alias="FFMPEG_PATH")
    ffprobe_path: str | None = Field(default=None, alias="FFPROBE_PATH")
    ingest_dir: Path = Field(default=Path("./ingest"), alias="INGEST_DIR")
    output_dir: Path = Field(default=Path("./output"), alias="OUTPUT_DIR")
    env: str = Field(default="dev", alias="DIRECTORCUT_ENV")
    log_level: str = Field(default="INFO", alias="DIRECTORCUT_LOG_LEVEL")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    @field_validator("ingest_dir", "output_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:  # noqa: ANN001
        return Path(value) if not isinstance(value, Path) else value

    @property
    def dev_mode(self) -> bool:
        """Return True when running in development or test mode."""
        return self.env in ("dev", "test", "development")

    @property
    def safe_api_key(self) -> str | None:
        """Return the API key if present, else None."""
        key = self.gemini_api_key
        return key if key and key != "your_api_key_here" else None
