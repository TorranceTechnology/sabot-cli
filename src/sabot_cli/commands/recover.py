"""The `recover` command."""

import argparse
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.vault.transaction import (
    Discard,
    Recovery,
    TransactionError,
    discard,
    recover,
)

NAME = "recover"


def register(subparsers: Subparsers) -> None:
    """Add the `recover` subparser."""
    parser = subparsers.add_parser(
        NAME,
        help="roll back a write that was killed, and clear what it left",
        description=(
            "Rolls back every unfinished write to its state before the run, removes staged "
            "files and clears a stale write lock. It never completes a write. It applies a "
            "journal only when the journal is signed with this user's journal key, names this "
            "vault and holds the backups it records; any other journal is refused "
            "(journal-unverified) and nothing is changed. It changes a file only when the "
            "file holds what the journal says it should, and changes nothing when any "
            "operation cannot be checked. It refuses while a sabot process of this host is "
            "writing the vault and while the lock was left by another host or cannot be read; "
            "--force overrides that once you know no write is running. --discard removes "
            "journals and staged files without applying anything."
        ),
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help=(
            "remove journal directories (.sabot-txn-*) and staged files (.sabot-new-*) and "
            "change no other file, applying no journal (default: roll back verified journals)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "clear a lock held by a running process or left by another host; it covers the "
            "lock only and never makes an unverified journal apply (default: refuse)"
        ),
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
        if args.discard:
            removal = discard(root, force=bool(args.force))
            return _report_discard(removal, as_json=as_json)
        recovery = recover(root, force=bool(args.force))
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    if as_json:
        output.write_json(
            {
                "clearedLock": recovery.cleared_lock,
                "command": NAME,
                "format": output.FORMAT_VERSION,
                "removedStaged": list(recovery.removed_staged),
                "rolledBack": list(recovery.rolled_back),
            }
        )
    else:
        output.write_text(_text(recovery))
    return int(ExitCode.OK)


def _report_discard(removal: Discard, *, as_json: bool) -> int:
    if as_json:
        output.write_json(
            {
                "clearedLock": removal.cleared_lock,
                "command": NAME,
                "discard": True,
                "format": output.FORMAT_VERSION,
                "removedJournals": list(removal.removed_journals),
                "removedStaged": list(removal.removed_staged),
            }
        )
    else:
        lines = [f"removed journal: {name}" for name in removal.removed_journals]
        lines += [f"removed staged file: {path}" for path in removal.removed_staged]
        if removal.cleared_lock:
            lines.append("cleared the write lock")
        output.write_text("".join(f"{line}\n" for line in lines) or "nothing to discard\n")
    return int(ExitCode.OK)


def _text(recovery: Recovery) -> str:
    lines = [f"rolled back: {name}" for name in recovery.rolled_back]
    lines += [f"removed staged file: {path}" for path in recovery.removed_staged]
    if recovery.cleared_lock:
        lines.append("cleared the write lock")
    return "".join(f"{line}\n" for line in lines) or "nothing to recover\n"
