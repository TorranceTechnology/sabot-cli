"""Writing and reading `sources/src.<key>.md`, shared by `ingest` and `segment`."""

import json
from dataclasses import dataclass
from pathlib import Path

from sabot_cli.sources import SourceError, span_file_relative
from sabot_cli.vault import (
    FrontMatterError,
    TextFileError,
    parse_front_matter,
    read_text,
    split_front_matter,
)

_DELIMITER = "---"


def note_relative(key: str) -> str:
    """The vault-relative path of the source note for `key`."""
    return f"sources/src.{key}.md"


def note_path(vault: Path, key: str) -> Path:
    """Where the source note for `key` lives in a vault."""
    return vault / note_relative(key)


@dataclass(frozen=True)
class SourceNote:
    """Everything an ingested source note records. Optional fields are left out when absent."""

    key: str
    label: str
    title: str
    kind: str
    edition: str
    retrieved: str
    size: int
    sha256: str
    extractor: str
    effective: str | None = None
    url: str | None = None
    archive_url: str | None = None
    supersedes: str | None = None
    amends: str | None = None


def _quoted(value: str) -> str:
    """A YAML double-quoted scalar; JSON string syntax is a subset of it."""
    return json.dumps(value, ensure_ascii=False)


def _optional(name: str, value: str | None) -> list[str]:
    return [] if value is None else [f"{name}: {_quoted(value)}"]


def render(note: SourceNote) -> str:
    """The note text: front matter in the fixed order of the schema, then a short body."""
    lines = [
        _DELIMITER,
        f"id: src.{note.key}",
        "type: source",
        f"title: {_quoted(note.title)}",
        "status: structural",
        "confidence: high",
        "rev: 1",
        "schema: 1",
        f"kind: {note.kind}",
        f"key: {_quoted(note.key)}",
        f"label: {_quoted(note.label)}",
        f"edition: {_quoted(note.edition)}",
        *_optional("effective", note.effective),
        *_optional("url", note.url),
        *_optional("archive-url", note.archive_url),
        f"retrieved: {_quoted(note.retrieved)}",
        f"bytes: {note.size}",
        f"sha256: {_quoted(note.sha256)}",
        f"extractor: {_quoted(note.extractor)}",
        *_optional("supersedes", note.supersedes),
        *_optional("amends", note.amends),
        f"spans: {_quoted(span_file_relative(note.key))}",
        _DELIMITER,
        "",
        f"# {note.title}",
        "",
        "A registered source. Its text is kept outside the vault.",
        "",
    ]
    return "\n".join(lines)


def with_segmenter(text: str, mode: str) -> str:
    """The note text with its `segmenter` property set to `mode`, nothing else changed."""
    try:
        lines, closing = split_front_matter(text)
    except FrontMatterError as error:
        raise SourceError("source-note-invalid", "the source note has no front matter") from error
    carriage = "\r" if lines[0].endswith("\r") else ""
    entry = f"segmenter: {mode}{carriage}"
    for index in range(1, closing):
        if lines[index].startswith("segmenter:"):
            lines[index] = entry
            return "\n".join(lines)
    anchor = next((i for i in range(1, closing) if lines[i].startswith("spans:")), closing)
    lines.insert(anchor, entry)
    return "\n".join(lines)


def registered_sha256(vault: Path, key: str) -> str | None:
    """The `sha256` the vault's source note records, or None when there is no such note."""
    path = note_path(vault, key)
    if not path.is_file():
        return None
    try:
        properties, _ = parse_front_matter(read_text(path))
    except (TextFileError, FrontMatterError) as error:
        raise SourceError(
            "source-note-invalid", f"the source note cannot be read: {error}"
        ) from error
    value = properties.get("sha256")
    if not isinstance(value, str):
        raise SourceError("source-note-invalid", "the source note records no sha256")
    return value
