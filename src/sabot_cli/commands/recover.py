"""The `recover` command."""

import argparse
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.vault.transaction import Recovery, TransactionError, recover

NAME = "recover"


def register(subparsers: Subparsers) -> None:
    """Add the `recover` subparser."""
    parser = subparsers.add_parser(
        NAME,
        help="roll back a write that was killed, and clear what it left",
        description=(
            "Rolls back every unfinished write to its state before the run, removes staged "
            "files and clears the write lock. It never completes a write. It changes a file "
            "only when the file holds what the journal says it should, and changes nothing "
            "when any operation cannot be checked. It refuses while a sabot process of this "
            "host is writing the vault, while the lock was left by another host or cannot be "
            "read, and for a journal written on another host; --force overrides these once "
            "you know no write is running. Review a journal in a vault that came from "
            "elsewhere before recovering it."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "clear a lock held by a running process or left by another host, and roll back a "
            "journal written on another host (default: refuse)"
        ),
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
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


def _text(recovery: Recovery) -> str:
    lines = [f"rolled back: {name}" for name in recovery.rolled_back]
    lines += [f"removed staged file: {path}" for path in recovery.removed_staged]
    if recovery.cleared_lock:
        lines.append("cleared the write lock")
    return "".join(f"{line}\n" for line in lines) or "nothing to recover\n"
