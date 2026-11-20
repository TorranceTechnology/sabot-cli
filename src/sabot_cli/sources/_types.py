"""Names shared by every module of `sources`: the literals, the refusal and the plain results."""

import re
from dataclasses import dataclass
from typing import Literal

from sabot_cli.vault.paths import reserved_device_name

type SourceFormat = Literal["pdf", "html", "text"]
type SegmentMode = Literal["numbered", "headings", "sentences", "boundaries"]
type Numbering = Literal["dotted", "dotted-letter"]

SPANS_DIRECTORY = "spans"
_BOUNDARIES_SUFFIX = ".boundaries"
_SOURCE_KEY = re.compile(r"[a-z0-9][a-z0-9.-]*")


class SourceError(Exception):
    """A source document or its segmentation input cannot be used; `code` names the refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def source_note_relative(key: str) -> str:
    """The vault-relative path of the source note for `key`."""
    return f"sources/src.{key}.md"


def span_file_relative(key: str, *, boundaries: bool = False) -> str:
    """The vault-relative path of a source's span file, or of its boundaries file."""
    return f"{SPANS_DIRECTORY}/{key}{_BOUNDARIES_SUFFIX if boundaries else ''}.jsonl"


def is_source_key(text: str) -> bool:
    """Whether `text` is a source key: lowercase letters, digits, dots and hyphens, starting
    with a letter or digit. This is the one place the rule is written."""
    return _SOURCE_KEY.fullmatch(text) is not None


def check_key(key: str) -> str:
    """The key itself, or `SourceError` `source-key-invalid`."""
    if not is_source_key(key):
        raise SourceError(
            "source-key-invalid",
            "a source key is lowercase letters, digits, . and -, starting with a letter or digit",
        )
    if key.endswith(_BOUNDARIES_SUFFIX):
        raise SourceError(
            "source-key-invalid",
            f"a source key may not end in {_BOUNDARIES_SUFFIX}: spans/<key>.jsonl would be "
            "another source's boundaries file",
        )
    if reserved_device_name(f"{key}.jsonl"):
        raise SourceError(
            "source-key-invalid",
            "a source key may not start with a name Windows reserves for a device: "
            f"spans/{key}.jsonl could not be checked out there",
        )
    return key


@dataclass(frozen=True)
class ExtractedText:
    """The text of a source and the extractor that produced it, as recorded on the source note."""

    text: str
    extractor: str
