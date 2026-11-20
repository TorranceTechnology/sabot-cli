"""The `diff-editions` command."""

import argparse
from collections.abc import Mapping
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError
from sabot_cli.sources import (
    EditionDiff,
    SourceError,
    SpanIndex,
    check_key,
    diff_editions,
    plan_note_migration,
    read_edition_ranges,
    read_span_file,
    read_text_file,
)
from sabot_cli.vault import Vault

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
    """Execute the command and return its exit code.

    Differences are the report and not a failure, so the exit code is 0 unless input is refused.
    """
    as_json = bool(args.json)
    vault: Path = args.vault
    old_key, new_key = str(args.old_key), str(args.new_key)
    scope: list[str] = list(args.scope)
    try:
        rules = load_rules(vault)
        check_key(old_key)
        check_key(new_key)
        if old_key == new_key:
            raise SourceError("editions-identical", "an edition cannot be compared with itself")
        old = read_span_file(vault, old_key)
        new = read_span_file(vault, new_key)
        old_text = None if args.old_text is None else read_text_file(args.old_text)
        new_text = None if args.new_text is None else read_text_file(args.new_text)
        diff = diff_editions(
            old,
            new,
            scope,
            old_text,
            new_text,
            old_ranges=read_edition_ranges(vault, old_key),
            new_ranges=read_edition_ranges(vault, new_key),
        )
        affected = _affected_notes(rules.load_notes(vault), diff, old, new)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    texts_given = old_text is not None
    if as_json:
        output.write_json(_document(diff, scope, affected, texts_given=texts_given))
    else:
        output.write_text(_text(diff, affected))
    return int(ExitCode.OK)


def _affected_notes(vault: Vault, diff: EditionDiff, old: SpanIndex, new: SpanIndex) -> list[str]:
    """Ids of notes citing an old span that is substantive, removed or moved: the re-review set."""
    found: list[str] = []
    for note in sorted(vault.notes.values(), key=lambda n: n.id):
        cited = note.properties.get("source")
        citations = [c for c in cited if isinstance(c, str)] if isinstance(cited, list) else []
        migration = plan_note_migration(note.id, citations, diff, old, new)
        if migration is not None and migration.affected:
            found.append(note.id)
    return found


def _document(
    diff: EditionDiff, scope: list[str], affected: list[str], *, texts_given: bool
) -> dict[str, JsonValue]:
    moved: list[JsonValue] = [{"from": m.old, "to": m.new} for m in diff.moved]
    paired: list[JsonValue] = [
        {"changedWords": p.changed_words, "new": p.new, "old": p.old} for p in diff.paired
    ]
    changed: Mapping[str, JsonValue] = dict(diff.changed_words)
    return {
        "affectedNotes": list(affected),
        "changedWords": changed,
        "classes": {
            "added": list(diff.added),
            "editorial": list(diff.editorial),
            "removed": list(diff.removed),
            "substantive": list(diff.substantive),
            "unchanged": list(diff.unchanged),
        },
        "command": NAME,
        "counts": _counts(diff),
        "format": output.FORMAT_VERSION,
        "moved": moved,
        "new": diff.new_key,
        "old": diff.old_key,
        "paired": paired,
        "scope": sorted(scope),
        "textsGiven": texts_given,
    }


def _counts(diff: EditionDiff) -> dict[str, JsonValue]:
    return {
        "added": len(diff.added),
        "editorial": len(diff.editorial),
        "moved": len(diff.moved),
        "paired": len(diff.paired),
        "removed": len(diff.removed),
        "substantive": len(diff.substantive),
        "unchanged": len(diff.unchanged),
    }


def _text(diff: EditionDiff, affected: list[str]) -> str:
    lines = [f"{diff.old_key} -> {diff.new_key}"]
    for name, count in _counts(diff).items():
        lines.append(f"{name}: {count}")
    sections: list[tuple[str, list[str]]] = [
        ("editorial", list(diff.editorial)),
        (
            "substantive",
            [
                f"{loc} ({diff.changed_words[loc]} words)" if loc in diff.changed_words else loc
                for loc in diff.substantive
            ],
        ),
        ("added", list(diff.added)),
        ("removed", list(diff.removed)),
        ("moved", [f"{m.old} -> {m.new}" for m in diff.moved]),
        ("paired", [f"{p.old} -> {p.new} ({p.changed_words} words)" for p in diff.paired]),
        ("affected notes", list(affected)),
    ]
    for name, items in sections:
        if items:
            lines += ["", f"{name}:", *(f"  {item}" for item in items)]
    return "".join(f"{line}\n" for line in lines)
