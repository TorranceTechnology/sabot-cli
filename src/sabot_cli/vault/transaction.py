"""All-or-nothing writes into a vault, and the recovery of a write that was killed.

Every command that writes into a vault goes through `Transaction`; none writes a vault file
another way. A transaction computes every output in memory first, then:

1. records a journal directory `.sabot-txn-<token>/` at the vault root, holding a backup of
   every file that will be replaced or deleted and `journal.json`, the ordered list of operations;
2. creates any missing directories and stages each output as a hidden sibling of its target,
   `.sabot-new-<token>-<name>`, flushed to disk;
3. applies the operations with `os.replace`, the registration file `_ruleset.yaml` last;
4. removes `journal.json`, which is the commit point, and then the rest of the journal.

An exception, `KeyboardInterrupt` or a termination signal the platform lets Python catch undoes
the applied operations in reverse and removes every staged file, so the vault's bytes are what
they were before the run. A process killed outright leaves the journal behind; every command
then refuses with `transaction-incomplete` until `recover` rolls the journal back. Recovery never
rolls forward.

A transaction creates, replaces or deletes files; a deleted file is backed up like a replaced
one and put back by a rollback. `recover` validates every path a journal names before it
touches anything, since a vault can arrive from elsewhere.

A lock file created with exclusive-create makes a second writer refuse with `vault-locked`. It
records the writer's process id and host name, so that a reader can tell a write in progress
from one that was killed. Two things are out of scope: concurrent writers that bypass this
module, and power loss on a file system that does not order a rename against the directory
reaching disk.
"""

import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import FrameType, TracebackType
from typing import Literal, Self, cast

from sabot_cli.vault.paths import relative_within
from sabot_cli.vault.text import TextFileError, read_text

__all__ = [
    "LOCK_FILE",
    "WRITE_IN_PROGRESS",
    "Checkpoint",
    "HardStop",
    "LockState",
    "Recovery",
    "Transaction",
    "TransactionError",
    "atomic_write",
    "checkpoint_hook",
    "incomplete_work",
    "is_vault_path",
    "lock_state",
    "pending_journals",
    "recover",
    "write_in_progress",
]

LOCK_FILE = ".sabot-lock"
JOURNAL_PREFIX = ".sabot-txn-"
STAGED_PREFIX = ".sabot-new-"
_JOURNAL_FILE = "journal.json"
_BACKUP_DIRECTORY = "backup"
# The file that registers a vault; applied last so a vault never names a source whose files
# are absent.
_REGISTRATION_FILE = "_ruleset.yaml"
_TOKEN_LENGTH = 12
WRITE_IN_PROGRESS = "a write is in progress in this vault; try again when it has finished"
_TERMINATION_SIGNALS = ("SIGTERM", "SIGHUP", "SIGBREAK")
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{12}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_BACKUP_PATTERN = re.compile(rf"{_BACKUP_DIRECTORY}/[0-9]+")
_REMOVAL_ATTEMPTS = 5
_REMOVAL_PAUSE_SECONDS = 0.05

type Checkpoint = Callable[[str, int], None]
type _Kind = Literal["create", "replace", "delete"]
type _Handler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None


class TransactionError(Exception):
    """A write was refused or undone; `code` is the refusal's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class HardStop(BaseException):
    """Raised by a checkpoint hook to model a process killed outright.

    Nothing is rolled back and neither the lock nor the journal is removed, so a test can
    inspect and recover the state a real kill would leave.
    """


class _Terminated(BaseException):
    """A termination signal arrived; derived from `BaseException` so no handler swallows it."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class _Operation:
    kind: _Kind
    path: str
    sha256: str
    staged: str
    backup: str | None


@dataclass(frozen=True)
class _Journal:
    token: str
    operations: tuple[_Operation, ...]
    directories: tuple[str, ...]

    def to_json(self) -> str:
        document = {
            "directories": list(self.directories),
            "format": 1,
            "operations": [
                {
                    "backup": op.backup,
                    "kind": op.kind,
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
        path is checked to lie inside the vault in the shape this module writes.
        """
        try:
            document = json.loads(text)
            operations = tuple(
                _Operation(
                    kind=cast(_Kind, _string(item["kind"])),
                    path=_string(item["path"]),
                    sha256=_string(item["sha256"]),
                    staged=_string(item["staged"]),
                    backup=None if item["backup"] is None else _string(item["backup"]),
                )
                for item in document["operations"]
            )
            directories = tuple(_string(name) for name in document["directories"])
            journal = cls(_string(document["token"]), operations, directories)
        except (ValueError, KeyError, TypeError) as error:
            raise TransactionError("journal-invalid", "a journal cannot be read") from error
        journal._validate(expected_token)
        return journal

    def _validate(self, expected_token: str) -> None:
        if self.token != expected_token or not _TOKEN_PATTERN.fullmatch(self.token):
            raise _journal_refusal("its token does not match its directory")
        for name in self.directories:
            if not is_vault_path(name):
                raise _journal_refusal(f"it names a directory outside the vault ({name!r})")
        for operation in self.operations:
            self._validate_operation(operation)

    def _validate_operation(self, operation: _Operation) -> None:
        if not is_vault_path(operation.path):
            raise _journal_refusal(f"it names a path outside the vault ({operation.path!r})")
        target = PurePosixPath(operation.path)
        expected = target.with_name(f"{STAGED_PREFIX}{self.token}-{target.name}")
        if operation.staged != expected.as_posix():
            raise _journal_refusal(f"it names a staged file that is not a copy of {operation.path}")
        if operation.kind not in ("create", "replace", "delete"):
            raise _journal_refusal(f"it names an unknown operation ({operation.kind!r})")
        if not _DIGEST_PATTERN.fullmatch(operation.sha256):
            raise _journal_refusal("it holds a malformed digest")
        if operation.kind == "create":
            wrong_backup = operation.backup is not None
        else:  # replace and delete both put a backup back
            wrong_backup = operation.backup is None or not _BACKUP_PATTERN.fullmatch(
                operation.backup
            )
        if wrong_backup:
            raise _journal_refusal(f"it names a backup it does not hold ({operation.backup!r})")


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("not a string")
    return value


def _journal_refusal(reason: str) -> TransactionError:
    return TransactionError("journal-invalid", f"a journal is refused: {reason}")


def is_vault_path(relative: str) -> bool:
    """Whether `relative` is a non-empty relative POSIX path of plain names inside a vault."""
    path = PurePosixPath(relative)
    return not (
        not relative
        or path.is_absolute()
        or "\\" in relative
        or ":" in relative
        or any(part in ("", ".", "..") or part.startswith(".sabot-") for part in path.parts)
    )


@dataclass(frozen=True)
class Recovery:
    """What `recover` did, by name and relative path, sorted."""

    rolled_back: tuple[str, ...]
    removed_staged: tuple[str, ...]
    cleared_lock: bool


_checkpoint: Checkpoint | None = None


@contextlib.contextmanager
def checkpoint_hook(hook: Checkpoint) -> Iterator[None]:
    """Run transactions in the block with `hook(phase, index)` called at each step.

    The phases are `journaled` (once, index 0), `staged` (after each staged file) and
    `applied` (after each applied operation); an index counts from 0. A hook that raises
    models a failure at that step; one that raises `HardStop` models a kill. Production code
    never installs a hook.
    """
    global _checkpoint
    previous, _checkpoint = _checkpoint, hook
    try:
        yield
    finally:
        _checkpoint = previous


def _notify(phase: str, index: int) -> None:
    if _checkpoint is not None:
        _checkpoint(phase, index)


def _sync_directory(path: Path) -> None:
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


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _token_of(entries: list[tuple[str, str]]) -> str:
    encoded = json.dumps(entries, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:_TOKEN_LENGTH]


def _staged_name(token: str, target: Path) -> Path:
    return target.with_name(f"{STAGED_PREFIX}{token}-{target.name}")


def atomic_write(path: Path, data: bytes) -> None:
    """Write one file outside any vault by staging a hidden sibling and renaming it over `path`.

    Missing parent directories are created. The staged file is removed on failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = _staged_name(_token_of([(path.name, hashlib.sha256(data).hexdigest())]), path)
    try:
        _write_synced(staged, data)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def pending_journals(root: Path) -> tuple[str, ...]:
    """Names of the journal directories an unfinished transaction left at the vault root.

    A journal directory without its `journal.json` is not listed: removing that file is the
    commit point, so such a directory is the remains of a write that finished (or one that had
    changed nothing yet), and the next writer or `recover` removes it.
    """
    return tuple(name for name in _journal_directories(root) if _has_record(root / name))


def _journal_directories(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in root.iterdir()
            if entry.name.startswith(JOURNAL_PREFIX) and entry.is_dir()
        )
    )


def _has_record(journal_directory: Path) -> bool:
    return (journal_directory / _JOURNAL_FILE).is_file()


def _staged_files(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            name
            for name in subdirectories
            if name != ".git" and not name.startswith(JOURNAL_PREFIX)
        ]
        found += [
            (Path(directory) / name).relative_to(root).as_posix()
            for name in files
            if name.startswith(STAGED_PREFIX)
        ]
    return tuple(sorted(found))


def incomplete_work(root: Path) -> tuple[str, ...]:
    """Relative paths of leftover journals and staged files; empty for a settled vault."""
    if not root.is_dir():
        return ()
    return tuple(sorted((*pending_journals(root), *_staged_files(root))))


type LockState = Literal["none", "live", "stale", "other"]


def lock_state(root: Path) -> LockState:
    """What the write lock of a vault says about whoever holds it.

    `none`: no lock. `live`: the process that wrote it is running on this host. `stale`: that
    process is gone. `other`: it was written on another host or cannot be read, so whether its
    writer is running cannot be told.
    """
    try:
        text = (root / LOCK_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "none"
    except (OSError, UnicodeDecodeError):
        return "other"
    try:
        document = json.loads(text)
        pid, host = document["pid"], document["host"]
    except (ValueError, KeyError, TypeError):
        return "other"
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or host != _host_name():
        return "other"
    return "live" if _process_is_alive(pid) else "stale"


def write_in_progress(root: Path) -> bool:
    """Whether a sabot process on this host is writing the vault right now."""
    return lock_state(root) == "live"


def _host_name() -> str:
    return socket.gethostname()


def _process_is_alive(pid: int) -> bool:
    """Whether process `pid` exists on this host; True when that cannot be told."""
    if sys.platform == "win32":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # The process exists but belongs to someone else, or the platform declines to say.
        return True
    return True


def _windows_process_is_alive(pid: int) -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    # `os.kill(pid, 0)` is not a probe on Windows: it terminates the process.
    still_active, invalid_parameter, query_limited = 259, 87, 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(query_limited, 0, pid)
    if not handle:
        return ctypes.get_last_error() != invalid_parameter
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _undo(root: Path, journal: _Journal, journal_directory: Path) -> list[str]:
    """Undo a journal's operations in reverse and remove the journal; return what failed."""
    problems: list[str] = []
    for operation in reversed(journal.operations):
        target, staged = root / operation.path, root / operation.staged
        try:
            if relative_within(target.parent, root) is None:
                problems.append(f"{operation.path}: lies outside the vault through a link")
                continue
            if operation.kind == "create":
                target.unlink(missing_ok=True)
            else:
                _restore(journal_directory, operation, target, staged, problems)
            staged.unlink(missing_ok=True)
        except OSError as error:
            problems.append(f"{operation.path}: {error.strerror}")
    for name in reversed(journal.directories):
        with contextlib.suppress(OSError):
            (root / name).rmdir()
    if not problems:
        _discard(journal_directory)
    return problems


def _restore(
    journal_directory: Path, operation: _Operation, target: Path, staged: Path, problems: list[str]
) -> None:
    backup = None if operation.backup is None else journal_directory / operation.backup
    if backup is None or backup.is_symlink() or not backup.is_file():
        problems.append(f"{operation.path}: its backup is missing")
        return
    # The staged copy inherited the target's mode, which may forbid writing it again.
    staged.unlink(missing_ok=True)
    shutil.copyfile(backup, staged)
    shutil.copymode(backup, staged)
    os.replace(staged, target)


def _discard(journal_directory: Path) -> None:
    """Remove a journal; deleting `journal.json` first is what marks it settled."""
    with contextlib.suppress(FileNotFoundError):
        (journal_directory / _JOURNAL_FILE).unlink()
    _remove_tree(journal_directory)


def _remove_tree(directory: Path) -> None:
    """Remove a directory tree, trying again a few times when something briefly holds a file.

    On Windows an indexer or scanner can hold a just-written file open for a moment. A tree
    that is still there afterwards is left for the next writer or `recover` to remove.
    """
    for attempt in range(_REMOVAL_ATTEMPTS):
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(_REMOVAL_PAUSE_SECONDS * (attempt + 1))
        else:
            return
    shutil.rmtree(directory, ignore_errors=True)


def _settle_committed(root: Path) -> None:
    """Remove journal directories that hold no record, which no write is still using."""
    for name in _journal_directories(root):
        if not _has_record(root / name):
            _remove_tree(root / name)


def recover(root: Path, *, force: bool = False) -> Recovery:
    """Roll back every unfinished transaction of a vault and clear what it left.

    A journal is undone, never completed. Staged files without a journal are removed. A lock
    held by a live process of this host is refused, as is one whose writer cannot be told
    (another host's, or unreadable), unless `force`; a lock whose process is gone is cleared.
    Every journal is read and validated before anything is changed.
    """
    if not root.is_dir():
        raise TransactionError("not-a-vault", f"the directory has no {_REGISTRATION_FILE}")
    state = lock_state(root)
    if state == "live" and not force:
        raise TransactionError(
            "vault-locked", "a write is in progress; wait for it to finish, or use --force"
        )
    if state == "other" and not force:
        raise TransactionError(
            "vault-locked",
            "the write lock was left by another host or cannot be read, so whether a write is "
            "running cannot be told; use --force once you know none is",
        )
    journals = []
    for name in pending_journals(root):
        journal_file = root / name / _JOURNAL_FILE
        try:
            text = read_text(journal_file)
        except TextFileError as error:
            raise TransactionError("journal-invalid", f"{name} cannot be read") from error
        token = name.removeprefix(JOURNAL_PREFIX)
        journals.append((name, _Journal.from_json(text, expected_token=token)))
    rolled_back: list[str] = []
    for name, journal in journals:
        problems = _undo(root, journal, root / name)
        if problems:
            raise TransactionError(
                "recovery-failed", f"{name} could not be fully rolled back: {problems[0]}"
            )
        rolled_back.append(name)
    _settle_committed(root)
    removed = _staged_files(root)
    for relative in removed:
        (root / relative).unlink(missing_ok=True)
    lock = root / LOCK_FILE
    cleared = lock.is_file()
    lock.unlink(missing_ok=True)
    return Recovery(tuple(rolled_back), removed, cleared)


class _SignalDeferral:
    """Hold back interrupt and termination signals for a block, noting the first that arrived.

    Used where a signal would leave the vault between two states: during a rollback, and
    around the removal of `journal.json`. Handlers can only be set from the main thread; off
    it the block runs without deferral.
    """

    def __init__(self) -> None:
        self.signum: int | None = None
        self._previous: dict[int, _Handler] = {}

    def __enter__(self) -> Self:
        def note(signum: int, _frame: FrameType | None) -> None:
            if self.signum is None:
                self.signum = signum

        self._previous = _set_handlers(("SIGINT", *_TERMINATION_SIGNALS), note)
        return self

    def __exit__(self, *_exc: object) -> Literal[False]:
        _restore_handlers(self._previous)
        return False


def _set_handlers(names: tuple[str, ...], handler: _Handler) -> dict[int, _Handler]:
    previous: dict[int, _Handler] = {}
    for name in names:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[number] = signal.signal(number, handler)
        except (ValueError, OSError):
            # Handlers can only be set from the main thread.
            continue
    return previous


def _restore_handlers(previous: dict[int, _Handler]) -> None:
    for number, handler in previous.items():
        with contextlib.suppress(ValueError, OSError, TypeError):
            signal.signal(number, handler if handler is not None else signal.SIG_DFL)


def _stop_for(signum: int) -> BaseException:
    return KeyboardInterrupt() if signum == signal.SIGINT else _Terminated(signum)


type _Outcome = Literal["unchanged", "undone", "incomplete"]


class Transaction:
    """One write into a vault. Use it as a context manager and call `commit` once.

    Entering refuses when the root is no vault (`not-a-vault`), another writer holds the lock
    (`vault-locked`) or an earlier transaction was left unfinished (`transaction-incomplete`).
    Everything read to plan the writes must be read inside the block, since the lock is what
    keeps the vault unchanged meanwhile. Nothing touches the disk before `commit`; leaving the
    block without committing writes nothing.

    `create_root` is for `init`: the root and its missing parents are created, and removed
    again unless the transaction commits.
    """

    def __init__(self, root: Path, *, create_root: bool = False) -> None:
        self._root = root
        self._create_root = create_root
        self._puts: dict[str, bytes] = {}
        self._deletes: list[str] = []
        self._directories: list[str] = []
        self._created_roots: list[Path] = []
        self._lock_held = False
        self._committed = False
        self._past_commit_point = False
        self._outcome: _Outcome = "unchanged"
        self._previous_handlers: dict[int, _Handler] = {}
        self._journal: _Journal | None = None
        self._journal_directory: Path | None = None

    def __enter__(self) -> Self:
        root = self._root
        if not self._create_root and not (root / _REGISTRATION_FILE).is_file():
            raise TransactionError("not-a-vault", f"the directory has no {_REGISTRATION_FILE}")
        if write_in_progress(root):
            raise TransactionError("vault-locked", WRITE_IN_PROGRESS)
        if pending := pending_journals(root):
            raise TransactionError(
                "transaction-incomplete",
                f"an earlier write was not finished ({pending[0]}); run sabot recover",
            )
        self._previous_handlers = _set_handlers(_TERMINATION_SIGNALS, self._raise_terminated)
        try:
            self._acquire_lock()
            _settle_committed(root)
        except BaseException:
            self._release_lock()
            _restore_handlers(self._previous_handlers)
            self._previous_handlers = {}
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if isinstance(exc, HardStop):
            self._restore_signal_handlers()
            return False
        try:
            self._release_lock()
            if not self._committed:
                self._remove_created_roots()
        finally:
            self._restore_signal_handlers()
        if isinstance(exc, KeyboardInterrupt | _Terminated):
            raise TransactionError("interrupted", self._interrupted_message()) from exc
        return False

    def _interrupted_message(self) -> str:
        if self._committed:
            return (
                "the run was interrupted after the write was complete; the vault holds the result"
            )
        if self._outcome == "incomplete":
            return "the run was interrupted and could not be fully undone; run sabot recover"
        return "the run was interrupted and the vault was left as it was"

    def put(self, relative: str, data: bytes) -> None:
        """Plan one file: created when absent from the vault, replaced when present."""
        self._check_relative(relative)
        if relative in self._puts or relative in self._deletes:
            raise ValueError(f"{relative} is already planned")
        self._puts[relative] = data

    def delete(self, relative: str) -> None:
        """Plan the removal of one file; nothing happens when it is absent at commit time."""
        self._check_relative(relative)
        if relative in self._puts or relative in self._deletes:
            raise ValueError(f"{relative} is already planned")
        self._deletes.append(relative)

    def make_directory(self, relative: str) -> None:
        """Plan one directory, with its missing parents, even when no file goes in it."""
        self._check_relative(relative)
        self._directories.append(relative)

    def commit(self) -> None:
        """Apply the plan, or undo it and raise `TransactionError`."""
        if not (self._puts or self._deletes or self._directories):
            self._committed = True
            return
        try:
            self._run()
        except HardStop:
            raise
        except BaseException as error:
            if self._past_commit_point:
                # `journal.json` is gone, so the write is complete whatever came after.
                self._committed = True
                if self._journal_directory is not None:
                    _discard(self._journal_directory)
                if isinstance(error, Exception):
                    return
                raise
            with _SignalDeferral() as deferral:
                problems = self._roll_back()
            self._outcome = "incomplete" if problems else "undone"
            if not isinstance(error, Exception):
                raise
            if deferral.signum is not None:
                raise _stop_for(deferral.signum) from error
            raise TransactionError("write-failed", _describe(error, problems)) from error
        self._committed = True

    @staticmethod
    def _check_relative(relative: str) -> None:
        if not is_vault_path(relative):
            raise ValueError(f"{relative!r} is not a path inside the vault")

    def _ordered_paths(self) -> list[str]:
        """Every planned file in apply order: by name, the registration file last."""
        planned = [*self._puts, *(path for path in self._deletes if (self._root / path).exists())]
        return sorted(planned, key=lambda path: (path == _REGISTRATION_FILE, path))

    def _missing_directories(self) -> list[str]:
        """Directories the plan needs that do not exist, parents before children."""
        wanted: set[str] = set()
        chains = [PurePosixPath(path).parent.parts for path in self._puts]
        chains += [PurePosixPath(name).parts for name in self._directories]
        for parts in chains:
            for depth in range(1, len(parts) + 1):
                name = "/".join(parts[:depth])
                if not (self._root / name).is_dir():
                    wanted.add(name)
        return sorted(wanted, key=lambda name: (name.count("/"), name))

    def _run(self) -> None:
        root = self._root
        paths = self._ordered_paths()
        digests = {path: hashlib.sha256(self._puts.get(path, b"")).hexdigest() for path in paths}
        token = _token_of(
            [(path, digests[path] if path in self._puts else "delete") for path in paths]
        )
        journal_directory = root / f"{JOURNAL_PREFIX}{token}"
        self._journal_directory = journal_directory
        operations: list[_Operation] = []
        for index, path in enumerate(paths):
            target = root / path
            exists = target.exists()
            if exists and not os.access(target, os.W_OK):
                raise PermissionError(errno.EACCES, "permission denied")
            if path in self._puts:
                kind: _Kind = "replace" if exists else "create"
            else:
                kind = "delete"
            operations.append(
                _Operation(
                    kind=kind,
                    path=path,
                    sha256=digests[path],
                    staged=_staged_name(token, target).relative_to(root).as_posix(),
                    backup=f"{_BACKUP_DIRECTORY}/{index}" if exists else None,
                )
            )
        journal = _Journal(token, tuple(operations), tuple(self._missing_directories()))
        self._write_journal(journal, journal_directory)
        self._journal = journal
        _notify("journaled", 0)
        for name in journal.directories:
            (root / name).mkdir()
        for index, operation in enumerate(journal.operations):
            staged = root / operation.staged
            if operation.kind != "delete":
                _write_synced(staged, self._puts[operation.path])
                if operation.kind == "replace":
                    shutil.copymode(root / operation.path, staged)
            _notify("staged", index)
        for index, operation in enumerate(journal.operations):
            staged = root / operation.staged
            if operation.kind == "delete":
                (root / operation.path).unlink()
            else:
                if hashlib.sha256(staged.read_bytes()).hexdigest() != operation.sha256:
                    raise OSError(errno.EIO, "a staged file changed before it was applied")
                os.replace(staged, root / operation.path)
            _sync_directory((root / operation.path).parent)
            _notify("applied", index)
        with _SignalDeferral() as deferral:
            (journal_directory / _JOURNAL_FILE).unlink()
            self._past_commit_point = True
        self._journal = None
        _sync_directory(root)
        _remove_tree(journal_directory)
        if deferral.signum is not None:
            raise _stop_for(deferral.signum)

    def _write_journal(self, journal: _Journal, journal_directory: Path) -> None:
        journal_directory.mkdir()
        (journal_directory / _BACKUP_DIRECTORY).mkdir()
        for operation in journal.operations:
            if operation.backup is not None:
                backup = journal_directory / operation.backup
                shutil.copyfile(self._root / operation.path, backup)
                shutil.copymode(self._root / operation.path, backup)
        temporary = journal_directory / f"{_JOURNAL_FILE}.tmp"
        _write_synced(temporary, journal.to_json().encode("utf-8"))
        os.replace(temporary, journal_directory / _JOURNAL_FILE)
        _sync_directory(journal_directory)
        _sync_directory(self._root)

    def _roll_back(self) -> list[str]:
        """Undo what the failed run did; return what could not be undone."""
        journal_directory = self._journal_directory
        if journal_directory is None or not journal_directory.exists():
            return []
        if self._journal is None:
            # Failed before the journal was complete: no target had changed yet.
            _discard(journal_directory)
            return []
        try:
            return _undo(self._root, self._journal, journal_directory)
        except BaseException:
            self._outcome = "incomplete"
            raise

    def _acquire_lock(self) -> None:
        root = self._root
        if self._create_root:
            missing = [root, *root.parents]
            self._created_roots = [path for path in missing if not path.exists()]
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                self._remove_created_roots()
                raise _write_failure(error) from error
        try:
            descriptor = os.open(root / LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            message = (
                WRITE_IN_PROGRESS
                if write_in_progress(root)
                else "a write left its lock behind; run sabot recover"
            )
            raise TransactionError("vault-locked", message) from error
        except OSError as error:
            self._remove_created_roots()
            raise _write_failure(error) from error
        self._lock_held = True
        try:
            holder = json.dumps({"host": _host_name(), "pid": os.getpid()}, sort_keys=True)
            os.write(descriptor, holder.encode("utf-8") + b"\n")
        except OSError as error:
            self._release_lock()
            self._remove_created_roots()
            raise _write_failure(error) from error
        finally:
            os.close(descriptor)

    def _release_lock(self) -> None:
        if self._lock_held:
            (self._root / LOCK_FILE).unlink(missing_ok=True)
            self._lock_held = False

    def _remove_created_roots(self) -> None:
        for path in self._created_roots:
            with contextlib.suppress(OSError):
                path.rmdir()
        self._created_roots = []

    @staticmethod
    def _raise_terminated(signum: int, _frame: FrameType | None) -> None:
        raise _Terminated(signum)

    def _restore_signal_handlers(self) -> None:
        _restore_handlers(self._previous_handlers)
        self._previous_handlers = {}


def _write_failure(error: OSError) -> TransactionError:
    return TransactionError("write-failed", f"a file could not be written: {error.strerror}")


def _describe(error: Exception, problems: list[str]) -> str:
    if isinstance(error, OSError):
        message = f"a file could not be written: {error.strerror}"
    else:
        message = f"the write failed ({type(error).__name__})"
    if problems:
        message += "; it could not be fully undone, run sabot recover"
    return message
