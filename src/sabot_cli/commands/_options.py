"""Options that more than one command declares."""

import argparse
from pathlib import Path

# argparse exposes no public name for the object add_subparsers returns.
type Subparsers = argparse._SubParsersAction[argparse.ArgumentParser]


def add_json_option(parser: argparse.ArgumentParser) -> None:
    """Declare --json, which switches stdout to one canonical JSON document."""
    parser.add_argument("--json", action="store_true", help="write one JSON document to stdout")


def add_vault_option(parser: argparse.ArgumentParser, *, optional: bool = False) -> None:
    """Declare --vault; it defaults to the current directory unless `optional`."""
    parser.add_argument(
        "--vault",
        type=Path,
        default=None if optional else Path("."),
        metavar="PATH",
        help="vault directory (default: the current directory)"
        if not optional
        else "vault directory whose extensions are merged in (default: none)",
    )
