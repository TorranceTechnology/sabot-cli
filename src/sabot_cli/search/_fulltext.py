"""BM25 over a title field and a body field, behind a small interface.

Only `rank` and `Document` are used by the rest of the package, so another ranking backend can
replace this module without touching callers. Scores are comparable only within one call.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sabot_cli.search._tokenise import Phrase

K1 = 1.2
B = 0.75
TITLE_WEIGHT = 2.0
BODY_WEIGHT = 1.0


@dataclass(frozen=True)
class Document:
    """One searchable note: a key and the words of its two fields."""

    key: str
    title_words: Sequence[str]
    body_words: Sequence[str]


@dataclass(frozen=True)
class Scored:
    """A matching document and its combined score, before rounding."""

    key: str
    score: float


class _Field:
    """Phrase statistics for one field of every document."""

    def __init__(self, texts: Sequence[Sequence[str]], phrases: Sequence[Phrase]) -> None:
        self.lengths = [len(text) for text in texts]
        self.frequencies = [_phrase_counts(text, phrases) for text in texts]
        self.average_length = sum(self.lengths) / len(texts) if texts else 0.0
        self.document_frequency = Counter(
            phrase for counts in self.frequencies for phrase in counts
        )

    def score(self, index: int, phrases: Sequence[Phrase]) -> float:
        counts = self.frequencies[index]
        total = len(self.lengths)
        score = 0.0
        for phrase in phrases:
            frequency = counts.get(phrase, 0)
            if frequency == 0:
                continue
            holding = self.document_frequency[phrase]
            idf = math.log(1 + (total - holding + 0.5) / (holding + 0.5))
            length_ratio = self.lengths[index] / self.average_length
            score += idf * frequency * (K1 + 1) / (frequency + K1 * (1 - B + B * length_ratio))
        return score


def rank(
    documents: Sequence[Document], phrases: Sequence[Phrase], *, require_all: bool
) -> list[Scored]:
    """Score every document that holds one phrase, or every phrase when `require_all`.

    The documents are the whole collection for IDF and length statistics, so a caller that
    wants ranking inside a subset passes only that subset. The result is unordered.
    """
    if not documents or not phrases:
        return []
    title = _Field([document.title_words for document in documents], phrases)
    body = _Field([document.body_words for document in documents], phrases)
    scored: list[Scored] = []
    for index, document in enumerate(documents):
        held = [
            phrase
            for phrase in phrases
            if title.frequencies[index].get(phrase, 0) or body.frequencies[index].get(phrase, 0)
        ]
        if not held or (require_all and len(held) < len(phrases)):
            continue
        combined = TITLE_WEIGHT * title.score(index, phrases) + BODY_WEIGHT * body.score(
            index, phrases
        )
        scored.append(Scored(document.key, combined))
    return scored


def _phrase_counts(text: Sequence[str], phrases: Sequence[Phrase]) -> dict[Phrase, int]:
    """How often each phrase occurs, as adjacent words, in one field; absent means zero."""
    present = set(text)
    counts: dict[Phrase, int] = {}
    for phrase in phrases:
        if not present.issuperset(phrase):
            continue
        found = _occurrences(text, phrase)
        if found:
            counts[phrase] = found
    return counts


def _occurrences(text: Sequence[str], phrase: Phrase) -> int:
    width = len(phrase)
    return sum(
        1 for start in range(len(text) - width + 1) if tuple(text[start : start + width]) == phrase
    )
