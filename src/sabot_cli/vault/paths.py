"""Where a path lies relative to a vault, judged by the file system rather than by spelling."""

import os
from pathlib import Path, PurePosixPath

# Windows refuses these names as a file name with any extension, so `con.x.md` cannot be
# checked out there; Git for Windows turns such a path away.
_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{n}" for n in range(1, 10)),
        *(f"lpt{n}" for n in range(1, 10)),
    }
)


def reserved_device_name(name: str) -> bool:
    """Whether the part of `name` before its first dot is a Windows device name, in any case."""
    return name.split(".", 1)[0].lower() in _DEVICE_NAMES


def relative_within(path: Path, root: Path) -> PurePosixPath | None:
    """`path` relative to `root`, or None when it lies outside.

    The nearest existing ancestor of `path` is compared with `root` by identity
    (`os.path.samefile`), so a path that spells the vault in another letter case lies inside it
    on a case-insensitive file system, and a link into the vault does too. The part below that
    ancestor is returned as typed. `path` equal to `root` gives an empty path.
    """
    target = path.resolve()
    vault = root.resolve()
    tail: list[str] = []
    current = target
    while True:
        if _same(current, vault):
            return PurePosixPath(*reversed(tail))
        if current.parent == current:
            return None
        tail.append(current.name)
        current = current.parent


def display_path(path: Path, root: Path) -> str:
    """Name a file without an absolute path: vault-relative when inside, else its file name."""
    relative = relative_within(path, root)
    return path.name if relative is None else relative.as_posix()


def _same(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
