"""The `compile` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "compile"


def register(subparsers: Subparsers) -> None:
    """Add the `compile` subparser."""
    parser = subparsers.add_parser(NAME, help="write the machine graph")
    parser.add_argument("--out", type=Path, metavar="PATH", help="where to write the graph")
    parser.add_argument(
        "--check", action="store_true", help="fail if the written graph is out of date"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
