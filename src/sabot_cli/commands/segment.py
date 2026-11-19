"""The `segment` command."""

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands import _source_note
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError, load_ruleset
from sabot_cli.sources import SOURCE_KEY_PATTERN, CheckResult, SegmentResult, SourceError, segment

NAME = "segment"

_KEY = re.compile(SOURCE_KEY_PATTERN)


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
        help="numbered-mode style",
    )
    parser.add_argument(
        "--skip-pages",
        type=int,
        default=1,
        metavar="N",
        help="leading pages to skip in numbered mode (default: 1, the title page)",
    )
    add_vault_option(parser)
    add_json_option(parser)


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as error:
        raise SourceError(
            "file-unreadable", f"{path.name} cannot be read: {error.strerror}"
        ) from error
    except UnicodeDecodeError as error:
        raise SourceError("text-undecodable", f"{path.name} is not valid UTF-8") from error


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    vault: Path = args.vault
    key, mode = str(args.source), str(args.mode)
    try:
        load_ruleset(vault)
        if not _KEY.match(key):
            raise SourceError(
                "source-key-invalid", "a source key is lowercase letters, digits, . and -"
            )
        note = _source_note.note_path(vault, key)
        if not note.is_file():
            raise SourceError(
                "source-not-ingested", f"the vault has no source note for {key!r}; run ingest first"
            )
        text = _read_text(args.text_file)
        boundaries = None if args.boundaries is None else _read_text(args.boundaries)
        result = segment(
            text,
            key,
            args.mode,
            args.numbering,
            args.skip_pages,
            boundaries=boundaries,
        )
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    emitted = _emit(args.emit_boundaries, result)
    if result.ok:
        _write_results(vault, note, key, mode, result)
    _report(key, mode, result, emitted=emitted, as_json=as_json)
    return int(ExitCode.OK if result.ok else ExitCode.FINDINGS)


def _emit(target: Path | None, result: SegmentResult) -> bool:
    """Write the boundaries file when asked, even for a failed result, so it can be repaired."""
    text = result.boundaries_text()
    if target is None or text is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return True


def _write_results(vault: Path, note: Path, key: str, mode: str, result: SegmentResult) -> None:
    spans = vault / "spans"
    spans.mkdir(parents=True, exist_ok=True)
    (spans / f"{key}.jsonl").write_bytes(result.span_file_text().encode("utf-8"))
    boundaries = result.boundaries_text()
    if mode == "boundaries" and boundaries is not None:
        (spans / f"{key}.boundaries.jsonl").write_bytes(boundaries.encode("utf-8"))
    updated = _source_note.with_segmenter(note.read_bytes().decode("utf-8"), mode)
    note.write_bytes(updated.encode("utf-8"))


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
                "path": f"spans/{key}.jsonl",
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
