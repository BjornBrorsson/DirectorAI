"""Bulk update ticket statuses for the markdown board."""

from pathlib import Path
import re


def main():
    tasks = Path("tasks")
    for p in tasks.glob("*.md"):
        if p.name == "BOARD.md":
            continue
        text = p.read_text(encoding="utf-8")
        text = re.sub(r"> \*\*Status:\*\* Open", "> **Status:** Done", text)
        p.write_text(text, encoding="utf-8")

    # Resolve smoke test remains open until manually verified.
    fcp24 = tasks / "FCP-024.md"
    text = fcp24.read_text(encoding="utf-8").replace(
        "> **Status:** Done", "> **Status:** Open"
    )
    fcp24.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
