"""`segment`: choose a mode, build items, hash them into span records and check the result."""

from collections.abc import Sequence
from dataclasses import dataclass

from sabot_cli.canonical import JsonValue
from sabot_cli.sources import _boundaries, _builders
from sabot_cli.sources._items import Item
from sabot_cli.sources._lines import JoinedText, LineIndex
from sabot_cli.sources._normalise import LEADING_NUMBER, digest, editorial, normalise
from sabot_cli.sources._records import SpanRecord, span_file_text
from sabot_cli.sources._self_checks import SELF_CHECKS, CheckResult, merge, run_self_checks
from sabot_cli.sources._sentences import split_sentences
from sabot_cli.sources._spanref import sentence_locator
from sabot_cli.sources._types import (
    Numbering,
    SegmentMode,
    SourceError,
    check_key,
)


@dataclass(frozen=True)
class SegmentResult:
    """The records a segmentation produced, the checks it ran and the boundaries it implies."""

    source: str
    mode: SegmentMode
    records: tuple[SpanRecord, ...]
    checks: tuple[CheckResult, ...]
    boundary_records: tuple[dict[str, JsonValue], ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every check passed; only then may a span file be written."""
        return all(check.passed for check in self.checks)

    @property
    def span_count(self) -> int:
        """Records that are spans, not sentences of one."""
        return sum(1 for record in self.records if record.kind == "span")

    @property
    def sentence_count(self) -> int:
        """Sentence records."""
        return len(self.records) - self.span_count

    def span_file_text(self) -> str:
        """The `spans/<key>.jsonl` content for these records."""
        return span_file_text(self.records)

    def boundaries_text(self) -> str | None:
        """The result as a boundaries file, or None when the boundaries file was refused."""
        if not self.boundary_records:
            return None
        return _boundaries.boundaries_text(self.boundary_records)


def segment(
    text: str,
    source_key: str,
    mode: SegmentMode,
    numbering: Numbering = "dotted",
    skip_pages: int = 0,
    *,
    boundaries: str | None = None,
) -> SegmentResult:
    """Segment extracted text into span records; no file is written here.

    `skip_pages` applies to numbered mode only, and only to text that has page breaks (form
    feeds): text without them is one page, which a skip would discard whole. `boundaries` is the
    text of a boundaries file and is required, and only used, in `boundaries` mode. A refused
    input raises `SourceError`; a result whose checks fail is returned with its findings.
    """
    check_key(source_key)
    if skip_pages < 0:
        raise SourceError("skip-pages-invalid", "the number of pages to skip cannot be negative")
    if (boundaries is None) != (mode != "boundaries"):
        raise SourceError(
            "boundaries-mismatch", "a boundaries file goes with boundaries mode and only with it"
        )
    index = LineIndex(text)
    if "\f" not in text:
        skip_pages = 0
    items: Sequence[Item]
    skips: Sequence[_boundaries.Skip]
    if mode == "boundaries":
        assert boundaries is not None  # the mismatch check above refused every other case
        plan, findings = _boundaries.check_boundaries(boundaries, index)
        if plan is None:
            return SegmentResult(source_key, mode, (), _boundary_checks(findings))
        items, skips = plan.items, plan.skips
    else:
        items = _build_items(index, mode, numbering, skip_pages)
        skips = _boundaries.derive_skips(items, index)
    records = _records(source_key, items, index)
    checks = merge(_boundary_checks([]) if mode == "boundaries" else (), run_self_checks(records))
    return SegmentResult(source_key, mode, records, checks, _boundaries.emit_records(items, skips))


def _build_items(
    index: LineIndex, mode: SegmentMode, numbering: Numbering, skip_pages: int
) -> list[Item]:
    if mode == "numbered":
        return _builders.numbered(index, numbering, skip_pages)
    if mode == "headings":
        return _builders.headings(index, numbering)
    return _builders.sentences(index)


def _boundary_checks(findings: Sequence[_boundaries.Finding]) -> tuple[CheckResult, ...]:
    """One result per boundaries check, passing unless a finding names it."""
    return tuple(
        CheckResult(name, tuple(f.detail for f in findings if f.check == name))
        for name in sorted({*_boundaries.BOUNDARY_CHECKS, *SELF_CHECKS})
        if name in _boundaries.BOUNDARY_CHECKS
    )


def _sentence_ranges(joined: JoinedText, leading_sentence: bool) -> list[tuple[int, int]]:
    text = joined.text
    if leading_sentence and text:
        title = joined.first_line_length
        rest = text[title + 1 :]
        return [(0, title), *((a + title + 1, b + title + 1) for a, b in split_sentences(rest))]
    lead = LEADING_NUMBER.match(text)
    skipped = lead.end() if lead else 0
    return [(a + skipped, b + skipped) for a, b in split_sentences(text[skipped:])]


def _records(source: str, items: Sequence[Item], index: LineIndex) -> tuple[SpanRecord, ...]:
    records: list[SpanRecord] = []
    for item in items:
        joined = index.joined(item.start, item.end)
        ranges = _sentence_ranges(joined, item.leading_sentence)
        normalised = normalise(joined.text)
        records.append(
            SpanRecord(
                span=f"{source}#{item.locator}",
                source=source,
                locator=item.locator,
                kind="span",
                parent=item.parent,
                page=item.page,
                offset=joined.first_offset if joined.first_offset is not None else item.start,
                words=len(normalised.split()),
                sentences=len(ranges),
                position=len(records),
                sha256=digest(normalised),
                editorial_sha256=digest(editorial(joined.text)),
            )
        )
        if len(ranges) > 1:
            for number, (begin, end) in enumerate(ranges, start=1):
                sentence = joined.text[begin:end]
                locator = sentence_locator(item.locator, number)
                records.append(
                    SpanRecord(
                        span=f"{source}#{locator}",
                        source=source,
                        locator=locator,
                        kind="sentence",
                        parent=item.locator,
                        page=item.page,
                        offset=joined.source_offset(begin),
                        words=len(normalise(sentence).split()),
                        sentences=1,
                        position=len(records),
                        sha256=digest(normalise(sentence)),
                        editorial_sha256=digest(editorial(sentence)),
                    )
                )
    return tuple(records)
