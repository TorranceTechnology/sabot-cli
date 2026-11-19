"""Measure how long a run of consecutive source words each note body repeats.

Only lengths and offsets are reported; the shared words themselves are never returned.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sabot_cli.sources._lines import is_page_furniture
from sabot_cli.sources._normalise import strip_leading_number
from sabot_cli.sources._types import SourceError

type OverlapStatus = Literal["ok", "warn", "fail"]

_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class NoteOverlap:
    """The longest run of source words in one body.

    `body_start` and `body_end` are character offsets into the body text, the end exclusive; both
    are None when the body shares no word with the source. Of equally long runs the first in the
    body is reported.
    """

    path: str
    length: int
    body_start: int | None
    body_end: int | None
    status: OverlapStatus


@dataclass(frozen=True)
class OverlapReport:
    """One entry per body, in path order, and the thresholds they were judged against."""

    notes: tuple[NoteOverlap, ...]
    fail_at: int
    warn_at: int

    @property
    def failed(self) -> tuple[NoteOverlap, ...]:
        """Entries whose run reaches `fail_at`."""
        return tuple(note for note in self.notes if note.status == "fail")

    @property
    def warned(self) -> tuple[NoteOverlap, ...]:
        """Entries whose run reaches `warn_at` but not `fail_at`."""
        return tuple(note for note in self.notes if note.status == "warn")


def overlap(
    source_text: str, bodies: Mapping[str, str], fail_at: int, warn_at: int
) -> OverlapReport:
    """Find the longest common run of words between the source and each body.

    Words are runs of letters and digits, case folded; punctuation, markup and line breaks
    between them do not matter. Page numbers and contents lines in the source are dropped so a
    run is not cut at a page break. Raises `SourceError` `threshold-invalid` unless
    `1 <= warn_at <= fail_at`.
    """
    if warn_at < 1 or fail_at < warn_at:
        raise SourceError(
            "threshold-invalid", "thresholds must satisfy 1 <= --warn-at <= --fail-at"
        )
    source_words = [word for word, _, _ in _words(_source_prose(source_text))]
    positions: dict[str, list[int]] = {}
    for position, word in enumerate(source_words):
        positions.setdefault(word, []).append(position)
    entries = [_measure(path, bodies[path], positions, fail_at, warn_at) for path in sorted(bodies)]
    return OverlapReport(tuple(entries), fail_at, warn_at)


def _source_prose(text: str) -> str:
    """The source without page furniture and without the rule number each line may start with."""
    kept: list[str] = []
    for line in text.replace("\f", "\n").split("\n"):
        stripped = line.strip()
        if stripped and not is_page_furniture(stripped):
            kept.append(strip_leading_number(stripped))
    return "\n".join(kept)


def _words(text: str) -> list[tuple[str, int, int]]:
    """Each word, case folded and NFKC normalised, with its character span in `text`."""
    return [
        (unicodedata.normalize("NFKC", match.group()).casefold(), match.start(), match.end())
        for match in _WORD.finditer(text)
    ]


def _measure(
    path: str, body: str, positions: Mapping[str, list[int]], fail_at: int, warn_at: int
) -> NoteOverlap:
    words = _words(body)
    best_length, best_end = 0, -1
    # `previous` maps a source position to the length of the common run ending there at the
    # previous body word; a run grows when the next body word follows at the next position.
    previous: dict[int, int] = {}
    for index, (word, _, _) in enumerate(words):
        current: dict[int, int] = {}
        for position in positions.get(word, ()):
            length = previous.get(position - 1, 0) + 1
            current[position] = length
            if length > best_length:
                best_length, best_end = length, index
        previous = current
    if best_length == 0:
        return NoteOverlap(path, 0, None, None, "ok")
    first = words[best_end - best_length + 1]
    last = words[best_end]
    status: OverlapStatus = (
        "fail" if best_length >= fail_at else "warn" if best_length >= warn_at else "ok"
    )
    return NoteOverlap(path, best_length, first[1], last[2], status)
