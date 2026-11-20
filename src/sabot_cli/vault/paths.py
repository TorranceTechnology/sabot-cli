"""Where a path lies relative to a vault, judged by the file system rather than by spelling."""

import os
from pathlib import Path, PurePosixPath

# Windows refuses these names as a file name with any extension, so `con.x.md` cannot be
# checked out there; Git for Windows turns such a path away. The superscript digits count as
# the ASCII ones, and `conin$` and `conout$` are console handles.
_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"com{n}" for n in (*range(1, 10), "\u00b9", "\u00b2", "\u00b3")),
        *(f"lpt{n}" for n in (*range(1, 10), "\u00b9", "\u00b2", "\u00b3")),
    }
)
_SKIPPED_DIRECTORIES = frozenset({".git"})


def reserved_device_name(name: str) -> bool:
    """Whether the part of `name` before its first dot is a Windows device name, in any case.

    Trailing spaces on that part are ignored, since Windows removes them (`con .md`).
    """
    return name.split(".", 1)[0].rstrip(" ").lower() in _DEVICE_NAMES


def windows_unusable_name(name: str) -> str | None:
    """Why Windows cannot hold a file or directory called `name`, or None when it can."""
    if reserved_device_name(name):
        return "starts with a name Windows reserves for a device"
    if name.endswith((".", " ")):
        return "ends with a dot or a space, which Windows removes"
    return None


def windows_unusable_entries(root: Path) -> tuple[tuple[str, str, bool, str], ...]:
    """Every file and directory below `root` whose name Windows cannot hold, sorted by path.

    Each entry is (relative POSIX path, name, is a directory, reason). The `.git` directory is
    not entered, since git owns what is in it.
    """
    found: list[tuple[str, str, bool, str]] = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in _SKIPPED_DIRECTORIES]
        for names, is_directory in ((subdirectories, True), (files, False)):
            for name in names:
                reason = windows_unusable_name(name)
                if reason is not None:
                    relative = (Path(directory) / name).relative_to(root).as_posix()
                    found.append((relative, name, is_directory, reason))
    return tuple(sorted(found))


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
