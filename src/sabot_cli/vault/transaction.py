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
one and put back by a rollback. No transaction names a hidden path: the lock and the journal are
managed here, and are not operations of it. A vault can arrive from elsewhere, so `recover`
applies only a journal whose MAC verifies under the per-user key (`_key.py`), whose vault path is
this vault and whose backups hold the recorded bytes, and even then only as far as it can check
it: see `_journal.py` for what a journal may name and the conditions under which an undo changes
a file. `discard` removes journal directories and staged files without applying anything, and is
safe on any vault.

A lock file created with exclusive-create makes a second writer refuse with `vault-locked`. It
records the writer's process id, start time, boot identity and host name, so that a reader can
tell a write in progress from one that was killed. Two things are out of scope: concurrent
writers that bypass this module, and power loss on a file system that does not order a rename
against the directory reaching disk.
"""

import contextlib
import errno
import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import FrameType, TracebackType
from typing import Literal, Self

from sabot_cli.vault._errors import TransactionError
from sabot_cli.vault._journal import (
    BACKUP_DIRECTORY,
    JOURNAL_FILE,
    JOURNAL_PREFIX,
    STAGED_PREFIX,
    Journal,
    Kind,
    Operation,
    is_vault_path,
    journal_refusal,
    parse_journal_document,
    plan_undo,
    run_undo,
    staged_name,
    sync_directory,
    verify_backups,
    write_synced,
)
from sabot_cli.vault._key import mac_matches, read_key, signing_key
from sabot_cli.vault._process import (
    boot_identity,
    plausible_pid,
    process_is_alive,
    process_start,
    same_boot,
)
from sabot_cli.vault.text import TextFileError, read_text

__all__ = [
    "JOURNAL_PREFIX",
    "LOCK_FILE",
    "WRITE_IN_PROGRESS",
    "Discard",
    "Recovery",
    "Transaction",
    "TransactionError",
    "atomic_write",
    "discard",
    "incomplete_work",
    "is_vault_path",
    "lock_state",
    "pending_journals",
    "recover",
    "write_in_progress",
]

LOCK_FILE = ".sabot-lock"
# The file that registers a vault; applied last so a vault never names a source whose files
# are absent.
_REGISTRATION_FILE = "_ruleset.yaml"
_TOKEN_LENGTH = 12
WRITE_IN_PROGRESS = (
    "a write is in progress in this vault; try again when it has finished, "
    "or run sabot recover --force if no sabot process is running"
)
_TERMINATION_SIGNALS = ("SIGTERM", "SIGHUP", "SIGBREAK")
_REMOVAL_ATTEMPTS = 5
_REMOVAL_PAUSE_SECONDS = 0.05

type Checkpoint = Callable[[str, int], None]
type _Handler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None


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
class Recovery:
    """What `recover` did, by name and relative path, sorted."""

    rolled_back: tuple[str, ...]
    removed_staged: tuple[str, ...]
    cleared_lock: bool


@dataclass(frozen=True)
class Discard:
    """What `discard` removed, by name and relative path, sorted."""

    removed_journals: tuple[str, ...]
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


def _token_of(entries: list[tuple[str, str]]) -> str:
    encoded = json.dumps(entries, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:_TOKEN_LENGTH]


def atomic_write(path: Path, data: bytes) -> None:
    """Write one file outside any vault by staging a hidden sibling and renaming it over `path`.

    Missing parent directories are created. The staged file is removed on failure. A file
    already at `path` is replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = staged_name(_token_of([(path.name, hashlib.sha256(data).hexdigest())]), path)
    try:
        write_synced(staged, data)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    sync_directory(path.parent)


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
    return (journal_directory / JOURNAL_FILE).is_file()


def _staged_files(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    for directory, subdirectories, files in os.walk(root):
        # A hidden directory (`.git`, `.obsidian`, a journal) is never entered: this tool names
        # none, and what is inside belongs to something else.
        subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
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

    `none`: no lock. `live`: the process that wrote it is running on this host. `stale`: it was
    written in another boot of this machine, or its process is gone, or its id now belongs to a
    process that started at another time. `other`: it was written on another host, or cannot be
    read or understood, so whether its writer is running cannot be told. When the lock records a
    start time and the running process's start time cannot be read, the lock is `live` unless the
    boot differs: a lock is never judged stale on a guess.
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
        start = document.get("start")
    except (ValueError, KeyError, TypeError, AttributeError):
        return "other"
    if not isinstance(pid, int) or isinstance(pid, bool) or not plausible_pid(pid):
        return "other"
    if host != _host_name():
        return "other"
    if start is not None and not isinstance(start, str):
        return "other"
    boot = document.get("boot")
    if boot is not None and not isinstance(boot, str):
        return "other"
    if boot is not None:
        current_boot = boot_identity()
        if current_boot is not None and not same_boot(boot, current_boot):
            return "stale"
    try:
        if not process_is_alive(pid):
            return "stale"
    except (OverflowError, ValueError):
        return "other"
    if start is not None:
        current = process_start(pid)
        if current is not None and current != start:
            return "stale"
    return "live"


def write_in_progress(root: Path) -> bool:
    """Whether a sabot process on this host is writing the vault right now."""
    return lock_state(root) == "live"


def _host_name() -> str:
    return socket.gethostname()


def _discard(journal_directory: Path) -> None:
    """Remove a journal; deleting `journal.json` first is what marks it settled."""
    with contextlib.suppress(FileNotFoundError):
        (journal_directory / JOURNAL_FILE).unlink()
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


_DISCARD_HINT = "; to drop the journal without applying it, run sabot recover --discard"
_HINTED_CODES = frozenset(
    {"journal-invalid", "journal-unverified", "key-invalid", "state-directory-in-vault"}
)


def _require_vault_directory(root: Path) -> None:
    if not root.is_dir():
        raise TransactionError("not-a-vault", f"the directory has no {_REGISTRATION_FILE}")


def _refuse_unless_forced_over_lock(root: Path, *, force: bool) -> None:
    """Refuse while a write may be running; `force` overrides this and nothing else."""
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


def _clear_lock(root: Path) -> bool:
    lock = root / LOCK_FILE
    cleared = lock.is_file()
    if lock.is_file() or lock.is_symlink():
        lock.unlink()
    return cleared


def recover(root: Path, *, force: bool = False) -> Recovery:
    """Roll back every unfinished transaction of a vault and clear what it left.

    A journal is undone, never completed, and only when it is verified: its MAC is made with
    this user's key, it names this vault and its backups hold the recorded bytes. A journal that
    fails that is refused with `journal-unverified`, whatever `force` says. A verified journal is
    then read, checked against the files it names, and planned before anything is changed: if
    one operation cannot be verified, nothing is changed and the refusal says which. Staged files
    without a journal are removed. A lock held by a live process of this host is refused, as is
    one whose writer cannot be told (another host's, or unreadable), unless `force`; a lock whose
    process is gone is cleared. `force` covers the lock only.
    """
    _require_vault_directory(root)
    _refuse_unless_forced_over_lock(root, force=force)
    journals = [(name, _read_journal(root, name)) for name in pending_journals(root)]
    _refuse_shared_files(journals)
    plans = [(name, plan_undo(root, root / name, journal)) for name, journal in journals]
    for name, plan in plans:
        if plan.problems:
            raise TransactionError(
                "recovery-failed",
                f"{name} was not rolled back, and nothing was changed: {plan.problems[0]}",
            )
    rolled_back: list[str] = []
    for name, plan in plans:
        problems = run_undo(plan)
        if problems:
            raise TransactionError(
                "recovery-failed", f"{name} could not be fully rolled back: {problems[0]}"
            )
        _discard(root / name)
        rolled_back.append(name)
    _settle_committed(root)
    removed = _staged_files(root)
    for relative in removed:
        (root / relative).unlink(missing_ok=True)
    return Recovery(tuple(rolled_back), removed, _clear_lock(root))


def discard(root: Path, *, force: bool = False) -> Discard:
    """Remove every journal directory and staged file of a vault, and the lock if it is stale.

    Nothing a journal names is read or changed, so this is safe on a vault whose journals are not
    trusted. It removes entries named `.sabot-txn-*` at the vault root (a link of that name is
    unlinked, never followed; a regular file of that name is left) and files named
    `.sabot-new-*` below non-hidden directories, never following a link and never entering a
    hidden directory. The lock rules are those of `recover`, and `force` covers them only.
    """
    _require_vault_directory(root)
    _refuse_unless_forced_over_lock(root, force=force)
    removed_journals: list[str] = []
    for name in sorted(entry for entry in os.listdir(root) if entry.startswith(JOURNAL_PREFIX)):
        path = root / name
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            _remove_tree(path)
            if path.exists():
                raise TransactionError("recovery-failed", f"{name} could not be removed")
        else:
            continue
        removed_journals.append(name)
    removed = _staged_files(root)
    for relative in removed:
        (root / relative).unlink(missing_ok=True)
    return Discard(tuple(removed_journals), removed, _clear_lock(root))


def _read_journal(root: Path, name: str) -> Journal:
    """Read a journal and verify it is one this user's sabot wrote for this vault.

    The MAC is checked before the journal's operations are read for any purpose, and a journal
    that does not verify is refused with `journal-unverified`.
    """
    try:
        return _read_verified_journal(root, name)
    except TransactionError as error:
        if error.code in _HINTED_CODES:
            raise TransactionError(error.code, error.message + _DISCARD_HINT) from error
        raise


def _read_verified_journal(root: Path, name: str) -> Journal:
    directory = root / name
    journal_file = directory / JOURNAL_FILE
    if directory.is_symlink() or journal_file.is_symlink():
        raise journal_refusal(f"{name} is a link, which a write of this tool never leaves")
    if (directory / BACKUP_DIRECTORY).is_symlink():
        raise journal_refusal(f"the backup directory of {name} is a link")
    try:
        text = read_text(journal_file)
    except TextFileError as error:
        raise TransactionError("journal-invalid", f"{name} cannot be read") from error
    document = parse_journal_document(text)
    key = read_key(root)
    if key is None:
        raise TransactionError(
            "journal-unverified",
            f"{name} cannot be verified: this user has no journal key, so no journal here is "
            "one this machine wrote",
        )
    if not mac_matches(document, key):
        raise TransactionError(
            "journal-unverified",
            f"{name} was not written by this user's sabot on this machine (its MAC does not "
            "verify), so it is not applied",
        )
    journal = Journal.from_document(document, expected_token=name.removeprefix(JOURNAL_PREFIX))
    if not _same_directory(journal.vault, root):
        raise TransactionError(
            "journal-unverified", f"{name} was written for another vault ({journal.vault!r})"
        )
    verify_backups(directory, journal)
    return journal


def _same_directory(recorded: str, root: Path) -> bool:
    try:
        return os.path.samefile(recorded, root)
    except (OSError, ValueError):
        return False


def _refuse_shared_files(journals: list[tuple[str, Journal]]) -> None:
    owner: dict[str, str] = {}
    for name, journal in journals:
        for operation in journal.operations:
            if owner.setdefault(operation.path, name) != name:
                raise journal_refusal(f"{owner[operation.path]} and {name} name the same file")


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
        self._journal: Journal | None = None
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
        if self._committed or self._past_commit_point:
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
        # Created on first need, before anything in the vault is touched; a refusal here leaves
        # the vault as it was.
        key = signing_key(self._root)
        try:
            self._run(key)
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
                directory = self._root / name
                if directory.is_dir():
                    continue
                if directory.exists():
                    # A file where a directory is wanted would make `mkdir` fail after the
                    # journal exists; refuse before there is one.
                    raise NotADirectoryError(errno.ENOTDIR, "Not a directory", name)
                wanted.add(name)
        return sorted(wanted, key=lambda name: (name.count("/"), name))

    def _run(self, key: bytes) -> None:
        root = self._root
        paths = self._ordered_paths()
        digests = {path: hashlib.sha256(self._puts.get(path, b"")).hexdigest() for path in paths}
        token = _token_of(
            [(path, digests[path] if path in self._puts else "delete") for path in paths]
        )
        journal_directory = root / f"{JOURNAL_PREFIX}{token}"
        self._journal_directory = journal_directory
        directories = tuple(self._missing_directories())
        operations: list[Operation] = []
        for index, path in enumerate(paths):
            target = root / path
            exists = target.exists()
            if exists and not os.access(target, os.W_OK):
                raise PermissionError(errno.EACCES, "permission denied")
            if path in self._puts:
                kind: Kind = "replace" if exists else "create"
            else:
                kind = "delete"
            operations.append(
                Operation(
                    kind=kind,
                    path=path,
                    sha256=digests[path],
                    staged=staged_name(token, target).relative_to(root).as_posix(),
                    backup=f"{BACKUP_DIRECTORY}/{index}" if exists else None,
                    backup_sha256=hashlib.sha256(target.read_bytes()).hexdigest()
                    if exists
                    else None,
                    mode=stat.S_IMODE(target.stat().st_mode) if exists else None,
                )
            )
        journal = Journal(token, os.path.realpath(root), tuple(operations), directories)
        self._write_journal(journal, journal_directory, key)
        # From here the vault can change, so any way out of this run that is not a finished
        # rollback has to say so.
        self._outcome = "incomplete"
        self._journal = journal
        _notify("journaled", 0)
        for name in journal.directories:
            (root / name).mkdir()
        for index, operation in enumerate(journal.operations):
            staged = root / operation.staged
            if operation.kind != "delete":
                write_synced(staged, self._puts[operation.path])
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
            sync_directory((root / operation.path).parent)
            _notify("applied", index)
        with _SignalDeferral() as deferral:
            (journal_directory / JOURNAL_FILE).unlink()
            self._past_commit_point = True
        self._journal = None
        sync_directory(root)
        _remove_tree(journal_directory)
        if deferral.signum is not None:
            raise _stop_for(deferral.signum)

    def _write_journal(self, journal: Journal, journal_directory: Path, key: bytes) -> None:
        journal_directory.mkdir()
        (journal_directory / BACKUP_DIRECTORY).mkdir()
        for operation in journal.operations:
            if operation.backup is not None:
                backup = journal_directory / operation.backup
                shutil.copyfile(self._root / operation.path, backup)
                shutil.copymode(self._root / operation.path, backup)
                if hashlib.sha256(backup.read_bytes()).hexdigest() != operation.backup_sha256:
                    raise OSError(errno.EIO, "a file changed while it was being backed up")
        temporary = journal_directory / f"{JOURNAL_FILE}.tmp"
        write_synced(temporary, journal.to_json(key).encode("utf-8"))
        os.replace(temporary, journal_directory / JOURNAL_FILE)
        sync_directory(journal_directory)
        sync_directory(self._root)

    def _roll_back(self) -> list[str]:
        """Undo what the failed run did; return what could not be undone."""
        journal_directory = self._journal_directory
        if journal_directory is None or not journal_directory.exists():
            return []
        if self._journal is None:
            # Failed before the journal was complete: no target had changed yet.
            _discard(journal_directory)
            return []
        plan = plan_undo(self._root, journal_directory, self._journal, trusted=True)
        problems = [*plan.problems, *run_undo(plan)]
        if not problems:
            _discard(journal_directory)
        return problems

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
            holder = json.dumps(
                {
                    "boot": boot_identity(),
                    "host": _host_name(),
                    "pid": os.getpid(),
                    "start": process_start(os.getpid()),
                },
                sort_keys=True,
            )
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
