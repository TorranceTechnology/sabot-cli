"""The per-user key that signs write journals, and the directory it lives in.

`recover` applies a journal only when the journal carries a MAC made with this key, so only a
journal that a sabot of this user wrote on this machine is ever applied. The key is 32 random
bytes in a file of its own in a per-user state directory, which must lie outside every vault: a
vault can be shared, and a key inside one would be shared with it.

A key file that exists but is not 32 bytes, is a link or is not a regular file is an error and
is never replaced, since replacing it would make every earlier journal unverifiable without a
word.
"""

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from sabot_cli.vault._errors import TransactionError

__all__ = [
    "KEY_FILE",
    "KEY_LENGTH",
    "STATE_DIRECTORY_VARIABLE",
    "canonical_journal_bytes",
    "default_state_directory",
    "mac_matches",
    "read_key",
    "sign",
    "signing_key",
    "state_directory",
]

STATE_DIRECTORY_VARIABLE = "SABOT_STATE_DIR"
KEY_FILE = "journal.key"
KEY_LENGTH = 32
_MAC_FIELD = "mac"


def default_state_directory() -> Path:
    """The per-user state directory for this platform, ignoring `SABOT_STATE_DIR`."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "sabot"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "sabot"
    configured = os.environ.get("XDG_STATE_HOME")
    # The XDG rules say a relative path there is to be ignored.
    base_path = Path(configured) if configured and os.path.isabs(configured) else None
    return (base_path or Path.home() / ".local" / "state") / "sabot"


def state_directory() -> Path:
    """The directory the key lives in: `SABOT_STATE_DIR` when it is set, else the platform's."""
    override = os.environ.get(STATE_DIRECTORY_VARIABLE)
    return Path(override) if override else default_state_directory()


def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.normcase(path), os.path.normcase(directory)
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def _refuse_state_inside(vault: Path) -> None:
    if _inside(os.path.realpath(state_directory()), os.path.realpath(vault)):
        raise TransactionError(
            "state-directory-in-vault",
            "the directory that holds the journal key lies inside the vault, where it would "
            f"travel with the vault; set {STATE_DIRECTORY_VARIABLE} to a directory outside it",
        )


def _invalid(reason: str) -> TransactionError:
    return TransactionError("key-invalid", f"the journal key file {reason}; it is not replaced")


def _read_existing(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as error:
        raise _invalid(f"cannot be read ({error.strerror})") from error
    if stat.S_ISLNK(info.st_mode):
        raise _invalid("is a link")
    if not stat.S_ISREG(info.st_mode):
        raise _invalid("is not a regular file")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(KEY_LENGTH + 1)
    except OSError as error:
        raise _invalid(f"cannot be read ({error.strerror})") from error
    if len(data) != KEY_LENGTH:
        raise _invalid(f"does not hold {KEY_LENGTH} bytes")
    return data


def read_key(vault: Path) -> bytes | None:
    """The key, or None when this user has none yet; an unusable key file raises `key-invalid`.

    Nothing is created. Refuses with `state-directory-in-vault` when the state directory lies
    inside `vault`.
    """
    _refuse_state_inside(vault)
    path = state_directory() / KEY_FILE
    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise _invalid(f"cannot be read ({error.strerror})") from error
    return _read_existing(path)


def signing_key(vault: Path) -> bytes:
    """The key, created on first need with exclusive-create and mode 0600.

    Two writers that race to create it agree on the one that landed: the key is written to a
    private temporary file and linked into place, so a reader never sees a half-written key.
    """
    existing = read_key(vault)
    if existing is not None:
        return existing
    directory = state_directory()
    path = directory / KEY_FILE
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        return _create(directory, path)
    except FileExistsError:
        return _read_existing(path)
    except OSError as error:
        raise TransactionError(
            "write-failed", f"the journal key could not be created: {error.strerror}"
        ) from error


def _create(directory: Path, path: Path) -> bytes:
    key = secrets.token_bytes(KEY_LENGTH)
    temporary = directory / f".{KEY_FILE}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise
        except OSError:
            # A file system without hard links: create the key in place, still exclusively.
            target = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(target, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()
    return key


def canonical_journal_bytes(document: dict[str, object]) -> bytes:
    """The bytes a journal's MAC covers: its fields without the MAC, as canonical JSON."""
    body = {name: value for name, value in document.items() if name != _MAC_FIELD}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sign(document: dict[str, object], key: bytes) -> dict[str, object]:
    """The document with its `mac` set."""
    mac = hmac.new(key, canonical_journal_bytes(document), hashlib.sha256).hexdigest()
    return {**document, _MAC_FIELD: mac}


def mac_matches(document: dict[str, object], key: bytes) -> bool:
    """Whether the document's `mac` is the one `key` makes for the rest of it."""
    found = document.get(_MAC_FIELD)
    if not isinstance(found, str):
        return False
    try:
        expected = hmac.new(key, canonical_journal_bytes(document), hashlib.sha256).hexdigest()
    except (ValueError, TypeError, RecursionError):
        return False
    return hmac.compare_digest(found.encode("utf-8", "replace"), expected.encode("ascii"))
