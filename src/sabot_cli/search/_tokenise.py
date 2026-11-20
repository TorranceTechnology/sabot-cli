"""Words, phrases and the text of a body, as full text and snippets see them.

A word is a run of ASCII letters and digits, lower-cased, with no stemming: `squall's` is the
words `squall` and `s`. Punctuation is never query syntax, because a query is turned into words
here and nothing else reads it.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sabot_cli.vault import find_wikilinks, parse_wikilink

type Phrase = tuple[str, ...]

_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Token:
    """A word and where it starts and ends in the text it came from."""

    word: str
    start: int
    end: int


def tokens(text: str) -> list[Token]:
    """Every word of `text` in reading order, with its character span."""
    return [
        Token(match.group().lower(), match.start(), match.end()) for match in _WORD.finditer(text)
    ]


def words(text: str) -> list[str]:
    """Every word of `text` in reading order."""
    return [token.word for token in tokens(text)]


def searchable_body(body: str, title: str) -> str:
    """The text of a body that full text indexes.

    The first non-blank line is dropped when it is a level-one heading repeating `title`: the
    title field already holds those words, and counting them again would weight the title
    three times instead of twice. Each wikilink is replaced by its alias, or by its target when
    it has none, which keeps the brackets and any `#heading` part out of the words. Text inside
    code fences is left as written, so it stays searchable.
    """
    text = _without_repeated_title(body, title)
    for raw in dict.fromkeys(find_wikilinks(text)):
        inner = raw[2:-2]
        alias = inner.split("|", 1)[1].strip() if "|" in inner else ""
        text = text.replace(raw, alias or parse_wikilink(raw) or "")
    return text


def _without_repeated_title(body: str, title: str) -> str:
    lines = body.split("\n")
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return body
    heading = lines[first].strip()
    if heading.startswith("# ") and _fold(heading[2:]) == _fold(title):
        return "\n".join(lines[first + 1 :])
    return body


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def query_phrases(arguments: Sequence[str]) -> tuple[Phrase, ...]:
    """Turn command-line terms into distinct phrases in one fixed order.

    An argument that holds several words, whether separated by spaces or by punctuation, is a
    phrase: its words must be adjacent. One that holds a single word is a term. An argument
    with no words is dropped. Sorting makes the result independent of the order given.
    """
    return tuple(sorted({tuple(words(argument)) for argument in arguments} - {()}))
