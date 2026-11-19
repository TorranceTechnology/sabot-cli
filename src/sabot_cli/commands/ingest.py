"""The `ingest` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "ingest"


def register(subparsers: Subparsers) -> None:
    """Add the `ingest` subparser."""
    parser = subparsers.add_parser(NAME, help="register one source edition as a source note")
    parser.add_argument("file", type=Path, help="source file to register")
    parser.add_argument("--key", required=True, help="source key")
    parser.add_argument("--label", required=True, help="short label used in citations")
    parser.add_argument("--title", required=True, help="source title")
    parser.add_argument("--edition", required=True, help="edition text")
    parser.add_argument("--retrieved", required=True, metavar="YYYY-MM-DD", help="retrieval date")
    parser.add_argument("--url", help="where the file came from")
    parser.add_argument("--effective", metavar="YYYY-MM-DD", help="date the edition took effect")
    parser.add_argument("--format", choices=("pdf", "html", "text"), help="source format")
    parser.add_argument(
        "--kind",
        choices=("edition", "errata", "ruling", "faq"),
        default="edition",
        help="source kind",
    )
    parser.add_argument("--supersedes", metavar="KEY", help="key of the edition this one replaces")
    parser.add_argument("--amends", metavar="KEY", help="key of the edition this one amends")
    parser.add_argument("--archive-url", help="archived snapshot of the source")
    parser.add_argument("--extractor", metavar="NAME", help="text extractor to use")
    parser.add_argument(
        "--store", type=Path, metavar="DIR", help="private store to copy the file into"
    )
    parser.add_argument(
        "--text-out", type=Path, metavar="PATH", help="where to write extracted text"
    )
    parser.add_argument(
        "--verify", action="store_true", help="compare the file with the registered hash"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
