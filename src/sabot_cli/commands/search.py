"""The `search` command."""

import argparse
import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.commands._search_vault import load_searchable
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError
from sabot_cli.search import Hit, InvalidFilterError, full_text, query
from sabot_cli.vault import Note

NAME = "search"
DEFAULT_LIMIT = 20
_BASE_COLUMNS = ("id", "type", "status", "title", "path")
_RANKED_COLUMNS = ("rank", "score", "snippet")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def register(subparsers: Subparsers) -> None:
    """Add the `search` subparser."""
    parser = subparsers.add_parser(NAME, help="find notes by filters and full-text relevance")
    parser.add_argument(
        "terms",
        nargs="*",
        help="words to rank by in titles and bodies; an argument of several words is a phrase",
    )
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
        "--limit",
        type=_positive_int,
        metavar="N",
        help=f"maximum rows (default {DEFAULT_LIMIT} with terms, none without)",
    )
    parser.add_argument(
        "--snippets", action="store_true", help="include a matching snippet per row"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    terms: list[str] = args.terms
    if not (terms or args.type or args.status or args.where):
        return _usage("give at least one term or one of --type, --status and --where")
    fields = _field_names(args.fields)
    if overlap := [name for name in fields if name in {*_BASE_COLUMNS, *_RANKED_COLUMNS}]:
        return _usage(f"--fields names {overlap[0]!r}, which is already a column of every row")
    root: Path = args.vault
    try:
        vault, _ = load_searchable(root)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=bool(args.json))
    try:
        matched = query(vault, args.type, args.status, args.where)
    except InvalidFilterError as error:
        return _usage(str(error))
    if terms:
        subset = dataclasses.replace(vault, notes={note.id: note for note in matched})
        hits = full_text(subset, terms, args.all, args.limit or DEFAULT_LIMIT)
    else:
        capped = matched[: args.limit] if args.limit else matched
        hits = tuple(Hit(note, 0, 0.0, "") for note in capped)
    _report(hits, fields, ranked=bool(terms), snippets=args.snippets, as_json=bool(args.json))
    return int(ExitCode.OK)


def _usage(message: str) -> int:
    output.write_diagnostic(f"sabot {NAME}: {message}")
    return int(ExitCode.USAGE)


def _field_names(raw: str | None) -> tuple[str, ...]:
    names = (name.strip() for name in (raw or "").split(","))
    return tuple(dict.fromkeys(name for name in names if name))


def _report(
    hits: Sequence[Hit], fields: Sequence[str], *, ranked: bool, snippets: bool, as_json: bool
) -> None:
    rows = [_row(hit, fields, ranked=ranked, snippets=snippets) for hit in hits]
    if as_json:
        output.write_json({"command": NAME, "format": output.FORMAT_VERSION, "results": list(rows)})
        return
    headers = [
        *(_RANKED_COLUMNS[:2] if ranked else ()),
        *_BASE_COLUMNS,
        *fields,
        *(_RANKED_COLUMNS[2:] if ranked and snippets else ()),
    ]
    output.write_text(
        output.format_table(headers, [[_cell(row.get(name)) for name in headers] for row in rows])
    )


def _row(hit: Hit, fields: Sequence[str], *, ranked: bool, snippets: bool) -> dict[str, JsonValue]:
    note = hit.note
    row: dict[str, JsonValue] = {
        "id": note.id,
        "path": note.path.as_posix(),
        "status": _property(note, "status"),
        "title": _property(note, "title"),
        "type": _property(note, "type"),
    }
    row.update({name: _json_value(note.properties.get(name)) for name in fields})
    if ranked:
        row["rank"] = hit.rank
        row["score"] = hit.score
        if snippets:
            row["snippet"] = hit.snippet
    return row


def _property(note: Note, name: str) -> JsonValue:
    return _json_value(note.properties.get(name))


def _json_value(value: object) -> JsonValue:
    """A front-matter value as JSON; floats become text because only scores are floats."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _cell(value: JsonValue) -> str:
    """One table cell: a single line, with `-` for a value that is absent."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, str):
        return " ".join(value.split()) or "-"
    if isinstance(value, list):
        return ", ".join(_cell(item) for item in value) or "-"
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)
