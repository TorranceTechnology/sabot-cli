"""Boundaries files: an agent's proposed spans, checked here and never produced by a model.

A record is `{"locator", "parent", "start", "end", "page"?}` or a skip,
`{"skip": true, "reason", "start", "end"}`; offsets are character offsets into the extracted
text, `end` exclusive. A record that other records name as their parent is a parent: its range
covers its children, and its own text is whatever lies before its first child (a heading, say),
so a parent with nothing before its first child hashes the empty string. Every non-whitespace
character must lie in exactly one leaf, one skip or one parent's own text, and text between or
after a parent's children is therefore a gap unless a skip declares it.
"""

import bisect
import itertools
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sabot_cli.output import JsonValue
from sabot_cli.sources._items import Item
from sabot_cli.sources._lines import LineIndex, is_page_furniture
from sabot_cli.sources._records import dump_compact
from sabot_cli.sources._spanref import locator_problem

BOUNDARY_CHECKS: tuple[str, ...] = (
    "child-outside-parent",
    "duplicate-locator",
    "gap",
    "locator-malformed",
    "missing-parent",
    "offset-order",
    "offset-out-of-range",
    "overlap",
    "parent-cycle",
    "record-malformed",
)


@dataclass(frozen=True)
class Finding:
    """One failed check and the place it failed, named without quoting any source text."""

    check: str
    detail: str


@dataclass(frozen=True)
class Skip:
    """Text the agent declares is not a span, with the reason it gives."""

    reason: str
    start: int
    end: int


@dataclass(frozen=True)
class _Span:
    line: int
    locator: str
    parent: str | None
    start: int
    end: int
    page: int | None


@dataclass(frozen=True)
class BoundariesPlan:
    """A checked proposal: items in document order and the declared skips."""

    items: tuple[Item, ...]
    skips: tuple[Skip, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse(text: str, findings: list[Finding]) -> tuple[list[_Span], list[tuple[int, Skip]]]:
    spans: list[_Span] = []
    skips: list[tuple[int, Skip]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            findings.append(Finding("record-malformed", f"line {number}"))
            continue
        if not isinstance(raw, dict):
            findings.append(Finding("record-malformed", f"line {number}"))
        elif raw.get("skip") is True:
            if set(raw) == {"skip", "reason", "start", "end"} and _is_skip_valid(raw):
                skips.append((number, Skip(raw["reason"], raw["start"], raw["end"])))
            else:
                findings.append(Finding("record-malformed", f"line {number}"))
        elif _is_span_valid(raw):
            spans.append(
                _Span(
                    number,
                    raw["locator"],
                    raw.get("parent"),
                    raw["start"],
                    raw["end"],
                    raw.get("page"),
                )
            )
        else:
            findings.append(Finding("record-malformed", f"line {number}"))
    return spans, skips


def _is_skip_valid(raw: Mapping[str, object]) -> bool:
    return isinstance(raw["reason"], str) and _is_int(raw["start"]) and _is_int(raw["end"])


def _is_span_valid(raw: Mapping[str, object]) -> bool:
    page = raw.get("page")
    return (
        set(raw) <= {"locator", "parent", "start", "end", "page"}
        and isinstance(raw.get("locator"), str)
        and (raw.get("parent") is None or isinstance(raw.get("parent"), str))
        and _is_int(raw.get("start"))
        and _is_int(raw.get("end"))
        and (page is None or (isinstance(page, int) and not isinstance(page, bool) and page >= 1))
    )


def check_boundaries(text: str, index: LineIndex) -> tuple[BoundariesPlan | None, list[Finding]]:
    """Parse and check a boundaries file against the extracted text it describes.

    Returns the plan when every check passes, otherwise None and the findings, sorted.
    """
    findings: list[Finding] = []
    spans, skip_entries = _parse(text, findings)
    length = len(index.text)
    skips = [skip for _, skip in skip_entries]
    for span in spans:
        if span.end <= span.start:
            findings.append(Finding("offset-order", span.locator))
        if span.start < 0 or span.end > length:
            findings.append(Finding("offset-out-of-range", span.locator))
        if (problem := locator_problem(span.locator)) is not None:
            findings.append(Finding("locator-malformed", f"{span.locator}: {problem}"))
    for number, skip in skip_entries:
        if skip.end <= skip.start:
            findings.append(Finding("offset-order", f"skip at line {number}"))
        if skip.start < 0 or skip.end > length:
            findings.append(Finding("offset-out-of-range", f"skip at line {number}"))
    counts = Counter(span.locator for span in spans)
    findings += [Finding("duplicate-locator", name) for name, n in counts.items() if n > 1]
    by_locator: dict[str, _Span] = {}
    for span in spans:
        by_locator.setdefault(span.locator, span)
    findings += _tree_findings(spans, by_locator)
    findings += _coverage_findings(index, spans, skips, by_locator)
    if findings:
        unique = sorted(set(findings), key=lambda f: (f.check, f.detail))
        return None, unique
    return _plan(spans, skips, by_locator, index), []


def _tree_findings(spans: Sequence[_Span], by_locator: Mapping[str, _Span]) -> list[Finding]:
    findings: list[Finding] = []
    for span in spans:
        if span.parent is None:
            continue
        parent = by_locator.get(span.parent)
        if parent is None:
            findings.append(Finding("missing-parent", f"{span.locator} names {span.parent}"))
        elif span.start < parent.start or span.end > parent.end:
            findings.append(Finding("child-outside-parent", span.locator))
    for span in spans:
        seen = {span.locator}
        cursor = by_locator.get(span.parent) if span.parent is not None else None
        while cursor is not None:
            if cursor.locator in seen:
                findings.append(Finding("parent-cycle", span.locator))
                break
            seen.add(cursor.locator)
            cursor = by_locator.get(cursor.parent) if cursor.parent is not None else None
    siblings: dict[str | None, list[_Span]] = {}
    for span in spans:
        if span.parent is None or span.parent in by_locator:
            siblings.setdefault(span.parent, []).append(span)
    for group in siblings.values():
        ordered = sorted(group, key=lambda s: (s.start, s.end))
        for left, right in itertools.pairwise(ordered):
            if right.start < left.end:
                findings.append(Finding("overlap", f"{left.locator}, {right.locator}"))
    return findings


def _children(spans: Iterable[_Span], by_locator: Mapping[str, _Span]) -> dict[str, list[_Span]]:
    children: dict[str, list[_Span]] = {}
    for span in spans:
        if span.parent is not None and span.parent in by_locator:
            children.setdefault(span.parent, []).append(span)
    return children


def _own_range(span: _Span, children: Mapping[str, Sequence[_Span]]) -> tuple[int, int]:
    """The leaf's whole range, or the text before a parent's first child."""
    kids = children.get(span.locator)
    if not kids:
        return span.start, span.end
    return span.start, max(span.start, min(child.start for child in kids))


def _coverage_findings(
    index: LineIndex,
    spans: Sequence[_Span],
    skips: Sequence[Skip],
    by_locator: Mapping[str, _Span],
) -> list[Finding]:
    text = index.text
    length = len(text)
    children = _children(spans, by_locator)
    pieces = [_own_range(span, children) for span in spans]
    pieces += [(skip.start, skip.end) for skip in skips]
    # Clipped, not dropped, so an offset out of range is reported once by its own check.
    clipped = [(max(0, a), min(length, b)) for a, b in pieces]
    clipped = [(a, b) for a, b in clipped if a < b]
    cuts = sorted({0, length, *(point for piece in clipped for point in piece)})
    prefix = [0, *itertools.accumulate(0 if c.isspace() else 1 for c in text)]
    starts = sorted(a for a, _ in clipped)
    ends = sorted(b for _, b in clipped)
    findings: list[Finding] = []
    for low, high in itertools.pairwise(cuts):
        if prefix[high] == prefix[low]:
            continue
        depth = bisect.bisect_right(starts, low) - bisect.bisect_right(ends, low)
        if depth == 1:
            continue
        stretch = text[low:high]
        begin = low + len(stretch) - len(stretch.lstrip())
        finish = high - (len(stretch) - len(stretch.rstrip()))
        name = "gap" if depth == 0 else "overlap"
        findings.append(Finding(name, f"{begin}-{finish}"))
    return findings


def _plan(
    spans: Sequence[_Span],
    skips: Sequence[Skip],
    by_locator: Mapping[str, _Span],
    index: LineIndex,
) -> BoundariesPlan:
    children = _children(spans, by_locator)

    def depth(span: _Span) -> int:
        levels = 0
        cursor = span
        while cursor.parent is not None:
            cursor = by_locator[cursor.parent]
            levels += 1
        return levels

    ordered = sorted(enumerate(spans), key=lambda pair: (pair[1].start, depth(pair[1]), pair[0]))
    items: list[Item] = []
    for _, span in ordered:
        start, end = _own_range(span, children)
        page = span.page if span.page is not None else index.page_at(start)
        items.append(Item(span.locator, span.parent, page, start, end))
    return BoundariesPlan(tuple(items), tuple(sorted(skips, key=lambda s: (s.start, s.end))))


def derive_skips(items: Sequence[Item], index: LineIndex) -> tuple[Skip, ...]:
    """Skip records for every line text that no item covers, as `check_boundaries` expects."""
    covered = _covered_ranges(items)
    highs = [high for _, high in covered]
    skips: list[Skip] = []
    for line in index.lines:
        if not line.text:
            continue
        cursor, end = line.text_offset, line.text_end
        for low, high in covered[bisect.bisect_right(highs, cursor) :]:
            if low >= end:
                break
            if low > cursor:
                skips += _skips_for(index.text, cursor, low, line.text)
            cursor = max(cursor, high)
        if cursor < end:
            skips += _skips_for(index.text, cursor, end, line.text)
    return tuple(skips)


def _skips_for(text: str, start: int, end: int, line_text: str) -> list[Skip]:
    segment = text[start:end]
    start += len(segment) - len(segment.lstrip())
    end -= len(segment) - len(segment.rstrip())
    if start >= end:
        return []
    if is_page_furniture(line_text):
        reason = "page furniture"
    elif set(text[start:end]) == {"#"}:
        reason = "heading marker"
    else:
        reason = "unsegmented text"
    return [Skip(reason, start, end)]


def _covered_ranges(items: Sequence[Item]) -> list[tuple[int, int]]:
    """Ranges the checker counts as covered: leaves, and the lead-in of each parent."""
    first_child: dict[str, int] = {}
    for item in items:
        if item.parent is not None:
            first_child[item.parent] = min(first_child.get(item.parent, item.start), item.start)
    ranges = sorted(
        (item.start, max(item.start, first_child.get(item.locator, item.end))) for item in items
    )
    merged: list[tuple[int, int]] = []
    for low, high in ranges:
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def emit_records(items: Sequence[Item], skips: Sequence[Skip]) -> tuple[dict[str, JsonValue], ...]:
    """The boundaries records for items and skips, in document order."""
    children: dict[str, list[Item]] = {}
    for item in items:
        if item.parent is not None:
            children.setdefault(item.parent, []).append(item)

    def extent(item: Item) -> int:
        return max([item.end, *(extent(child) for child in children.get(item.locator, []))])

    entries: list[tuple[int, dict[str, JsonValue]]] = [
        (
            item.start,
            {
                "end": extent(item),
                "locator": item.locator,
                "page": item.page,
                "parent": item.parent,
                "start": item.start,
            },
        )
        for item in items
    ]
    entries += [
        (skip.start, {"end": skip.end, "reason": skip.reason, "skip": True, "start": skip.start})
        for skip in skips
    ]
    entries.sort(key=lambda entry: entry[0])
    return tuple(record for _, record in entries)


def boundaries_text(records: Iterable[Mapping[str, JsonValue]]) -> str:
    """A boundaries file: compact sorted-key JSON, one record per line, LF."""
    return "".join(dump_compact(record) + "\n" for record in records)
