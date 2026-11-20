"""The `segment` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands import _source_note
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError
from sabot_cli.sources import (
    CheckResult,
    SegmentResult,
    SourceError,
    check_key,
    read_text_file,
    segment,
    span_file_relative,
)
from sabot_cli.vault import TextFileError, decode_text, encode_text
from sabot_cli.vault.paths import relative_within
from sabot_cli.vault.transaction import Transaction, TransactionError, atomic_write

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
    parser.add_argument(
        "--boundaries",
        type=Path,
        metavar="FILE",
        help="the proposed spans to check and hash; required by, and only with, --mode boundaries",
    )
    parser.add_argument(
        "--emit-boundaries", type=Path, metavar="FILE", help="write the result as a boundaries file"
    )
    parser.add_argument(
        "--numbering",
        choices=("dotted", "dotted-letter"),
        default="dotted",
        help="numbered-mode style (default: dotted)",
    )
    parser.add_argument(
        "--skip-pages",
        type=int,
        default=0,
        metavar="N",
        help=(
            "leading pages to skip in numbered mode (default: 0). Pages are counted by form "
            "feed; text without form feeds is one page and nothing is skipped"
        ),
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    try:
        with Transaction(args.vault) as transaction:
            return _segment(args, transaction, as_json=as_json)
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)


def _segment(args: argparse.Namespace, transaction: Transaction, *, as_json: bool) -> int:
    vault: Path = args.vault
    key, mode = str(args.source), str(args.mode)
    emit: Path | None = args.emit_boundaries
    if emit is not None and relative_within(emit, vault) is not None:
        return output.refuse(
            NAME,
            "emit-inside-vault",
            "--emit-boundaries names a path inside the vault; write it outside",
            as_json=as_json,
        )
    try:
        load_rules(vault)
        check_key(key)
        note = _source_note.note_path(vault, key)
        if not note.is_file():
            raise SourceError(
                "source-not-ingested", f"the vault has no source note for {key!r}; run ingest first"
            )
        text = read_text_file(args.text_file)
        boundaries = None if args.boundaries is None else read_text_file(args.boundaries)
        result = segment(
            text,
            key,
            args.mode,
            args.numbering,
            args.skip_pages,
            boundaries=boundaries,
        )
        updated = _updated_note(note, mode) if result.ok else None
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    if updated is not None:
        _plan_writes(transaction, key, mode, result, updated)
        transaction.commit()
    try:
        emitted = _emit(emit, result)
    except OSError as error:
        return output.refuse(
            NAME, "write-failed", f"a file could not be written: {error.strerror}", as_json=as_json
        )
    _report(key, mode, result, emitted=emitted, as_json=as_json)
    return int(ExitCode.OK if result.ok else ExitCode.FINDINGS)


def _emit(target: Path | None, result: SegmentResult) -> bool:
    """Write the boundaries file when asked, even for a rule-based result whose checks failed."""
    text = result.boundaries_text()
    if target is None or text is None:
        return False
    atomic_write(target, text.encode("utf-8"))
    return True


def _updated_note(note: Path, mode: str) -> bytes:
    """The source note with its `segmenter` set, decoded as the vault reader decodes a note."""
    raw = note.read_bytes()
    try:
        text = decode_text(raw, note.name)
    except TextFileError as error:
        raise SourceError("source-note-invalid", "the source note is not valid UTF-8") from error
    return encode_text(_source_note.with_segmenter(text, mode), like=raw)


def _plan_writes(
    transaction: Transaction, key: str, mode: str, result: SegmentResult, note: bytes
) -> None:
    transaction.put(span_file_relative(key), result.span_file_text().encode("utf-8"))
    boundaries = result.boundaries_text()
    boundaries_file = span_file_relative(key, boundaries=True)
    if mode == "boundaries" and boundaries is not None:
        transaction.put(boundaries_file, boundaries.encode("utf-8"))
    else:
        # A boundaries file left by an earlier run would describe spans that no longer exist.
        transaction.delete(boundaries_file)
    transaction.put(_source_note.note_relative(key), note)


def _check_json(check: CheckResult) -> dict[str, JsonValue]:
    return {
        "failures": list(check.failures),
        "name": check.name,
        "status": "passed" if check.passed else "failed",
    }


def _report(key: str, mode: str, result: SegmentResult, *, emitted: bool, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "boundariesEmitted": emitted,
                "checks": [_check_json(check) for check in result.checks],
                "command": NAME,
                "format": output.FORMAT_VERSION,
                "mode": mode,
                "path": span_file_relative(key),
                "sentences": result.sentence_count,
                "source": key,
                "spans": result.span_count,
                "written": result.ok,
            }
        )
        return
    lines = _text_lines(result)
    output.write_text("".join(f"{line}\n" for line in lines))
    if not result.ok:
        output.write_diagnostic("sabot segment: a check failed; no span file was written")


def _text_lines(result: SegmentResult) -> Sequence[str]:
    lines = [f"spans: {result.span_count}", f"sentences: {result.sentence_count}"]
    for check in result.checks:
        lines.append(f"check {check.name}: {'passed' if check.passed else 'failed'}")
        lines += [f"  {failure}" for failure in check.failures]
    return lines
