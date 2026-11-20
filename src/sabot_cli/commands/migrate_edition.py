"""The `migrate-edition` command."""

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._migrate_text import rewrite_note_text
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError
from sabot_cli.sources import (
    NoteMigration,
    SourceError,
    check_key,
    diff_editions,
    plan_note_migration,
    read_span_file,
)
from sabot_cli.vault import TextFileError, decode_text, encode_text
from sabot_cli.vault.transaction import Transaction, TransactionError

NAME = "migrate-edition"

_OUTCOME_NAMES = ("repointed", "stale-editorial", "stale-substantive", "stale-removed")


def register(subparsers: Subparsers) -> None:
    """Add the `migrate-edition` subparser."""
    parser = subparsers.add_parser(NAME, help="move citations to the successor edition")
    parser.add_argument("old_key", help="source key of the older edition")
    parser.add_argument("new_key", help="source key of the newer edition")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code.

    Every note is planned and rewritten in memory first, so a note that cannot be edited safely
    refuses the whole run before any file is written; the rewrites are then written together,
    all or none. A dry run writes nothing and takes no lock.
    """
    as_json = bool(args.json)
    if args.dry_run:
        return _migrate(args, None, as_json=as_json)
    try:
        with Transaction(args.vault) as transaction:
            return _migrate(args, transaction, as_json=as_json)
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)


def _migrate(args: argparse.Namespace, transaction: Transaction | None, *, as_json: bool) -> int:
    vault: Path = args.vault
    old_key, new_key = str(args.old_key), str(args.new_key)
    dry_run = transaction is None
    try:
        rules = load_rules(vault)
        check_key(old_key)
        check_key(new_key)
        if old_key == new_key:
            raise SourceError("editions-identical", "an edition cannot be migrated to itself")
        old = read_span_file(vault, old_key)
        new = read_span_file(vault, new_key)
        diff = diff_editions(old, new)
        loaded = rules.load_notes(vault)
        outcomes: dict[str, list[str]] = {name: [] for name in _OUTCOME_NAMES}
        unresolved: list[tuple[str, str]] = []
        edits: dict[str, bytes] = {}
        for note in sorted(loaded.notes.values(), key=lambda n: n.path.as_posix()):
            cited = note.properties.get("source")
            citations = [c for c in cited if isinstance(c, str)] if isinstance(cited, list) else []
            migration = plan_note_migration(note.id, citations, diff, old, new)
            if migration is None:
                continue
            unresolved += [(note.id, raw) for raw in migration.unresolved]
            _file_outcome(outcomes, migration)
            reason = migration.reason_to_write(note.properties)
            if not migration.repoints and reason is None:
                continue
            raw = (vault / note.path).read_bytes()
            before = decode_text(raw, note.path.name)
            after = rewrite_note_text(before, migration.repoints, reason)
            if after != before:
                edits[note.path.as_posix()] = encode_text(after, like=raw)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except (OSError, TextFileError):
        # The vault reader read this note moments ago, so only another program changing it
        # since can bring us here.
        return output.refuse(
            NAME,
            "migration-rewrite-invalid",
            "a note changed or became unreadable while it was being migrated",
            as_json=as_json,
        )
    if transaction is not None:
        for relative, data in edits.items():
            transaction.put(relative, data)
        transaction.commit()
    moved_to = {m.new for m in diff.moved}
    added = [locator for locator in diff.added if locator not in moved_to]
    for name in outcomes:
        outcomes[name].sort()
    changed = sorted(edits)
    unparsed = sorted(problem.path.as_posix() for problem in loaded.problems)
    if as_json:
        output.write_json(
            _document(
                old_key, new_key, dry_run, outcomes, added, changed, sorted(unresolved), unparsed
            )
        )
    else:
        output.write_text(
            _text(old_key, new_key, dry_run, outcomes, added, changed, unresolved, unparsed)
        )
    return int(ExitCode.OK)


def _file_outcome(outcomes: Mapping[str, list[str]], migration: NoteMigration) -> None:
    if migration.outcome == "clean":
        if migration.repoints:
            outcomes["repointed"].append(migration.note_id)
    else:
        outcomes[f"stale-{migration.outcome}"].append(migration.note_id)


def _document(
    old_key: str,
    new_key: str,
    dry_run: bool,
    outcomes: Mapping[str, Sequence[str]],
    added: Sequence[str],
    changed: Sequence[str],
    unresolved: Sequence[tuple[str, str]],
    unparsed: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "added": list(added),
        "changed": list(changed),
        "command": NAME,
        "counts": {name: len(ids) for name, ids in outcomes.items()},
        "dryRun": dry_run,
        "format": output.FORMAT_VERSION,
        "new": new_key,
        "old": old_key,
        "outcomes": {name: list(ids) for name, ids in outcomes.items()},
        "unparsed": list(unparsed),
        "unresolved": [{"note": note, "reference": raw} for note, raw in unresolved],
        "written": not dry_run and bool(changed),
    }


def _text(
    old_key: str,
    new_key: str,
    dry_run: bool,
    outcomes: Mapping[str, Sequence[str]],
    added: Sequence[str],
    changed: Sequence[str],
    unresolved: Sequence[tuple[str, str]],
    unparsed: Sequence[str],
) -> str:
    lines = [f"{old_key} -> {new_key}{' (dry run, nothing written)' if dry_run else ''}"]
    for name, ids in outcomes.items():
        lines.append(f"{name}: {len(ids)}")
    lines.append(f"added spans to extract: {len(added)}")
    lines.append(f"notes {'that would change' if dry_run else 'changed'}: {len(changed)}")
    sections: list[tuple[str, list[str]]] = [
        *((name, list(ids)) for name, ids in outcomes.items()),
        ("added spans", list(added)),
        ("unresolved citations", [f"{note}: {raw}" for note, raw in unresolved]),
        ("notes not read (their front matter does not parse; check them by hand)", list(unparsed)),
    ]
    for name, items in sections:
        if items:
            lines += ["", f"{name}:", *(f"  {item}" for item in items)]
    return "".join(f"{line}\n" for line in lines)
