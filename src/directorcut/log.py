"""Structured logging setup, with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"

# Key-shaped strings get masked even when nobody registered the exact value -
# e.g. httpx errors embed the request URL, and the Gemini endpoint carries the
# key as `?key=...`, so a bare exception str() would otherwise leak it.
_KEY_PATTERNS = (
    re.compile(r"AIzaSy[0-9A-Za-z_-]{33}"),  # Google API keys
    re.compile(r"sk-[0-9A-Za-z_-]{20,}"),  # OpenAI-style "sk-..." keys
)

_registered_secrets: set[str] = set()


def _mask(value: str) -> str:
    """Mask a secret while keeping enough edge characters to identify it."""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def register_secret(value: str | None) -> None:
    """Mark a configured secret so `redact()` masks it wherever it appears.

    Values under 8 chars are ignored - masking a short common string would
    mangle unrelated log lines for no real secrecy gain.
    """
    if value and len(value) >= 8 and value != "your_api_key_here":
        _registered_secrets.add(value)


def register_secrets(values: Iterable[str | None]) -> None:
    """Register several secret values at once."""
    for value in values:
        register_secret(value)


def redact(text: str) -> str:
    """Mask registered secrets and key-shaped strings in `text`."""
    for secret in _registered_secrets:
        if secret in text:
            text = text.replace(secret, _mask(secret))
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(lambda m: _mask(m.group(0)), text)
    return text


class _RedactFilter(logging.Filter):
    """Mutate a record's msg/args in place so the key never reaches a handler.

    Formatter-level redaction alone can't cover pytest's caplog (or any
    third-party handler): filters on the *originating* logger run before the
    record propagates to ancestor handlers, so masking here protects every
    sink. Traceback text (exc_text) is still redacted at format time by the
    `_Formatter` installed in `setup_logging`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger with the redaction filter attached."""
    logger = logging.getLogger(name)
    if not getattr(logger, "_directorcut_redact", False):
        logger.addFilter(_RedactFilter())
        logger._directorcut_redact = True  # type: ignore[attr-defined]
    return logger


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    secrets: Iterable[str | None] = (),
) -> None:
    """Configure root console + optional file logging.

    `secrets` are registered with the redaction filter so configured API keys
    can never reach a log line (they show up masked as `abcd***wxyz`).
    """
    register_secrets(secrets)

    root = logging.getLogger("directorcut")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    class _Formatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):  # noqa: N802
            dt = datetime.fromtimestamp(record.created, tz=UTC)
            return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

        def format(self, record):
            # Redact the fully rendered line - that covers the message, the
            # %-args interpolation, and any attached exception traceback.
            return redact(super().format(record))

    formatter = _Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
