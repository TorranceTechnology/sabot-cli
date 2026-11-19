"""The `init` command."""

import argparse
import re
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.rules.ruleset import RULESET_FILE

NAME = "init"

_RULESET_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_FOLDERS: tuple[str, ...] = (
    "spans",
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
    created = _create(directory, ruleset_id)
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


def _create(directory: Path, ruleset_id: str) -> tuple[str, ...]:
    """Write the skeleton; return paths relative to `directory`, sorted, folders ending in `/`."""
    created: set[str] = {RULESET_FILE}
    for folder in _FOLDERS:
        (directory / folder).mkdir(parents=True, exist_ok=True)
        parts = folder.split("/")
        created.update("/".join(parts[: depth + 1]) + "/" for depth in range(len(parts)))
    (directory / RULESET_FILE).write_bytes(_ruleset_text(ruleset_id).encode("utf-8"))
    return tuple(sorted(created))


def _ruleset_text(ruleset_id: str) -> str:
    return f"ruleset: {ruleset_id}\nschemaVersion: 1\nsources: []\nscope: {{}}\nextensions: {{}}\n"
