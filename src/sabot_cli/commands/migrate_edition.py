"""The `migrate-edition` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "migrate-edition"


def register(subparsers: Subparsers) -> None:
    """Add the `migrate-edition` subparser."""
    parser = subparsers.add_parser(NAME, help="move citations to the successor edition")
    parser.add_argument("old_key", help="source key of the older edition")
    parser.add_argument("new_key", help="source key of the newer edition")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
