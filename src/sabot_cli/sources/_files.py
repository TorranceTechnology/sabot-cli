"""Reading the files the edition commands take: span files by key, and extracted text."""

import re
from collections.abc import Mapping
from pathlib import Path

from sabot_cli.sources._boundaries import own_ranges
from sabot_cli.sources._records import SpanIndex, read_spans
from sabot_cli.sources._types import SOURCE_KEY_PATTERN, SourceError

_KEY = re.compile(SOURCE_KEY_PATTERN)
SPANS_DIRECTORY = "spans"


def check_key(key: str) -> str:
    """The key itself, or `SourceError` `source-key-invalid`."""
    if not _KEY.fullmatch(key):
        raise SourceError(
            "source-key-invalid", "a source key is lowercase letters, digits, . and -"
        )
    return key


def read_edition_spans(vault: Path, key: str) -> SpanIndex:
    """The span file `spans/<key>.jsonl` of a vault.

    An edition is found by its key whether or not `_ruleset.yaml` lists it, so a later edition
    can be compared and migrated to before it is registered. Raises `SourceError`
    `span-file-missing` when there is no such file.
    """
    path = vault / SPANS_DIRECTORY / f"{check_key(key)}.jsonl"
    if not path.is_file():
        raise SourceError(
            "span-file-missing",
            f"the vault has no {SPANS_DIRECTORY}/{key}.jsonl; run segment first",
        )
    return read_spans(path)


def read_edition_ranges(vault: Path, key: str) -> Mapping[str, tuple[int, int]] | None:
    """The text range each span of an edition owns, from its boundaries file in the vault.

    Only an edition segmented in `boundaries` mode has one (`spans/<key>.boundaries.jsonl`);
    for any other the result is None and a span is read as running to the next span's start.
    """
    path = vault / SPANS_DIRECTORY / f"{check_key(key)}.boundaries.jsonl"
    if not path.is_file():
        return None
    return own_ranges(read_text_file(path))


def read_text_file(path: Path) -> str:
    """The UTF-8 text of a file, or `SourceError` `file-unreadable` or `text-undecodable`."""
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as error:
        raise SourceError(
            "file-unreadable", f"{path.name} cannot be read: {error.strerror}"
        ) from error
    except UnicodeDecodeError as error:
        raise SourceError("text-undecodable", f"{path.name} is not valid UTF-8") from error
