"""The `diff-editions` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "diff-editions"


def register(subparsers: Subparsers) -> None:
    """Add the `diff-editions` subparser."""
    parser = subparsers.add_parser(NAME, help="classify span changes between two editions")
    parser.add_argument("old_key", help="source key of the older edition")
    parser.add_argument("new_key", help="source key of the newer edition")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="PREFIX",
        help="limit to locators with this prefix",
    )
    parser.add_argument(
        "--old-text", type=Path, metavar="PATH", help="extracted text of the older edition"
    )
    parser.add_argument(
        "--new-text", type=Path, metavar="PATH", help="extracted text of the newer edition"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
