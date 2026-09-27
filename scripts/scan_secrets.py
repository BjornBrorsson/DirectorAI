"""Secrets scan for CI / pre-commit: exits 1 on any committed-looking secret.

Run: .venv/Scripts/python.exe scripts/scan_secrets.py

`.env`, `.env.example`, and `tasks/` scratch are exempt by design; findings
print path:line with a masked match - never the raw key.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from directorcut.safety.secrets import scan_repo  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan_repo(root)
    for f in findings:
        print(f"{f.path.relative_to(root)}:{f.line}: {f.pattern}: {f.match}")
    if findings:
        print(f"\n{len(findings)} possible secret(s) found - remove or rotate them.")
        return 1
    print("No committed secrets found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
