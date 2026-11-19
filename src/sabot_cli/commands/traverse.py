"""The `traverse` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "traverse"


def register(subparsers: Subparsers) -> None:
    """Add the `traverse` subparser."""
    parser = subparsers.add_parser(NAME, help="list notes reachable from one note")
    parser.add_argument("id", help="id of the note to start from")
    parser.add_argument(
        "--edge", action="append", default=[], metavar="E", help="edge to follow (default: all)"
    )
    parser.add_argument(
        "--direction", choices=("out", "in", "both"), default="out", help="edge direction to follow"
    )
    parser.add_argument("--depth", type=int, default=1, metavar="N", help="hops to follow, 1 to 10")
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
