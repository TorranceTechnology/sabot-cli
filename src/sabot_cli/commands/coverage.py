"""The `coverage` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import CoverageReport, RulesetError, RulesetSource, SourceCoverage, coverage
from sabot_cli.sources import SourceError, SpanIndex, SpanRecord, read_span_file

NAME = "coverage"


def register(subparsers: Subparsers) -> None:
    """Add the `coverage` subparser."""
    parser = subparsers.add_parser(NAME, help="check that every in-scope span is cited")
    parser.add_argument(
        "--source",
        metavar="KEY",
        help=(
            "measure this source only, even when another source supersedes it "
            "(default: every registered source that no other registered source supersedes)"
        ),
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
        rules = load_rules(root)
        keys, skipped = _requested_keys(rules.ruleset.sources, args.source)
        spans = _read_span_files(root, keys)
    except (RulesetError, SourceError) as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    report = coverage(rules.load_notes(root), rules.ruleset, spans, skipped)
    _report(report, as_json=as_json)
    return int(ExitCode.OK if report.complete else ExitCode.FINDINGS)


def _requested_keys(
    sources: Sequence[RulesetSource], requested: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The keys to measure and, separately, the superseded ones left out of the default run."""
    registered = tuple(source.key for source in sources)
    if requested is None:
        # An edition that a later one replaces is finished work: its citations moved on with
        # `migrate-edition`, so measuring it would keep the report failing for good.
        superseded = {source.supersedes for source in sources if source.supersedes}
        measured = tuple(key for key in registered if key not in superseded)
        return measured, tuple(key for key in registered if key in superseded)
    if requested not in registered:
        raise RulesetError("source-unknown", f"the source key {requested!r} is not registered")
    return (requested,), ()


def _read_span_files(root: Path, keys: Sequence[str]) -> SpanIndex:
    """One index holding the records of each requested source, in key order."""
    records: list[SpanRecord] = []
    for key in keys:
        records.extend(read_span_file(root, key).records)
    return SpanIndex(tuple(records))


def _report(report: CoverageReport, *, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "complete": report.complete,
                "format": output.FORMAT_VERSION,
                "skippedSuperseded": list(report.skipped_superseded),
                "sources": [_source_json(source) for source in report.sources],
            }
        )
        return
    summary = [
        [
            source.key,
            str(source.in_scope),
            str(source.cited),
            str(len(source.uncited)),
            str(len(source.partial)),
        ]
        for source in report.sources
    ]
    text = output.format_table(["source", "in scope", "cited", "uncited", "partial"], summary)
    for source in report.sources:
        text += "".join(f"uncited {span}\n" for span in source.uncited)
        text += "".join(
            f"partial {part.span} missing {' '.join(part.missing)}\n" for part in source.partial
        )
        notes = " ".join(f"{status}={count}" for status, count in source.citing_notes.items())
        text += f"citing notes {source.key} {notes or 'none'}\n"
    text += "".join(f"skipped {key} (superseded)\n" for key in report.skipped_superseded)
    output.write_text(text)


def _source_json(source: SourceCoverage) -> dict[str, JsonValue]:
    return {
        "cited": source.cited,
        "citingNotes": dict(source.citing_notes),
        "inScope": source.in_scope,
        "key": source.key,
        "partiallyCited": [
            {"missing": list(part.missing), "span": part.span} for part in source.partial
        ],
        "uncited": list(source.uncited),
    }
