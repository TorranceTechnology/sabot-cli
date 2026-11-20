"""The span-reference grammar, and the only code that parses it.

`<source-key>#<locator>`, then optionally `/s<n>` for one sentence of the span or `/*` for the
whole subtree. A locator is one or more `/`-separated segments of lowercase letters, digits,
dots and hyphens. A heading whose slug is `s2` cannot be addressed as the last segment of a
locator, because that segment reads as a sentence; locators are refused when they end that way.
"""

import re
from dataclasses import dataclass

from sabot_cli.sources._types import SOURCE_KEY_PATTERN

_SEGMENT = r"[a-z0-9]+(?:[.-][a-z0-9]+)*"
_LOCATOR = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")
_SOURCE_KEY = re.compile(SOURCE_KEY_PATTERN)
_SENTENCE_SEGMENT = re.compile(r"s[1-9][0-9]*")
_SENTENCE_LIKE = re.compile(r"s[0-9]+")


class SpanRefError(ValueError):
    """The text is not a span reference; the message says which part is wrong."""


@dataclass(frozen=True)
class SpanRef:
    """A parsed reference: the span it names, and whether it narrows to a sentence or widens."""

    source: str
    locator: str
    sentence: int | None = None
    subtree: bool = False

    @property
    def span(self) -> str:
        """The `<source>#<locator>` form of the span itself, as a span file spells it."""
        return f"{self.source}#{self.locator}"


def locator_problem(locator: str) -> str | None:
    """Why a string cannot be a locator, or None when it can."""
    if not _LOCATOR.fullmatch(locator):
        return "a locator is lowercase letters, digits, dots and hyphens, with / between parts"
    if _SENTENCE_LIKE.fullmatch(locator.rsplit("/", 1)[-1]):
        return "a locator may not end in a part that reads as a sentence, such as s1"
    return None


def parse_span_ref(ref: str) -> SpanRef:
    """Parse one span reference; raise `SpanRefError` when it does not fit the grammar."""
    source, separator, rest = ref.partition("#")
    if not separator or not _SOURCE_KEY.fullmatch(source):
        raise SpanRefError("a span reference is <source-key>#<locator>")
    sentence: int | None = None
    subtree = False
    head, slash, tail = rest.rpartition("/")
    if slash and tail == "*":
        rest, subtree = head, True
    elif slash and _SENTENCE_SEGMENT.fullmatch(tail):
        rest, sentence = head, int(tail[1:])
    if (problem := locator_problem(rest)) is not None:
        raise SpanRefError(problem)
    return SpanRef(source, rest, sentence, subtree)
