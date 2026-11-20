"""The span-reference grammar, and the only code that parses it.

`<source-key>#<locator>`, then optionally `/s<n>` for one sentence of the span or `/*` for the
whole subtree. A locator is one or more `/`-separated segments of lowercase letters, digits,
dots and hyphens. A heading whose slug is `s2` cannot be addressed as the last segment of a
locator, because that segment reads as a sentence; locators are refused when they end that way.
"""

import re
from dataclasses import dataclass

from sabot_cli.sources._types import is_source_key

_SEGMENT = r"[a-z0-9]+(?:[.-][a-z0-9]+)*"
_LOCATOR = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT})*")
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

    @property
    def record_span(self) -> str:
        """The `span` of the span-file record the reference names: the span, or one sentence."""
        return self.span if self.sentence is None else sentence_locator(self.span, self.sentence)


def sentence_locator(locator: str, number: int) -> str:
    """The locator of sentence `number` of a span, as a span file records it."""
    return f"{locator}/s{number}"


def sentence_number(locator: str) -> int | None:
    """The `n` of a `<span>/s<n>` sentence locator, or None when it is not one."""
    head, slash, tail = locator.rpartition("/")
    return int(tail[1:]) if slash and head and _SENTENCE_SEGMENT.fullmatch(tail) else None


def locator_under(locator: str, prefix: str) -> bool:
    """Whether a locator is the prefix or continues it at a `.` or `/` boundary, so `1` holds
    `1.1` and `1/a` but not `10`."""
    return locator == prefix or locator.startswith((f"{prefix}.", f"{prefix}/"))


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
    if not separator or not is_source_key(source):
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
