"""The deterministic walk that decides which files are notes."""

from pathlib import Path

_SKIPPED_FILE = "README.md"
_SKIPPED_DIRECTORY_PREFIXES = ("_", ".")


def note_files(root: Path) -> list[Path]:
    """Return every note file under `root`, sorted by POSIX path relative to it.

    A directory whose name starts with `_` or `.` is not entered, and `README.md` is not a note.
    """
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            if entry.is_dir():
                if not entry.name.startswith(_SKIPPED_DIRECTORY_PREFIXES):
                    pending.append(entry)
            elif entry.suffix == ".md" and entry.name != _SKIPPED_FILE:
                found.append(entry)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
