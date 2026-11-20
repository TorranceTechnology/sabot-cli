"""The journal of a write, the rules for what it may name, and the undoing of it.

A transaction writes a journal before it changes any file, and `recover` acts on a journal a
killed run left behind. A vault can arrive from elsewhere, version control included, so a
journal found in one is not trusted: it is checked in full before anything is touched, and each
undo is conditional on what the file holds now.

A journal may name only plain, non-hidden paths inside the vault, with no link on the way. For
each operation, undoing it changes a file only in these cases:

- `create`: the file's SHA-256 is the one journaled, so it is removed; an absent file needs
  nothing.
- `replace`: the file holds the bytes the write put there, so the backup is restored; a file
  that already holds the backup's bytes needs nothing.
- `delete`: the file is absent, so the backup is restored; a file that already holds the
  backup's bytes needs nothing.

Anything else is unverifiable, and one unverifiable operation stops the whole recovery before
any file changes.
"""

import contextlib
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, Self, cast

__all__ = [
    "BACKUP_DIRECTORY",
    "JOURNAL_FILE",
    "JOURNAL_PREFIX",
    "STAGED_PREFIX",
    "Journal",
    "Operation",
    "TransactionError",
    "UndoPlan",
    "is_vault_path",
    "journal_refusal",
    "plan_undo",
    "run_undo",
    "staged_name",
    "sync_directory",
    "write_synced",
]

JOURNAL_PREFIX = ".sabot-txn-"
STAGED_PREFIX = ".sabot-new-"
JOURNAL_FILE = "journal.json"
BACKUP_DIRECTORY = "backup"
_JOURNAL_FORMAT = 2
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{12}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_BACKUP_PATTERN = re.compile(rf"{BACKUP_DIRECTORY}/[0-9]+")
_SHORT_NAME = re.compile(r"~\d")
_MAX_MODE = 0o7777

type Kind = Literal["create", "replace", "delete"]


class TransactionError(Exception):
    """A write was refused or undone; `code` is the refusal's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Operation:
    """One file a write creates, replaces or deletes, as the journal records it."""

    kind: Kind
    path: str
    sha256: str
    staged: str
    backup: str | None
    mode: int | None


@dataclass(frozen=True)
class Journal:
    """The ordered operations of one write, the directories it created and where it ran."""

    token: str
    host: str
    operations: tuple[Operation, ...]
    directories: tuple[str, ...]

    def to_json(self) -> str:
        """The journal as the text of `journal.json`."""
        document = {
            "directories": list(self.directories),
            "format": _JOURNAL_FORMAT,
            "host": self.host,
            "operations": [
                {
                    "backup": op.backup,
                    "kind": op.kind,
                    "mode": op.mode,
                    "path": op.path,
                    "sha256": op.sha256,
                    "staged": op.staged,
                }
                for op in self.operations
            ],
            "token": self.token,
        }
        return json.dumps(document, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str, *, expected_token: str) -> Self:
        """Parse and validate a journal; a journal that names anything else is refused.

        `recover` acts on what a journal says, and a vault may arrive from elsewhere, so every
        path is checked to be a plain, non-hidden path inside the vault in the shape this module
        writes.
        """
        try:
            document = json.loads(text)
            if document["format"] != _JOURNAL_FORMAT:
                raise _refusal("it is of a format this version does not read")
            operations = tuple(
                Operation(
                    kind=cast(Kind, _string(item["kind"])),
                    path=_string(item["path"]),
                    sha256=_string(item["sha256"]),
                    staged=_string(item["staged"]),
                    backup=None if item["backup"] is None else _string(item["backup"]),
                    mode=None if item["mode"] is None else _integer(item["mode"]),
                )
                for item in document["operations"]
            )
            directories = tuple(_string(name) for name in document["directories"])
            journal = cls(
                _string(document["token"]), _string(document["host"]), operations, directories
            )
        except (ValueError, KeyError, TypeError) as error:
            raise TransactionError("journal-invalid", "a journal cannot be read") from error
        journal._validate(expected_token)
        return journal

    def _validate(self, expected_token: str) -> None:
        if self.token != expected_token or not _TOKEN_PATTERN.fullmatch(self.token):
            raise _refusal("its token does not match its directory")
        if not self.host:
            raise _refusal("it names no host")
        for name in self.directories:
            if not is_vault_path(name):
                raise _refusal(f"it names a directory it may not ({name!r})")
        paths = [operation.path for operation in self.operations]
        if len(set(paths)) != len(paths):
            raise _refusal("it names one file twice")
        for operation in self.operations:
            self._validate_operation(operation)

    def _validate_operation(self, operation: Operation) -> None:
        if not is_vault_path(operation.path):
            raise _refusal(f"it names a path it may not ({operation.path!r})")
        if operation.staged != staged_relative(self.token, operation.path):
            raise _refusal(f"it names a staged file that is not a copy of {operation.path}")
        if operation.kind not in ("create", "replace", "delete"):
            raise _refusal(f"it names an unknown operation ({operation.kind!r})")
        if not _DIGEST_PATTERN.fullmatch(operation.sha256):
            raise _refusal("it holds a malformed digest")
        if operation.kind == "create":
            wrong_backup = operation.backup is not None or operation.mode is not None
        else:  # replace and delete both put a backup, with its mode, back
            wrong_backup = (
                operation.backup is None
                or not _BACKUP_PATTERN.fullmatch(operation.backup)
                or operation.mode is None
                or not 0 <= operation.mode <= _MAX_MODE
            )
        if wrong_backup:
            raise _refusal(f"it names a backup it does not hold ({operation.backup!r})")


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("not a string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("not an integer")
    return value


def _refusal(reason: str) -> TransactionError:
    return TransactionError("journal-invalid", f"a journal is refused: {reason}")


def journal_refusal(reason: str) -> TransactionError:
    """The `journal-invalid` refusal, worded for `reason`."""
    return _refusal(reason)


def staged_relative(token: str, path: str) -> str:
    """The relative path of the hidden copy a write stages beside `path`."""
    target = PurePosixPath(path)
    return target.with_name(f"{STAGED_PREFIX}{token}-{target.name}").as_posix()


def staged_name(token: str, target: Path) -> Path:
    """The hidden copy a write stages beside `target`."""
    return target.with_name(f"{STAGED_PREFIX}{token}-{target.name}")


def is_vault_path(relative: str, *, windows: bool | None = None) -> bool:
    """Whether `relative` is a non-empty relative POSIX path of plain, non-hidden names.

    A name that starts with a dot is refused: it is where a version-control directory, an
    editor's settings and this tool's own files live, and no write names one. With `windows`
    (by default, on Windows) a name that ends in a dot or a space, or that has the `~1` form of
    a short name, is refused as well, since Windows reads each as another file.
    """
    check_windows = sys.platform == "win32" if windows is None else windows
    if not relative or relative.startswith("/") or "\\" in relative or ":" in relative:
        return False
    for part in relative.split("/"):
        if part in ("", ".", "..") or part.startswith("."):
            return False
        if check_windows and (part.endswith((".", " ")) or _SHORT_NAME.search(part)):
            return False
    return True


def sync_directory(path: Path) -> None:
    """Flush a directory's entries to disk where the platform allows it; elsewhere a no-op."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_synced(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    """Write `data` to `path` and flush it to disk; with `exclusive`, refuse an existing path."""
    with path.open("xb" if exclusive else "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


class _Unverifiable(Exception):
    """An operation cannot be undone because what it names is not what the journal says."""


@dataclass(frozen=True)
class _Step:
    relative: str
    target: Path
    staged: Path
    data: bytes | None  # None removes the target; bytes restore it
    mode: int


@dataclass
class UndoPlan:
    """What undoing a journal would change, worked out before anything is changed."""

    steps: list[_Step] = field(default_factory=list)
    staged: list[Path] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def plan_undo(root: Path, journal_directory: Path, journal: Journal) -> UndoPlan:
    """Decide, without changing anything, how to undo `journal`, and what cannot be undone."""
    plan = UndoPlan()
    for operation in reversed(journal.operations):
        try:
            _plan_operation(root, journal_directory, operation, plan)
        except _Unverifiable as reason:
            plan.problems.append(f"{operation.path}: {reason}")
    for name in reversed(journal.directories):
        if _link_free(root, name):
            plan.directories.append(root / name)
        else:
            plan.problems.append(f"{name}: lies outside the vault through a link")
    return plan


def run_undo(plan: UndoPlan) -> list[str]:
    """Carry out a plan; return what failed. The plan's own problems are not repeated."""
    problems: list[str] = []
    for step in plan.steps:
        try:
            if step.data is None:
                _unlink_if_present(step.target)
            else:
                _restore(step, step.data)
        except OSError as error:
            problems.append(f"{step.relative}: {error.strerror}")
    for staged in plan.staged:
        try:
            _unlink_if_present(staged)
        except OSError as error:
            problems.append(f"{staged.name}: {error.strerror}")
    for directory in plan.directories:
        with contextlib.suppress(OSError):
            directory.rmdir()
    return problems


def _plan_operation(
    root: Path, journal_directory: Path, operation: Operation, plan: UndoPlan
) -> None:
    if not _link_free(root, operation.path):
        raise _Unverifiable("it lies outside the vault through a link")
    target = root / operation.path
    current = _read_regular(target)
    if operation.kind == "create":
        if current is None:
            pass
        elif hashlib.sha256(current).hexdigest() == operation.sha256:
            plan.steps.append(_Step(operation.path, target, root / operation.staged, None, 0))
        else:
            raise _Unverifiable("it is not the file the write created")
    else:
        backup = _read_backup(journal_directory, operation)
        recorded = operation.mode or 0
        if current is not None and current == backup:
            pass
        elif operation.kind == "replace":
            if current is None:
                raise _Unverifiable("the file the write replaced is gone")
            if hashlib.sha256(current).hexdigest() != operation.sha256:
                raise _Unverifiable("it holds neither what the write wrote nor what it replaced")
            # Execute bits come back only for a file that is executable now, so a journal cannot
            # make a file executable; every other bit is the recorded one, minus set-id bits.
            held = stat.S_IMODE(target.lstat().st_mode)
            mode = (recorded & 0o666) | (recorded & held & 0o111)
            plan.steps.append(_Step(operation.path, target, root / operation.staged, backup, mode))
        else:
            if current is not None:
                raise _Unverifiable("it holds neither the file the write deleted nor nothing")
            if not target.parent.is_dir():
                raise _Unverifiable("its directory is gone")
            plan.steps.append(
                _Step(operation.path, target, root / operation.staged, backup, recorded & 0o666)
            )
    plan.staged.append(root / operation.staged)


def _read_regular(path: Path) -> bytes | None:
    """The bytes of a regular file, None when nothing is there; anything else is unverifiable."""
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise _Unverifiable(error.strerror or "it cannot be read") from error
    if not stat.S_ISREG(info.st_mode):
        raise _Unverifiable("it is not a regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise _Unverifiable(error.strerror or "it cannot be read") from error


def _read_backup(journal_directory: Path, operation: Operation) -> bytes:
    if operation.backup is None:
        raise _Unverifiable("its backup is missing")
    if (journal_directory / BACKUP_DIRECTORY).is_symlink():
        raise _Unverifiable("its backup directory is a link")
    data = _read_regular(journal_directory / operation.backup)
    if data is None:
        raise _Unverifiable("its backup is missing")
    return data


def _unlink_if_present(path: Path) -> None:
    """Remove a file; one that is not there, or sits below a file, needs nothing."""
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        path.unlink()


def _restore(step: _Step, data: bytes) -> None:
    # The staged copy of a replaced file inherited the target's mode, which may forbid writing
    # it again, so it is removed and made afresh.
    _unlink_if_present(step.staged)
    write_synced(step.staged, data, exclusive=True)
    os.chmod(step.staged, step.mode)
    os.replace(step.staged, step.target)


def _link_free(root: Path, relative: str) -> bool:
    """Whether every existing part of `relative` below `root` is a real file or directory.

    A link (or, on Windows, a junction) on the way would let a write land somewhere the journal
    does not name. The comparison is by what the file system resolves, not by spelling.
    """
    real_root = os.path.realpath(root)
    parts = PurePosixPath(relative).parts
    current = root
    for depth, part in enumerate(parts, start=1):
        current = current / part
        try:
            info = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return True
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode):
            return False
        expected = os.path.join(real_root, *parts[:depth])
        if os.path.normcase(os.path.realpath(current)) != os.path.normcase(expected):
            return False
    return True
