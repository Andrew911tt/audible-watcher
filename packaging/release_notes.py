"""Print one version's section of CHANGELOG.md: the release notes.

    python packaging/release_notes.py 1.2.0 > release-notes.md

Exits with an error if the changelog has no section for that version, so a
release can't go out without notes. Used by the release workflow.
"""
from __future__ import annotations

import os
import sys

CHANGELOG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "CHANGELOG.md")


def section(version: str, text: str | None = None) -> str | None:
    """The notes under "## <version> — <date>", up to the next version, or
    None if there's no such section (or it's empty)."""
    if text is None:
        with open(CHANGELOG, encoding="utf-8") as f:
            text = f.read()
    version = version.lstrip("vV")
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            words = line[3:].split()
            inside = bool(words) and words[0] == version
            continue
        if inside:
            lines.append(line)
    notes = "\n".join(lines).strip()
    return notes or None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    notes = section(sys.argv[1])
    if notes is None:
        print(f"CHANGELOG.md has no section for {sys.argv[1]}",
              file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
