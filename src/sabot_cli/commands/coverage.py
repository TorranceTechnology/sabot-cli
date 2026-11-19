"""The `coverage` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError, load_ruleset
from sabot_cli.rules._checks._spans_shim import (
    SpanFileError,
    SpanIndex,
    SpanRecord,
    read_spans,
)
from sabot_cli.rules.coverage import CoverageReport, SourceCoverage, coverage
from sabot_cli.rules.ruleset import RulesetSource
from sabot_cli.vault import load_vault

NAME = "coverage"
SPANS_DIRECTORY = "spans"


def register(subparsers: Subparsers) -> None:
    """Add the `coverage` subparser."""
    parser = subparsers.add_parser(NAME, help="check that every in-scope span is cited")
    parser.add_argument("--source", metavar="KEY", help="limit the report to one source")
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
        ruleset = load_ruleset(root)
        spans = _read_span_files(root, _requested_keys(ruleset.sources, args.source))
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    # Citations are read as written, so the link properties a schema would name are not needed.
    report = coverage(load_vault(root, frozenset()), ruleset, spans)
    _report(report, as_json=as_json)
    return int(ExitCode.OK if report.complete else ExitCode.FINDINGS)


def _requested_keys(sources: Sequence[RulesetSource], requested: str | None) -> tuple[str, ...]:
    registered = tuple(source.key for source in sources)
    if requested is None:
        return registered
    if requested not in registered:
        raise RulesetError("source-unknown", f"the source key {requested!r} is not registered")
    return (requested,)


def _read_span_files(root: Path, keys: Sequence[str]) -> SpanIndex:
    """One index holding the records of each requested source, in key order."""
    records: list[SpanRecord] = []
    for key in keys:
        path = root / SPANS_DIRECTORY / f"{key}.jsonl"
        if not path.is_file():
            raise RulesetError(
                "span-file-missing", f"the source {key!r} has no {SPANS_DIRECTORY}/{key}.jsonl"
            )
        try:
            records.extend(read_spans(path).records)
        except SpanFileError as error:
            raise RulesetError("span-file-invalid", str(error)) from error
    return SpanIndex(tuple(records))


def _report(report: CoverageReport, *, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "complete": report.complete,
                "format": output.FORMAT_VERSION,
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
