"""The `traverse` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.commands._search_vault import known_edge_names, load_searchable
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError
from sabot_cli.search import Hop, Reached, UnknownNoteError, traverse

NAME = "traverse"
MAX_DEPTH = 10


def _depth(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if not 1 <= value <= MAX_DEPTH:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_DEPTH}")
    return value


def register(subparsers: Subparsers) -> None:
    """Add the `traverse` subparser."""
    parser = subparsers.add_parser(NAME, help="list notes reachable from one note")
    parser.add_argument("id", help="id of the note to start from")
    parser.add_argument(
        "--edge", action="append", default=[], metavar="E", help="edge to follow (default: all)"
    )
    parser.add_argument(
        "--direction",
        choices=("out", "in", "both"),
        default="out",
        help="edge direction to follow (default: out)",
    )
    parser.add_argument(
        "--depth",
        type=_depth,
        default=1,
        metavar="N",
        help=f"hops to follow, 1 to {MAX_DEPTH} (default: 1)",
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
        vault, edges = load_searchable(root)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    unknown = sorted(set(args.edge) - known_edge_names(edges))
    if unknown:
        return output.refuse(
            NAME, "edge-unknown", f"no edge is named {unknown[0]!r}", as_json=as_json
        )
    try:
        reached = traverse(
            vault,
            edges,
            args.id,
            args.direction,
            args.depth,
            frozenset(args.edge) if args.edge else None,
        )
    except UnknownNoteError:
        return output.refuse(
            NAME, "note-unknown", f"no note has the id {args.id!r}", as_json=as_json
        )
    _report(reached, as_json=as_json)
    return int(ExitCode.OK)


def _report(reached: Sequence[Reached], *, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "format": output.FORMAT_VERSION,
                "results": [_row(item) for item in reached],
            }
        )
        return
    rows = [
        [
            str(item.distance),
            item.id,
            _text_property(item, "type"),
            _text_property(item, "title"),
            " ".join(_hop_text(hop) for hop in item.via),
        ]
        for item in reached
    ]
    output.write_text(output.format_table(["distance", "id", "type", "title", "via"], rows))


def _row(item: Reached) -> dict[str, JsonValue]:
    return {
        "distance": item.distance,
        "id": item.id,
        "title": _property(item, "title"),
        "type": _property(item, "type"),
        "via": [{"edge": hop.edge, "from": hop.source, "to": hop.target} for hop in item.via],
    }


def _property(item: Reached, name: str) -> str | None:
    """A text property of the note, or None for a dangling target or a non-text value."""
    value = None if item.note is None else item.note.properties.get(name)
    return value if isinstance(value, str) else None


def _text_property(item: Reached, name: str) -> str:
    return " ".join((_property(item, name) or "-").split())


def _hop_text(hop: Hop) -> str:
    return f"{hop.source} -{hop.edge}-> {hop.target}"
