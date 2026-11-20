"""Reading the files the edition commands take: span files by key, and extracted text."""

from collections.abc import Mapping
from pathlib import Path

from sabot_cli.sources._boundaries import own_ranges
from sabot_cli.sources._records import SpanIndex, parse_spans
from sabot_cli.sources._types import SourceError, check_key, span_file_relative
from sabot_cli.vault import TextFileError, read_text


def read_text_file(path: Path) -> str:
    """The UTF-8 text of a file, or `SourceError` `file-unreadable` or `text-undecodable`."""
    try:
        return read_text(path)
    except TextFileError as error:
        code = "file-unreadable" if error.reason == "unreadable" else "text-undecodable"
        raise SourceError(code, str(error)) from error


def read_span_file(vault: Path, key: str) -> SpanIndex:
    """The span file `spans/<key>.jsonl` of a vault; the only reader of one.

    An edition is found by its key whether or not `_ruleset.yaml` lists it, so a later edition
    can be compared and migrated to before it is registered. Raises `SourceError`
    `source-key-invalid`, `span-file-missing` when there is no such file, or `span-file-invalid`.
    """
    relative = span_file_relative(check_key(key))
    path = vault / relative
    if not path.is_file():
        raise SourceError("span-file-missing", f"the vault has no {relative}; run segment first")
    try:
        return parse_spans(read_text(path), path.name)
    except TextFileError as error:
        raise SourceError("span-file-invalid", str(error)) from error


def read_edition_ranges(vault: Path, key: str) -> Mapping[str, tuple[int, int]] | None:
    """The text range each span of an edition owns, from its boundaries file in the vault.

    Only an edition segmented in `boundaries` mode has one (`spans/<key>.boundaries.jsonl`);
    for any other the result is None and a span is read as running to the next span's start.
    """
    path = vault / span_file_relative(check_key(key), boundaries=True)
    if not path.is_file():
        return None
    return own_ranges(read_text_file(path))
