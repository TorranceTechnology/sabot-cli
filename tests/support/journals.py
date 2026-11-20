"""Journals as a write killed part-way leaves them, built by hand so a test can bend them."""

import hashlib
import json
import os
from pathlib import Path

from sabot_cli.testing import sign, signing_key

TOKEN = "eeeeeeeeeeee"


def record(
    operations: list[dict[str, object]], *, directories: tuple[str, ...] = (), token: str = TOKEN
) -> dict[str, object]:
    return {
        "directories": list(directories),
        "format": 3,
        "operations": operations,
        "token": token,
    }


def operation(
    path: str = "clauses/cl.setup.md",
    *,
    kind: str = "replace",
    sha256: str | None = None,
    token: str = TOKEN,
    **overrides: object,
) -> dict[str, object]:
    name = path.rsplit("/", 1)[-1]
    parent = path[: -len(name)]
    puts_back = kind != "create"
    built: dict[str, object] = {
        "backup": "backup/0" if puts_back else None,
        "backup_sha256": None,  # filled in from the planted backup unless overridden
        "kind": kind,
        "mode": 0o644 if puts_back else None,
        "path": path,
        "sha256": sha256 or "0" * 64,
        "staged": f"{parent}.sabot-new-{token}-{name}",
    }
    built.update(overrides)
    return built


def plant_journal(
    vault: Path,
    record: dict[str, object],
    *,
    backup: bytes | list[bytes] = b"ATTACKER",
    signed: bool = True,
) -> Path:
    """Plant a journal as a write killed part-way leaves it, signed with this user's key.

    The journal names `vault`. `backup` is the bytes of `backup/0`, or a list holding one entry
    per backup file; an operation that records no digest gets that of its own backup file.
    `signed=False` leaves out the MAC, as a journal from elsewhere would be.
    """
    backups = [backup] if isinstance(backup, bytes) else backup
    document: dict[str, object] = {"vault": os.path.realpath(vault), **record}
    operations = document["operations"]
    assert isinstance(operations, list)
    for item in operations:
        if item["backup"] is not None and item["backup_sha256"] is None:
            name = str(item["backup"]).rsplit("/", 1)[-1]
            # A backup name a test bends (a path above the journal) is the first backup's.
            item["backup_sha256"] = sha(backups[int(name) if name.isdigit() else 0])
    if signed:
        document = sign(document, signing_key(vault))
    directory = vault / f".sabot-txn-{document['token']}"
    (directory / "backup").mkdir(parents=True)
    for number, data in enumerate(backups):
        (directory / "backup" / str(number)).write_bytes(data)
    (directory / "journal.json").write_text(json.dumps(document), encoding="utf-8")
    return directory


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
