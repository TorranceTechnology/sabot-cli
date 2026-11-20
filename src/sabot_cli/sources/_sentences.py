"""Sentence splitting for English prose.

A boundary is a terminator (`.`, `!` or `?`, with closing quotes or brackets) followed by
whitespace and then a capital letter, a digit or an opening quote. A full stop after a known
abbreviation or a single capital letter is not a terminator. Prose the rules do not fit
(sentences that start in lower case, unlisted abbreviations) is split wrongly, and a span file
built from it records those splits.
"""

import re

_CLOSERS = "\"')\\]\N{RIGHT SINGLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
_OPENERS = "\"'(\\[\N{LEFT SINGLE QUOTATION MARK}\N{LEFT DOUBLE QUOTATION MARK}"
_CANDIDATE = re.compile(rf"[.!?]+[{_CLOSERS}]*\s+")
_NEXT_STARTS_SENTENCE = re.compile(rf"[A-Z0-9{_OPENERS}]")
_PRECEDING_WORD = re.compile(r"([A-Za-z.]+)$")
_ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "etc", "vs", "cf", "no", "fig", "approx", "mr", "mrs", "ms", "dr", "st"}
)


def _ends_abbreviation(before: str) -> bool:
    match = _PRECEDING_WORD.search(before)
    if match is None:
        return False
    word = match.group(1)
    return word.casefold() in _ABBREVIATIONS or (len(word) == 1 and word.isupper())


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) index pairs of each sentence in `text`, whitespace excluded."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _CANDIDATE.finditer(text):
        following = text[match.end() : match.end() + 1]
        terminator = match.group().strip()
        if not following or not _NEXT_STARTS_SENTENCE.match(following):
            continue
        if terminator.startswith(".") and _ends_abbreviation(text[start : match.start()]):
            continue
        spans.append((start, match.start() + len(terminator)))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return spans
