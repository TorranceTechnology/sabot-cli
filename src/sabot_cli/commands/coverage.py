"""The `coverage` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "coverage"


def register(subparsers: Subparsers) -> None:
    """Add the `coverage` subparser."""
    parser = subparsers.add_parser(NAME, help="check that every in-scope span is cited")
    parser.add_argument("--source", metavar="KEY", help="limit the report to one source")
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
