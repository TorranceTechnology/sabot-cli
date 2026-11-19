"""Report which spans of each source a vault's notes cite."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from sabot_cli.rules._checks._spans_shim import SpanIndex, SpanRecord, parse_span_ref
from sabot_cli.rules.ruleset import Ruleset
from sabot_cli.vault import Note, Vault

__all__ = ["CoverageReport", "PartialSpan", "SourceCoverage", "coverage"]

# A citation by a note in one of these statuses does not cover anything: the note is unfinished
# or has been replaced.
_NON_COUNTING_STATUSES = frozenset({"pending", "superseded"})
# Only a note that declines or merely frames a part of the source may cite its whole subtree.
_SUBTREE_STATUSES = frozenset({"refused", "structural"})
_NO_STATUS = "none"


@dataclass(frozen=True)
class PartialSpan:
    """A multi-sentence span some of whose sentences are cited and some are not."""

    span: str
    missing: tuple[str, ...]


@dataclass(frozen=True)
class SourceCoverage:
    """How well the notes cite one source, over the spans the ruleset puts in scope.

    `uncited` and `partial` are in the span file's document order and name spans by their full
    reference. `citing_notes` counts the distinct notes citing the source by status, including
    the statuses whose citations do not count.
    """

    key: str
    in_scope: int
    cited: int
    uncited: tuple[str, ...]
    partial: tuple[PartialSpan, ...]
    citing_notes: Mapping[str, int]


@dataclass(frozen=True)
class CoverageReport:
    """The coverage of each requested source, in source-key order."""

    sources: tuple[SourceCoverage, ...]

    @property
    def complete(self) -> bool:
        """Whether every in-scope span of every source is covered."""
        return all(not source.uncited and not source.partial for source in self.sources)


def coverage(vault: Vault, ruleset: Ruleset, spans: SpanIndex) -> CoverageReport:
    """Measure the citation coverage of every source that has records in `spans`.

    A span is covered when it is cited whole, when a refused or structural note cites the
    subtree of it or of an ancestor, or when each of its sentences is cited. A source the
    ruleset gives no scope entry is wholly in scope, as is one whose entry is empty.
    """
    records_by_source: dict[str, list[SpanRecord]] = defaultdict(list)
    for record in spans.records:
        records_by_source[record.source].append(record)
    return CoverageReport(
        tuple(
            _source_coverage(key, records_by_source[key], ruleset.scope.get(key, ()), vault)
            for key in sorted(records_by_source)
        )
    )


@dataclass(frozen=True)
class _Citations:
    whole: frozenset[str]
    sentences: Mapping[str, frozenset[int]]
    subtrees: frozenset[str]
    citing_notes: Mapping[str, int]


def _source_coverage(
    key: str, records: Sequence[SpanRecord], scope: Sequence[str], vault: Vault
) -> SourceCoverage:
    citations = _citations(key, vault)
    by_locator = {record.locator: record for record in records}
    sentences_of: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.kind == "sentence" and record.parent is not None:
            sentences_of[record.parent].append(record.locator)
    in_scope = [
        record
        for record in records
        if record.kind == "span" and _in_scope(record, by_locator, scope)
    ]
    cited = 0
    uncited: list[str] = []
    partial: list[PartialSpan] = []
    for record in in_scope:
        if _covered_whole(record, by_locator, citations):
            cited += 1
            continue
        sentences = sentences_of.get(record.locator, [])
        numbers = citations.sentences.get(record.locator, frozenset())
        given = [locator for locator in sentences if _sentence_number(locator) in numbers]
        if sentences and len(given) == len(sentences):
            cited += 1
        elif given:
            missing = tuple(f"{key}#{locator}" for locator in sentences if locator not in given)
            partial.append(PartialSpan(f"{key}#{record.locator}", missing))
        else:
            uncited.append(f"{key}#{record.locator}")
    return SourceCoverage(
        key=key,
        in_scope=len(in_scope),
        cited=cited,
        uncited=tuple(uncited),
        partial=tuple(partial),
        citing_notes=MappingProxyType(dict(sorted(citations.citing_notes.items()))),
    )


def _citations(key: str, vault: Vault) -> _Citations:
    whole: set[str] = set()
    subtrees: set[str] = set()
    sentences: dict[str, set[int]] = defaultdict(set)
    citing: dict[str, set[str]] = defaultdict(set)
    for note in vault.notes.values():
        status = _status(note)
        for raw in _source_entries(note):
            try:
                ref = parse_span_ref(raw)
            except ValueError:
                continue
            if ref.source != key:
                continue
            citing[status].add(note.id)
            if status in _NON_COUNTING_STATUSES:
                continue
            if ref.subtree:
                if status in _SUBTREE_STATUSES:
                    subtrees.add(ref.locator)
            elif ref.sentence is not None:
                sentences[ref.locator].add(ref.sentence)
            else:
                whole.add(ref.locator)
    return _Citations(
        frozenset(whole),
        {locator: frozenset(numbers) for locator, numbers in sentences.items()},
        frozenset(subtrees),
        {status: len(ids) for status, ids in citing.items()},
    )


def _status(note: Note) -> str:
    status = note.properties.get("status")
    return status if isinstance(status, str) else _NO_STATUS


def _source_entries(note: Note) -> list[str]:
    value = note.properties.get("source")
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _ancestry(record: SpanRecord, by_locator: Mapping[str, SpanRecord]) -> list[str]:
    """The locators of the record and of each ancestor, nearest first."""
    locators: list[str] = []
    current: SpanRecord | None = record
    while current is not None and current.locator not in locators:
        locators.append(current.locator)
        current = by_locator.get(current.parent) if current.parent is not None else None
    return locators


def _covered_whole(
    record: SpanRecord, by_locator: Mapping[str, SpanRecord], citations: _Citations
) -> bool:
    if record.locator in citations.whole:
        return True
    return any(locator in citations.subtrees for locator in _ancestry(record, by_locator))


def _in_scope(
    record: SpanRecord, by_locator: Mapping[str, SpanRecord], scope: Sequence[str]
) -> bool:
    if not scope:
        return True
    return any(
        _under(locator, prefix) for locator in _ancestry(record, by_locator) for prefix in scope
    )


def _under(locator: str, prefix: str) -> bool:
    """Whether a locator is the prefix or continues it at a `.` or `/` boundary, so `1` holds
    `1.1` and `1/a` but not `10`."""
    return locator == prefix or locator.startswith((f"{prefix}.", f"{prefix}/"))


def _sentence_number(locator: str) -> int:
    """The `n` of a sentence record's `<span>/s<n>` locator."""
    return int(locator.rsplit("/s", 1)[1])
