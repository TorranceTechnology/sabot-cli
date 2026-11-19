"""The `overlap` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "overlap"


def register(subparsers: Subparsers) -> None:
    """Add the `overlap` subparser."""
    parser = subparsers.add_parser(NAME, help="detect copied source wording in note bodies")
    parser.add_argument("paths", nargs="*", type=Path, help="notes to check (default: every note)")
    parser.add_argument(
        "--source-text", required=True, type=Path, metavar="PATH", help="extracted source text"
    )
    parser.add_argument(
        "--fail-at", type=int, default=10, metavar="WORDS", help="run length that fails"
    )
    parser.add_argument(
        "--warn-at", type=int, default=8, metavar="WORDS", help="run length that warns"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
