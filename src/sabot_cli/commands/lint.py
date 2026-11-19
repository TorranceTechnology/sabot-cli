"""The `lint` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "lint"


def register(subparsers: Subparsers) -> None:
    """Add the `lint` subparser."""
    parser = subparsers.add_parser(NAME, help="check a vault against schema v1")
    parser.add_argument(
        "--strict", action="store_true", help="treat build-readiness warnings as errors"
    )
    parser.add_argument(
        "--list-classes", action="store_true", help="list every lint class and exit"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
