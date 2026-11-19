"""The `segment` command."""

import argparse
from pathlib import Path

from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
    refuse_not_implemented,
)

NAME = "segment"


def register(subparsers: Subparsers) -> None:
    """Add the `segment` subparser."""
    parser = subparsers.add_parser(NAME, help="turn extracted text into a span file")
    parser.add_argument("text_file", type=Path, help="extracted text to segment")
    parser.add_argument("--source", required=True, metavar="KEY", help="source key")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("numbered", "headings", "sentences", "boundaries"),
        help="segmentation mode",
    )
    parser.add_argument("--boundaries", type=Path, metavar="FILE", help="span boundaries to check")
    parser.add_argument(
        "--emit-boundaries", type=Path, metavar="FILE", help="write the result as a boundaries file"
    )
    parser.add_argument(
        "--numbering",
        choices=("dotted", "dotted-letter"),
        default="dotted",
        help="numbered-mode style",
    )
    parser.add_argument(
        "--skip-pages", type=int, default=1, metavar="N", help="leading pages to skip"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    return refuse_not_implemented(NAME, args)
