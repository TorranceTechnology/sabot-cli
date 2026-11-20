"""Write locks as another process would have left them."""

import json
import socket
import subprocess
import sys
from pathlib import Path

from sabot_cli.vault.transaction import LOCK_FILE


def dead_process_id() -> int:
    """The id of a process that has run and exited, so it is not running now."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def write_lock(vault: Path, *, pid: object, host: str | None = None, start: object = None) -> None:
    """Plant a lock naming `pid` on `host` (this host by default), with a start time if given."""
    holder: dict[str, object] = {"host": socket.gethostname() if host is None else host, "pid": pid}
    if start is not None:
        holder["start"] = start
    (vault / LOCK_FILE).write_text(json.dumps(holder, sort_keys=True) + "\n", encoding="utf-8")


def abandon_lock(vault: Path) -> None:
    """Make the lock a killed writer left read as one whose process is gone.

    A simulated kill (`HardStop`) leaves the lock naming the test's own live process; a real
    kill leaves one whose process is gone, which is what recovery has to be shown.
    """
    write_lock(vault, pid=dead_process_id())
