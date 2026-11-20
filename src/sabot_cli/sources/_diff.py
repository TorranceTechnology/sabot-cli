"""Classify how each span changed between two editions, from their span files alone.

The hashes decide the class; the extracted texts are optional and only add a changed-word count
for substantive spans and a pairing of removed with added spans. No text is ever returned.
"""

import difflib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sabot_cli.sources._lines import LineIndex
from sabot_cli.sources._normalise import digest, normalise
from sabot_cli.sources._records import SpanIndex, SpanRecord
from sabot_cli.sources._types import SourceError

type Fate = Literal["unchanged", "editorial", "substantive", "removed", "moved"]

# Two spans are paired when 2 * matching words / (words in both) is at least 4/5; the test is
# done on integers so that no rounding decides a pairing.
_SIMILARITY_NUMERATOR = 2
_SIMILARITY_DENOMINATOR = 5


@dataclass(frozen=True)
class MovedSpan:
    """A span whose text is byte-for-byte the same after normalising, under another locator."""

    old: str
    new: str


@dataclass(frozen=True)
class PairedSpan:
    """A removed and an added span whose texts are alike: a move with a light rewording."""

    old: str
    new: str
    changed_words: int


@dataclass(frozen=True)
class EditionDiff:
    """The classes of the spans of two editions, as locators in code-point order.

    A moved span appears in `removed` (under its old locator) and in `added` (under its new
    one), and once in `moved`; `paired` is empty unless both texts were given.
    """

    old_key: str
    new_key: str
    unchanged: tuple[str, ...]
    editorial: tuple[str, ...]
    substantive: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    moved: tuple[MovedSpan, ...]
    paired: tuple[PairedSpan, ...] = ()
    changed_words: Mapping[str, int] = field(default_factory=dict)
    _fates: Mapping[str, tuple[Fate, str | None]] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        fates: dict[str, tuple[Fate, str | None]] = {}
        for locator in self.unchanged:
            fates[locator] = ("unchanged", None)
        for locator in self.editorial:
            fates[locator] = ("editorial", None)
        for locator in self.substantive:
            fates[locator] = ("substantive", None)
        for locator in self.removed:
            fates[locator] = ("removed", None)
        for moved in self.moved:
            fates[moved.old] = ("moved", moved.new)
        object.__setattr__(self, "_fates", fates)

    def fate_of(self, old_locator: str) -> tuple[Fate, str | None] | None:
        """What became of an old span, with its new locator when it moved; None when the span
        was not classified (it is not in the old edition, or lies outside the scope)."""
        return self._fates.get(old_locator)


def diff_editions(
    old: SpanIndex,
    new: SpanIndex,
    scope: Sequence[str] = (),
    old_text: str | None = None,
    new_text: str | None = None,
    *,
    old_ranges: Mapping[str, tuple[int, int]] | None = None,
    new_ranges: Mapping[str, tuple[int, int]] | None = None,
) -> EditionDiff:
    """Classify every span of `old` and `new`; `scope` limits both to locators under a prefix.

    `old_ranges` and `new_ranges` give the text each span owns when the edition has a boundaries
    file; without them a span is read as running to the next span's start. Raises `SourceError`
    when exactly one text is given, or when a text does not hold the spans its span file
    describes (checked by hashing what the span file's offsets point at).
    """
    if (old_text is None) != (new_text is None):
        raise SourceError(
            "text-options-incomplete", "give both --old-text and --new-text, or neither"
        )
    old_spans = _spans_in_scope(old, scope)
    new_spans = _spans_in_scope(new, scope)
    unchanged: list[str] = []
    editorial: list[str] = []
    substantive: list[str] = []
    for locator, record in old_spans.items():
        counterpart = new_spans.get(locator)
        if counterpart is None:
            continue
        if counterpart.sha256 == record.sha256:
            unchanged.append(locator)
        elif counterpart.editorial_sha256 == record.editorial_sha256:
            editorial.append(locator)
        else:
            substantive.append(locator)
    removed = [locator for locator in old_spans if locator not in new_spans]
    added = [locator for locator in new_spans if locator not in old_spans]
    moved = _match_moves(old_spans, new_spans, removed, added)
    paired: tuple[PairedSpan, ...] = ()
    changed: dict[str, int] = {}
    if old_text is not None and new_text is not None:
        old_words = _span_words(old, old_text, old_ranges)
        new_words = _span_words(new, new_text, new_ranges)
        changed = {
            locator: _word_delta(old_words[locator], new_words[locator]) for locator in substantive
        }
        moved_old = {m.old for m in moved}
        moved_new = {m.new for m in moved}
        paired = _pair(
            {k: old_words[k] for k in removed if k not in moved_old},
            {k: new_words[k] for k in added if k not in moved_new},
        )
    return EditionDiff(
        old_key=_key_of(old),
        new_key=_key_of(new),
        unchanged=tuple(sorted(unchanged)),
        editorial=tuple(sorted(editorial)),
        substantive=tuple(sorted(substantive)),
        added=tuple(sorted(added)),
        removed=tuple(sorted(removed)),
        moved=tuple(sorted(moved, key=lambda m: (m.old, m.new))),
        paired=paired,
        changed_words=dict(sorted(changed.items())),
    )


def _key_of(index: SpanIndex) -> str:
    return index.records[0].source if index.records else ""


def _spans_in_scope(index: SpanIndex, scope: Sequence[str]) -> dict[str, SpanRecord]:
    """Span records (not sentence records) by locator, in document order, within the scope."""
    spans = {record.locator: record for record in index.records if record.kind == "span"}
    if not scope:
        return spans
    return {
        locator: record
        for locator, record in spans.items()
        if any(
            _under(ancestor, prefix) for ancestor in _ancestry(record, spans) for prefix in scope
        )
    }


def _ancestry(record: SpanRecord, spans: Mapping[str, SpanRecord]) -> list[str]:
    """The record's locator and those of its parents, so `4.2a` lies under the prefix `4.2`."""
    chain = [record.locator]
    parent = record.parent
    while parent is not None and parent in spans and parent not in chain:
        chain.append(parent)
        parent = spans[parent].parent
    return chain


def _under(locator: str, prefix: str) -> bool:
    """The locator is the prefix or continues it at a `.` or `/` boundary, so `1` holds `1.1`
    and `1/a` but not `10`."""
    return locator == prefix or locator.startswith((f"{prefix}.", f"{prefix}/"))


def _match_moves(
    old_spans: Mapping[str, SpanRecord],
    new_spans: Mapping[str, SpanRecord],
    removed: Sequence[str],
    added: Sequence[str],
) -> list[MovedSpan]:
    """Match removed to added spans of equal hash, in document order, one to one.

    A span with no words is never matched: every empty text hashes alike, so two unrelated
    parents that hold only their children would otherwise pair.
    """
    waiting: dict[str, list[str]] = {}
    for locator in added:
        if new_spans[locator].words > 0:
            waiting.setdefault(new_spans[locator].sha256, []).append(locator)
    moves: list[MovedSpan] = []
    for locator in removed:
        if old_spans[locator].words == 0:
            continue
        candidates = waiting.get(old_spans[locator].sha256)
        if candidates:
            moves.append(MovedSpan(locator, candidates.pop(0)))
    return moves


def _span_words(
    index: SpanIndex, text: str, ranges: Mapping[str, tuple[int, int]] | None
) -> dict[str, list[str]]:
    """The normalised words of every span, read from `text` at the span file's offsets.

    A span runs from its offset to the next span record's, which is how the numbered, headings
    and sentences modes cut it, unless `ranges` names the text the span owns, as a boundaries
    file does when it skips text between two spans. A span that hashes differently from its
    record means the text is not the edition the span file was made from.
    """
    lines = LineIndex(text)
    spans = [record for record in index.records if record.kind == "span"]
    words: dict[str, list[str]] = {}
    for position, record in enumerate(spans):
        if ranges is not None and record.locator in ranges:
            begin, end = ranges[record.locator]
        else:
            begin = record.offset
            end = spans[position + 1].offset if position + 1 < len(spans) else len(text)
        joined = lines.joined(begin, max(end, begin)).text
        normalised = normalise(joined)
        if digest(normalised) != record.sha256:
            # A heading's span ends where the next heading's title starts, after its `#` marks.
            normalised = normalise(joined.rstrip().rstrip("#"))
        if digest(normalised) != record.sha256:
            raise SourceError(
                "text-mismatch",
                f"the text does not hold span {record.locator!r} of {record.source!r} as the "
                "span file describes it",
            )
        words[record.locator] = normalised.split()
    return words


def _word_delta(old: Sequence[str], new: Sequence[str]) -> int:
    """How many words differ: for each differing stretch, the longer side's length."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    )


def _pair(old: Mapping[str, list[str]], new: Mapping[str, list[str]]) -> tuple[PairedSpan, ...]:
    """Pair removed with added spans, most alike first, each span used once."""
    candidates: list[tuple[int, int, str, str]] = []
    for old_locator, old_words in old.items():
        for new_locator, new_words in new.items():
            matched = _matching_words(old_words, new_words)
            if matched is not None:
                total = len(old_words) + len(new_words)
                # Larger fraction first: compare matched/total across candidates exactly.
                candidates.append((matched, total, old_locator, new_locator))
    candidates.sort(key=lambda c: (-_fraction_key(c[0], c[1]), c[2], c[3]))
    used_old: set[str] = set()
    used_new: set[str] = set()
    pairs: list[PairedSpan] = []
    for _matched, _total, old_locator, new_locator in candidates:
        if old_locator in used_old or new_locator in used_new:
            continue
        used_old.add(old_locator)
        used_new.add(new_locator)
        pairs.append(
            PairedSpan(old_locator, new_locator, _word_delta(old[old_locator], new[new_locator]))
        )
    return tuple(sorted(pairs, key=lambda p: (p.old, p.new)))


def _fraction_key(matched: int, total: int) -> int:
    """matched/total as an integer in parts per million, so candidates sort without floats."""
    return matched * 1_000_000 // total if total else 0


def _matching_words(old: Sequence[str], new: Sequence[str]) -> int | None:
    """The number of words the two share in order, or None when they are less alike than the
    similarity threshold asks."""
    total = len(old) + len(new)
    if total == 0 or _SIMILARITY_DENOMINATOR * min(len(old), len(new)) < (
        _SIMILARITY_NUMERATOR * total
    ):
        return None
    if _SIMILARITY_DENOMINATOR * sum((Counter(old) & Counter(new)).values()) < (
        _SIMILARITY_NUMERATOR * total
    ):
        return None
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    if _SIMILARITY_DENOMINATOR * matched < _SIMILARITY_NUMERATOR * total:
        return None
    return matched
