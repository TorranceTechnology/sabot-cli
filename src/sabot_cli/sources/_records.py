"""Span records, the index that answers questions about them, and the span file format."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from sabot_cli.canonical import JsonValue
from sabot_cli.sources._types import SourceError

_RECORD_KEYS = frozenset(
    {
        "editorialSha256",
        "kind",
        "locator",
        "offset",
        "page",
        "parent",
        "position",
        "sentences",
        "sha256",
        "source",
        "span",
        "words",
    }
)


@dataclass(frozen=True)
class SpanRecord:
    """Where a span is and what it hashes to. There is deliberately no text field."""

    span: str
    source: str
    locator: str
    kind: Literal["span", "sentence"]
    parent: str | None
    page: int
    offset: int
    words: int
    sentences: int
    position: int
    sha256: str
    editorial_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        """The record as one span-file line holds it."""
        return {
            "editorialSha256": self.editorial_sha256,
            "kind": self.kind,
            "locator": self.locator,
            "offset": self.offset,
            "page": self.page,
            "parent": self.parent,
            "position": self.position,
            "sentences": self.sentences,
            "sha256": self.sha256,
            "source": self.source,
            "span": self.span,
            "words": self.words,
        }


@dataclass(frozen=True)
class SpanIndex:
    """Span records in document order, from one span file or several merged by the caller."""

    records: tuple[SpanRecord, ...]
    _by_span: Mapping[str, SpanRecord] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_span", {record.span: record for record in self.records})

    def __contains__(self, span: object) -> bool:
        return span in self._by_span

    def get(self, span: str) -> SpanRecord | None:
        """The record whose `span` is `<source>#<locator>`, sentence records included."""
        return self._by_span.get(span)

    def sentences_of(self, span: str) -> tuple[SpanRecord, ...]:
        """The sentence records of one span, in order; empty for a one-sentence span."""
        return tuple(
            r for r in self.records if r.kind == "sentence" and f"{r.source}#{r.parent}" == span
        )

    def subtree_of(self, span: str) -> tuple[SpanRecord, ...]:
        """Span records beneath `span` through any depth of parents, not including `span`."""
        children: dict[str, list[SpanRecord]] = {}
        for record in self.records:
            if record.kind == "span" and record.parent is not None:
                children.setdefault(f"{record.source}#{record.parent}", []).append(record)
        found: list[SpanRecord] = []
        pending = list(reversed(children.get(span, [])))
        while pending:
            record = pending.pop()
            found.append(record)
            pending.extend(reversed(children.get(record.span, [])))
        return tuple(found)


def span_file_text(records: Iterable[SpanRecord]) -> str:
    """The span file for these records: compact sorted-key JSON, one record per line, LF."""
    return "".join(_dump(record.to_json()) + "\n" for record in records)


def _dump(document: Mapping[str, JsonValue]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dump_compact(document: Mapping[str, JsonValue]) -> str:
    """One JSON object on one line with sorted keys, the form of every JSONL file here."""
    return _dump(document)


def parse_spans(text: str, name: str) -> SpanIndex:
    """Parse the text of a span file; raise `SourceError` `span-file-invalid` naming the first
    bad line. `name` is the file's name, for the message."""
    records: list[SpanRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            records.append(_parse_record(name, number, line))
    return SpanIndex(tuple(records))


def _parse_record(name: str, number: int, line: str) -> SpanRecord:
    where = f"{name} line {number}"
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise SourceError("span-file-invalid", f"{where} is not JSON: {error.msg}") from error
    if not isinstance(raw, dict) or set(raw) != _RECORD_KEYS:
        raise SourceError("span-file-invalid", f"{where} does not have the span record keys")
    kind, parent = raw["kind"], raw["parent"]
    texts = (raw["span"], raw["source"], raw["locator"], raw["sha256"], raw["editorialSha256"])
    numbers = (raw["page"], raw["offset"], raw["words"], raw["sentences"], raw["position"])
    valid = (
        kind in ("span", "sentence")
        and (parent is None or isinstance(parent, str))
        and all(isinstance(value, str) for value in texts)
        and all(isinstance(value, int) and not isinstance(value, bool) for value in numbers)
    )
    if not valid:
        raise SourceError("span-file-invalid", f"{where} has a field of the wrong type")
    return SpanRecord(
        span=raw["span"],
        source=raw["source"],
        locator=raw["locator"],
        kind=kind,
        parent=parent,
        page=raw["page"],
        offset=raw["offset"],
        words=raw["words"],
        sentences=raw["sentences"],
        position=raw["position"],
        sha256=raw["sha256"],
        editorial_sha256=raw["editorialSha256"],
    )
