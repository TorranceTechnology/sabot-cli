"""The `schema` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "schema"


def register(subparsers: Subparsers) -> None:
    """Add the `schema` subparser."""
    parser = subparsers.add_parser(NAME, help="show the schema a vault is written to")
    parser.add_argument("--type", help="show one node type")
    parser.add_argument("--edge", help="show one edge type")
    add_vault_option(parser, optional=True)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
