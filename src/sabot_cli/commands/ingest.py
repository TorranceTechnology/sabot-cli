"""The `ingest` command."""

import argparse
import hashlib
import re
from datetime import date
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands import _source_note
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RULESET_FILE, RulesetError, ruleset_with_source
from sabot_cli.sources import (
    DEFAULT_EXTRACTOR,
    EXTRACTORS,
    ExtractedText,
    SourceError,
    SourceFormat,
    check_key,
    extract_text,
)
from sabot_cli.vault.paths import display_path, relative_within
from sabot_cli.vault.transaction import Transaction, TransactionError, atomic_write

NAME = "ingest"

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXTENSION_FORMATS: dict[str, SourceFormat] = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".text": "text",
    ".md": "text",
}
_STORE_EXTENSIONS: dict[SourceFormat, str] = {"pdf": "pdf", "html": "html", "text": "txt"}
_REQUIRED_TO_REGISTER = ("label", "title", "edition", "retrieved")


def register(subparsers: Subparsers) -> None:
    """Add the `ingest` subparser."""
    parser = subparsers.add_parser(NAME, help="register one source edition as a source note")
    parser.add_argument("file", type=Path, help="source file to register")
    parser.add_argument("--key", required=True, help="source key")
    parser.add_argument("--label", help="short label used in citations (required to register)")
    parser.add_argument("--title", help="source title (required to register)")
    parser.add_argument("--edition", help="edition text (required to register)")
    parser.add_argument(
        "--retrieved", metavar="YYYY-MM-DD", help="retrieval date (required to register)"
    )
    parser.add_argument("--url", help="where the file came from")
    parser.add_argument("--effective", metavar="YYYY-MM-DD", help="date the edition took effect")
    parser.add_argument("--format", choices=("pdf", "html", "text"), help="source format")
    parser.add_argument(
        "--kind",
        choices=("edition", "errata", "ruling", "faq"),
        default="edition",
        help="source kind",
    )
    parser.add_argument("--supersedes", metavar="KEY", help="key of the edition this one replaces")
    parser.add_argument("--amends", metavar="KEY", help="key of the edition this one amends")
    parser.add_argument("--archive-url", help="archived snapshot of the source")
    parser.add_argument(
        "--extractor",
        metavar="NAME",
        help=f"PDF text extractor, one of: {', '.join(EXTRACTORS)} (default: {DEFAULT_EXTRACTOR})",
    )
    parser.add_argument(
        "--store", type=Path, metavar="DIR", help="private store to copy the file into"
    )
    parser.add_argument(
        "--text-out", type=Path, metavar="PATH", help="where to write extracted text"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare the file with the registered hash; needs only the file and --key",
    )
    add_vault_option(parser)
    add_json_option(parser)


def _is_inside(path: Path, directory: Path) -> bool:
    return relative_within(path, directory) is not None


def _detect_format(path: Path, data: bytes) -> SourceFormat | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    return _EXTENSION_FORMATS.get(path.suffix.lower())


def _invalid_date(value: str | None) -> bool:
    if value is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return True
    return not _DATE.match(value)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    vault: Path = args.vault
    key = str(args.key)
    try:
        check_key(key)
        load_rules(vault)
    except (RulesetError, SourceError) as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    try:
        data = Path(args.file).read_bytes()
    except OSError as error:
        return output.refuse(
            NAME, "file-unreadable", f"the file cannot be read: {error.strerror}", as_json=as_json
        )
    if args.verify:
        return _verify(vault, key, data, as_json=as_json)
    missing = [name for name in _REQUIRED_TO_REGISTER if getattr(args, name) is None]
    if missing:
        output.write_diagnostic(
            f"sabot ingest: usage: {', '.join('--' + name for name in missing)} required "
            "unless --verify is given"
        )
        return int(ExitCode.USAGE)
    try:
        with Transaction(vault) as transaction:
            return _register(args, key, data, transaction, as_json=as_json)
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)


def _register(
    args: argparse.Namespace, key: str, data: bytes, transaction: Transaction, *, as_json: bool
) -> int:
    """Plan every output inside the write lock, then publish them together."""
    vault: Path = args.vault
    try:
        registered = {source.key for source in load_rules(vault).ruleset.sources}
        refusal = _refusal(args, key, registered, data)
        if refusal is not None:
            return output.refuse(NAME, refusal[0], refusal[1], as_json=as_json)
        fmt = args.format or _detect_format(args.file, data)
        assert fmt is not None  # _refusal turns an undetectable format into a refusal
        extracted = extract_text(Path(args.file), fmt, args.extractor or DEFAULT_EXTRACTOR)
        sha256 = hashlib.sha256(data).hexdigest()
        stored = _store_name(args, key, fmt, sha256)
        note = _source_note.render(_note_of(args, key, data, sha256, extracted))
        registration = ruleset_with_source(vault, key, f"src.{key}", args.supersedes)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    transaction.put(_source_note.note_relative(key), note.encode("utf-8"))
    transaction.put(RULESET_FILE, registration)
    outside = _OutsideFiles()
    try:
        if args.store is not None and stored is not None:
            outside.write(args.store / stored, data)
        if args.text_out is not None:
            outside.write(args.text_out, extracted.text.encode("utf-8"))
        transaction.commit()
    except OSError as error:
        outside.undo()
        return output.refuse(
            NAME, "write-failed", f"a file could not be written: {error.strerror}", as_json=as_json
        )
    except BaseException:
        outside.undo()
        raise
    _report(args, key, fmt, len(data), sha256, extracted, stored, as_json=as_json)
    return int(ExitCode.OK)


def _refusal(
    args: argparse.Namespace, key: str, registered: set[str], data: bytes
) -> tuple[str, str] | None:
    """The first reason the input cannot be ingested, as (code, message); nothing is written."""
    vault: Path = args.vault
    if key in registered:
        return "source-key-registered", f"the source key {key!r} is already registered"
    if _source_note.note_path(vault, key).exists():
        return "source-note-exists", f"a source note for the key {key!r} already exists"
    for option, value in (("--supersedes", args.supersedes), ("--amends", args.amends)):
        if value is not None and value not in registered:
            return "source-key-unknown", f"{option} names {value!r}, which is not registered"
    for option, value in (("--retrieved", args.retrieved), ("--effective", args.effective)):
        if _invalid_date(value):
            return "date-invalid", f"{option} must be a real date as YYYY-MM-DD"
    texts = (args.label, args.title, args.edition, args.url, args.archive_url)
    if any(value is not None and ("\n" in value or "\r" in value) for value in texts):
        return "value-invalid", "text values must not contain line breaks"
    if args.extractor is not None and args.extractor not in EXTRACTORS:
        known = ", ".join(EXTRACTORS)
        return "extractor-unknown", f"the extractor {args.extractor!r} is not one of: {known}"
    if args.format is None and _detect_format(Path(args.file), data) is None:
        return "format-undetectable", "the format is not recognised; pass --format"
    if args.text_out is not None and _is_inside(args.text_out, vault):
        return "text-out-inside-vault", "extracted text must not be written inside the vault"
    if args.store is not None and _is_inside(args.store, vault):
        return "store-inside-vault", "the source store must be outside the vault"
    return None


def _note_of(
    args: argparse.Namespace, key: str, data: bytes, sha256: str, extracted: ExtractedText
) -> _source_note.SourceNote:
    return _source_note.SourceNote(
        key=key,
        label=args.label,
        title=args.title,
        kind=args.kind,
        edition=args.edition,
        retrieved=args.retrieved,
        size=len(data),
        sha256=sha256,
        extractor=extracted.extractor,
        effective=args.effective,
        url=args.url,
        archive_url=args.archive_url,
        supersedes=args.supersedes,
        amends=args.amends,
    )


class _OutsideFiles:
    """Files written outside the vault, which a transaction cannot cover; undone on failure."""

    def __init__(self) -> None:
        self._previous: list[tuple[Path, bytes | None]] = []
        self._made: list[Path] = []

    def write(self, path: Path, data: bytes) -> None:
        self._previous.append((path, path.read_bytes() if path.is_file() else None))
        self._made += [
            directory for directory in (path.parent, *path.parent.parents) if not directory.exists()
        ]
        atomic_write(path, data)

    def undo(self) -> None:
        for path, previous in reversed(self._previous):
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, previous)
            except OSError:
                continue
        for directory in self._made:
            try:
                directory.rmdir()
            except OSError:
                continue


def _store_name(args: argparse.Namespace, key: str, fmt: SourceFormat, sha256: str) -> str | None:
    if args.store is None:
        return None
    extension = Path(args.file).suffix.lower().lstrip(".") or _STORE_EXTENSIONS[fmt]
    return f"{key}.{sha256[:16]}.{extension}"


def _report(
    args: argparse.Namespace,
    key: str,
    fmt: SourceFormat,
    size: int,
    sha256: str,
    extracted: ExtractedText,
    stored: str | None,
    *,
    as_json: bool,
) -> None:
    note = f"sources/src.{key}.md"
    text_out = None if args.text_out is None else display_path(args.text_out, args.vault)
    if as_json:
        document: dict[str, JsonValue] = {
            "bytes": size,
            "command": NAME,
            "extractor": extracted.extractor,
            "format": output.FORMAT_VERSION,
            "key": key,
            "note": note,
            "sha256": sha256,
            "sourceFormat": fmt,
            "store": stored,
            "textOut": text_out,
        }
        output.write_json(document)
        return
    rows = [
        ("key", key),
        ("sha256", sha256),
        ("bytes", str(size)),
        ("extractor", extracted.extractor),
        ("note", note),
        *([("store", stored)] if stored else []),
        *([("text-out", text_out)] if text_out else []),
    ]
    output.write_text("".join(f"{name}: {value}\n" for name, value in rows))


def _verify(vault: Path, key: str, data: bytes, *, as_json: bool) -> int:
    try:
        expected = _source_note.registered_sha256(vault, key)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    if expected is None:
        return output.refuse(
            NAME, "source-not-found", f"the vault has no source note for {key!r}", as_json=as_json
        )
    actual = hashlib.sha256(data).hexdigest()
    matches = actual == expected
    if as_json:
        output.write_json(
            {
                "actualSha256": actual,
                "command": NAME,
                "expectedSha256": expected,
                "format": output.FORMAT_VERSION,
                "key": key,
                "match": matches,
            }
        )
    else:
        output.write_text(
            f"{'match' if matches else 'mismatch'}: {key}\nregistered: {expected}\nfile: {actual}\n"
        )
    return int(ExitCode.OK if matches else ExitCode.FINDINGS)
