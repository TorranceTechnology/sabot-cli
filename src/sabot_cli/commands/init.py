"""The `init` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    refuse_not_implemented,
)

NAME = "init"


def register(subparsers: Subparsers) -> None:
    """Add the `init` subparser."""
    parser = subparsers.add_parser(NAME, help="create an empty vault skeleton")
    parser.add_argument("directory", type=Path, help="directory to create the vault in")
    parser.add_argument("--ruleset", required=True, help="ruleset id recorded in _ruleset.yaml")
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
