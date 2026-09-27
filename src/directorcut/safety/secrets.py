"""Repo secrets scanner - a lightweight gitleaks substitute.

Scans checked-in-style files for API-key-shaped strings so a real credential
can't land in the repo unnoticed. `.env` files, `.env.example`, and the
`tasks/` scratch area are intentionally skipped: `.env` legitimately holds
the real keys and `tasks/` is throwaway debugging space.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Each pattern targets a recognizable credential shape; keep them specific
# enough that ordinary prose/code can't trip them.
PATTERNS: dict[str, re.Pattern[str]] = {
    "google_api_key": re.compile(r"AIzaSy[0-9A-Za-z_-]{33}"),
    "openai_style_key": re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
    "github_token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr|github_pat)_[0-9A-Za-z_]{20,}"),
    "private_key_block": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
    ),
}

# Directories that never get scanned: VCS/tooling caches, vendored or
# generated trees, runtime input/output dirs, and the tasks/ scratch area
# (explicitly exempt - scratch files may legitimately quote secrets while
# debugging and are not meant to be committed verbatim).
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "output",
    "ingest",
    "smoketest_output",
    "smoketest_ingest",
    "tasks",
}

# .env holds the real keys by design; .env.example only documents variable
# names and is allowed to carry placeholder-shaped values.
SKIP_FILES = {".env", ".env.local", ".env.example"}

# Anything bigger is almost certainly media/binary or generated data.
MAX_FILE_BYTES = 2_000_000


@dataclass(frozen=True)
class SecretFinding:
    """One suspicious match - `match` is already masked, never the raw key."""

    path: Path
    line: int
    pattern: str
    match: str


def _masked(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _skip_file(path: Path) -> bool:
    name = path.name
    # Covers .env, .env.local, .env.production, etc. - any dotenv variant is
    # a legitimate secrets home and out of scope for the scanner.
    return name in SKIP_FILES or name.startswith(".env")


def iter_scannable_files(root: Path):
    """Yield files under `root` that should be scanned."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if _skip_file(path):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def scan_text(text: str, path: Path) -> list[SecretFinding]:
    """Scan decoded text; returns findings with masked matches."""
    findings: list[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                findings.append(
                    SecretFinding(
                        path=path,
                        line=lineno,
                        pattern=name,
                        match=_masked(m.group(0)),
                    )
                )
    return findings


def scan_file(path: Path) -> list[SecretFinding]:
    """Scan a single file; binary/non-UTF-8 files are skipped silently."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(text, path)


def scan_repo(root: Path) -> list[SecretFinding]:
    """Scan every scannable file under `root`."""
    findings: list[SecretFinding] = []
    for path in iter_scannable_files(root):
        findings.extend(scan_file(path))
    return findings
