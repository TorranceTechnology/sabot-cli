"""The deterministic walk that decides which files are notes."""

from collections.abc import Iterable
from pathlib import Path

_SKIPPED_FILE = "README.md"
_SKIPPED_DIRECTORY_PREFIXES = ("_", ".")
_HIDDEN_PREFIX = "."


def directory_entries(directory: Path) -> Iterable[Path]:
    """The entries of one directory in whatever order the file system lists them.

    The walk reads directories only through this function, so a test can substitute another
    order and check that nothing downstream depends on it.
    """
    return directory.iterdir()


def note_files(root: Path) -> list[Path]:
    """Return every note file under `root`, sorted by POSIX path relative to it.

    A directory whose name starts with `_` or `.` is not entered, a file whose name starts with
    `.` is not a note (a transaction's staged files are hidden for this reason), and `README.md`
    is not a note.
    """
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory_entries(directory):
            if entry.is_dir():
                if not entry.name.startswith(_SKIPPED_DIRECTORY_PREFIXES):
                    pending.append(entry)
            elif (
                entry.suffix == ".md"
                and entry.name != _SKIPPED_FILE
                and not entry.name.startswith(_HIDDEN_PREFIX)
            ):
                found.append(entry)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
