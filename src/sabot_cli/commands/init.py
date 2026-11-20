"""The `init` command."""

import argparse
import re
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.rules import RULESET_FILE
from sabot_cli.sources import SPANS_DIRECTORY
from sabot_cli.vault.transaction import LOCK_FILE, Transaction, TransactionError

NAME = "init"

_RULESET_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_FOLDERS: tuple[str, ...] = (
    SPANS_DIRECTORY,
    "sources",
    "clauses",
    "terms",
    "variants",
    "parameters",
    "rulings",
    "examples",
    "questions",
    "relations",
    "model/zones",
    "model/phases",
    "model/procedures",
    "model/actions",
    "model/decisions",
    "model/keyword-actions",
    "model/card-types",
    "model/characteristics",
    "model/keywords",
    "model/rule-processes",
    "model/windows",
    "model/patterns",
    "build/constructs",
    "build/obligations",
)


def register(subparsers: Subparsers) -> None:
    """Add the `init` subparser."""
    parser = subparsers.add_parser(NAME, help="create an empty vault skeleton")
    parser.add_argument("directory", type=Path, help="directory to create the vault in")
    parser.add_argument("--ruleset", required=True, help="ruleset id recorded in _ruleset.yaml")
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    directory: Path = args.directory
    ruleset_id = str(args.ruleset)
    if not _RULESET_ID.match(ruleset_id):
        return output.refuse(
            NAME,
            "ruleset-id-invalid",
            "the ruleset id must be lowercase letters, digits and hyphens, starting with a letter",
            as_json=as_json,
        )
    if directory.exists() and not directory.is_dir():
        return output.refuse(
            NAME, "not-a-directory", "the path exists and is not a directory", as_json=as_json
        )
    if directory.is_dir() and any(directory.iterdir()):
        return output.refuse(
            NAME, "directory-not-empty", "the directory is not empty", as_json=as_json
        )
    try:
        with Transaction(directory, create_root=True) as transaction:
            if any(entry.name != LOCK_FILE for entry in directory.iterdir()):
                return output.refuse(
                    NAME, "directory-not-empty", "the directory is not empty", as_json=as_json
                )
            created = _plan(transaction, ruleset_id)
            transaction.commit()
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "created": list(created),
                "format": output.FORMAT_VERSION,
                "ruleset": ruleset_id,
            }
        )
    else:
        output.write_text("".join(f"{path}\n" for path in created))
    return int(ExitCode.OK)


def _plan(transaction: Transaction, ruleset_id: str) -> tuple[str, ...]:
    """Plan the skeleton; return paths relative to the vault, sorted, folders ending in `/`."""
    created: set[str] = {RULESET_FILE}
    for folder in _FOLDERS:
        transaction.make_directory(folder)
        parts = folder.split("/")
        created.update("/".join(parts[: depth + 1]) + "/" for depth in range(len(parts)))
    transaction.put(RULESET_FILE, _ruleset_text(ruleset_id).encode("utf-8"))
    return tuple(sorted(created))


def _ruleset_text(ruleset_id: str) -> str:
    return f"ruleset: {ruleset_id}\nschemaVersion: 1\nsources: []\nscope: {{}}\nextensions: {{}}\n"
