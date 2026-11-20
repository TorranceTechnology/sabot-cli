"""All-or-nothing writes into a vault, and the recovery of a write that was killed.

Every command that writes into a vault goes through `Transaction`; none writes a vault file
another way. A transaction computes every output in memory first, then:

1. records a journal directory `.sabot-txn-<token>/` at the vault root, holding a backup of
   every file that will be replaced and `journal.json`, the ordered list of operations;
2. creates any missing directories and stages each output as a hidden sibling of its target,
   `.sabot-new-<token>-<name>`, flushed to disk;
3. applies the operations with `os.replace`, the registration file `_ruleset.yaml` last;
4. removes `journal.json`, which is the commit point, and then the rest of the journal.

An exception, `KeyboardInterrupt` or a termination signal the platform lets Python catch undoes
the applied operations in reverse and removes every staged file, so the vault's bytes are what
they were before the run. A process killed outright leaves the journal behind; every command
then refuses with `transaction-incomplete` until `recover` rolls the journal back. Recovery never
rolls forward.

A lock file created with exclusive-create makes a second writer refuse with `vault-locked`.
Two things are out of scope: concurrent writers that bypass this module, and power loss on a
file system that does not order a rename against the directory reaching disk.
"""

import contextlib
import errno
import hashlib
import json
import os
import shutil
import signal
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import FrameType, TracebackType
from typing import Literal, Self

__all__ = [
    "LOCK_FILE",
    "Checkpoint",
    "HardStop",
    "Recovery",
    "Transaction",
    "TransactionError",
    "atomic_write",
    "checkpoint_hook",
    "incomplete_work",
    "pending_journals",
    "recover",
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
_TERMINATION_SIGNALS = ("SIGTERM", "SIGHUP", "SIGBREAK")

type Checkpoint = Callable[[str, int], None]
type _Kind = Literal["create", "replace"]
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
    def from_json(cls, text: str) -> Self:
        try:
            document = json.loads(text)
            operations = tuple(
                _Operation(
                    kind=item["kind"],
                    path=item["path"],
                    sha256=item["sha256"],
                    staged=item["staged"],
                    backup=item["backup"],
                )
                for item in document["operations"]
            )
            directories = tuple(str(name) for name in document["directories"])
            return cls(str(document["token"]), operations, directories)
        except (ValueError, KeyError, TypeError) as error:
            raise TransactionError("journal-invalid", "a journal cannot be read") from error


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
    """Names of the journal directories an unfinished transaction left at the vault root."""
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in root.iterdir()
            if entry.name.startswith(JOURNAL_PREFIX) and entry.is_dir()
        )
    )


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


def _undo(root: Path, journal: _Journal, journal_directory: Path) -> list[str]:
    """Undo a journal's operations in reverse and remove the journal; return what failed."""
    problems: list[str] = []
    for operation in reversed(journal.operations):
        target, staged = root / operation.path, root / operation.staged
        try:
            if operation.kind == "replace" and operation.backup is not None:
                backup = journal_directory / operation.backup
                if backup.is_file():
                    shutil.copyfile(backup, staged)
                    shutil.copymode(backup, staged)
                    os.replace(staged, target)
            elif operation.kind == "create":
                target.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)
        except OSError as error:
            problems.append(f"{operation.path}: {error.strerror}")
    for name in reversed(journal.directories):
        with contextlib.suppress(OSError):
            (root / name).rmdir()
    if not problems:
        _discard(journal_directory)
    return problems


def _discard(journal_directory: Path) -> None:
    """Remove a journal; deleting `journal.json` first is what marks it settled."""
    with contextlib.suppress(FileNotFoundError):
        (journal_directory / _JOURNAL_FILE).unlink()
    shutil.rmtree(journal_directory, ignore_errors=True)


def recover(root: Path) -> Recovery:
    """Roll back every unfinished transaction of a vault and clear what it left.

    A journal is undone, never completed. Staged files without a journal are removed, and a
    lock file is cleared, so run this only when no other `sabot` process is writing the vault.
    """
    if not root.is_dir():
        raise TransactionError("not-a-vault", f"the directory has no {_REGISTRATION_FILE}")
    rolled_back: list[str] = []
    for name in pending_journals(root):
        directory = root / name
        journal_file = directory / _JOURNAL_FILE
        if journal_file.is_file():
            try:
                text = journal_file.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise TransactionError("journal-invalid", f"{name} cannot be read") from error
            problems = _undo(root, _Journal.from_json(text), directory)
            if problems:
                raise TransactionError(
                    "recovery-failed", f"{name} could not be fully rolled back: {problems[0]}"
                )
        else:
            _discard(directory)
        rolled_back.append(name)
    removed = _staged_files(root)
    for relative in removed:
        (root / relative).unlink(missing_ok=True)
    lock = root / LOCK_FILE
    cleared = lock.is_file()
    lock.unlink(missing_ok=True)
    return Recovery(tuple(rolled_back), removed, cleared)


class Transaction:
    """One write into a vault. Use it as a context manager and call `commit` once.

    Entering refuses when the root is no vault (`not-a-vault`), an earlier transaction was
    left unfinished (`transaction-incomplete`) or another writer holds the lock
    (`vault-locked`). Everything read to plan the writes must be read inside the block, since
    the lock is what keeps the vault unchanged meanwhile. Nothing touches the disk before
    `commit`; leaving the block without committing writes nothing.

    `create_root` is for `init`: the root and its missing parents are created, and removed
    again unless the transaction commits.
    """

    def __init__(self, root: Path, *, create_root: bool = False) -> None:
        self._root = root
        self._create_root = create_root
        self._puts: dict[str, bytes] = {}
        self._directories: list[str] = []
        self._created_roots: list[Path] = []
        self._lock_held = False
        self._committed = False
        self._previous_handlers: dict[int, _Handler] = {}
        self._journal: _Journal | None = None
        self._journal_directory: Path | None = None

    def __enter__(self) -> Self:
        root = self._root
        if not self._create_root and not (root / _REGISTRATION_FILE).is_file():
            raise TransactionError("not-a-vault", f"the directory has no {_REGISTRATION_FILE}")
        if pending := pending_journals(root):
            raise TransactionError(
                "transaction-incomplete",
                f"an earlier write was not finished ({pending[0]}); run sabot recover",
            )
        self._install_signal_handlers()
        try:
            self._acquire_lock()
        except BaseException:
            self._restore_signal_handlers()
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
            raise TransactionError(
                "interrupted", "the run was interrupted and the vault was left as it was"
            ) from exc
        return False

    def put(self, relative: str, data: bytes) -> None:
        """Plan one file: created when absent from the vault, replaced when present."""
        self._check_relative(relative)
        if relative in self._puts:
            raise ValueError(f"{relative} is already planned")
        self._puts[relative] = data

    def make_directory(self, relative: str) -> None:
        """Plan one directory, with its missing parents, even when no file goes in it."""
        self._check_relative(relative)
        self._directories.append(relative)

    def commit(self) -> None:
        """Apply the plan, or undo it and raise `TransactionError`."""
        if not self._puts and not self._directories:
            self._committed = True
            return
        try:
            self._run()
        except HardStop:
            raise
        except BaseException as error:
            problems = self._roll_back()
            if not isinstance(error, Exception):
                raise
            raise TransactionError("write-failed", _describe(error, problems)) from error
        self._committed = True

    @staticmethod
    def _check_relative(relative: str) -> None:
        path = PurePosixPath(relative)
        invalid = (
            not relative
            or path.is_absolute()
            or "\\" in relative
            or any(part in ("", ".", "..") or part.startswith(".sabot-") for part in path.parts)
        )
        if invalid:
            raise ValueError(f"{relative!r} is not a path inside the vault")

    def _ordered_paths(self) -> list[str]:
        return sorted(self._puts, key=lambda path: (path == _REGISTRATION_FILE, path))

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
        digests = {path: hashlib.sha256(self._puts[path]).hexdigest() for path in self._puts}
        token = _token_of([(path, digests[path]) for path in self._ordered_paths()])
        journal_directory = root / f"{JOURNAL_PREFIX}{token}"
        self._journal_directory = journal_directory
        operations: list[_Operation] = []
        for index, path in enumerate(self._ordered_paths()):
            target = root / path
            exists = target.exists()
            if exists and not os.access(target, os.W_OK):
                raise PermissionError(errno.EACCES, "permission denied")
            operations.append(
                _Operation(
                    kind="replace" if exists else "create",
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
            _write_synced(staged, self._puts[operation.path])
            if operation.kind == "replace":
                shutil.copymode(root / operation.path, staged)
            _notify("staged", index)
        for index, operation in enumerate(journal.operations):
            staged = root / operation.staged
            if hashlib.sha256(staged.read_bytes()).hexdigest() != operation.sha256:
                raise OSError(errno.EIO, "a staged file changed before it was applied")
            os.replace(staged, root / operation.path)
            _sync_directory((root / operation.path).parent)
            _notify("applied", index)
        (journal_directory / _JOURNAL_FILE).unlink()
        self._journal = None
        _sync_directory(root)
        shutil.rmtree(journal_directory, ignore_errors=True)

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
        return _undo(self._root, self._journal, journal_directory)

    def _acquire_lock(self) -> None:
        root = self._root
        try:
            if self._create_root:
                missing = [root, *root.parents]
                self._created_roots = [path for path in missing if not path.exists()]
                root.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(root / LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise TransactionError(
                "vault-locked", "another sabot process is writing the vault; try again later"
            ) from error
        except OSError as error:
            self._remove_created_roots()
            raise TransactionError(
                "write-failed", f"a file could not be written: {error.strerror}"
            ) from error
        os.close(descriptor)
        self._lock_held = True

    def _release_lock(self) -> None:
        if self._lock_held:
            (self._root / LOCK_FILE).unlink(missing_ok=True)
            self._lock_held = False

    def _remove_created_roots(self) -> None:
        for path in self._created_roots:
            with contextlib.suppress(OSError):
                path.rmdir()
        self._created_roots = []

    def _install_signal_handlers(self) -> None:
        def raise_terminated(signum: int, _frame: FrameType | None) -> None:
            raise _Terminated(signum)

        for name in _TERMINATION_SIGNALS:
            number = getattr(signal, name, None)
            if number is None:
                continue
            try:
                self._previous_handlers[number] = signal.signal(number, raise_terminated)
            except (ValueError, OSError):
                # Handlers can only be set from the main thread.
                continue

    def _restore_signal_handlers(self) -> None:
        for number, handler in self._previous_handlers.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(number, handler if handler is not None else signal.SIG_DFL)
        self._previous_handlers = {}


def _describe(error: Exception, problems: list[str]) -> str:
    if isinstance(error, OSError):
        message = f"a file could not be written: {error.strerror}"
    else:
        message = f"the write failed ({type(error).__name__})"
    if problems:
        message += "; it could not be fully undone, run sabot recover"
    return message
