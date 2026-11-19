"""The `search` command."""

import argparse

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "search"


def register(subparsers: Subparsers) -> None:
    """Add the `search` subparser."""
    parser = subparsers.add_parser(NAME, help="find notes by filters and full-text relevance")
    parser.add_argument("terms", nargs="*", help="words to rank by; a quoted argument is a phrase")
    parser.add_argument("--all", action="store_true", help="require every term")
    parser.add_argument("--type", action="append", default=[], metavar="T", help="note type filter")
    parser.add_argument("--status", action="append", default=[], metavar="S", help="status filter")
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="EXPR",
        help="property filter: key=value, key!=value, key~text, has:key, missing:key",
    )
    parser.add_argument("--fields", metavar="F,F", help="extra properties to include in each row")
    parser.add_argument(
        "--limit", type=int, metavar="N", help="maximum rows (default 20 with terms)"
    )
    parser.add_argument(
        "--snippets", action="store_true", help="include a matching snippet per row"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
