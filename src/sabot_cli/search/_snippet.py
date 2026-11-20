"""The short excerpt shown beside a ranked match."""

from collections.abc import Sequence

from sabot_cli.search._tokenise import Phrase, tokens

_BEFORE = 60
_AFTER = 100
_ELLIPSIS = "..."


def snippet(title: str, body: str, phrases: Sequence[Phrase]) -> str:
    """Excerpt the body around its first match, or give the title when only the title matches.

    `body` is the text as searched. Whitespace runs collapse to one space so the excerpt is a
    single line, and a cut is made at a space when one is in range and marked with `...`.
    """
    text = " ".join(body.split())
    found = _first_match(text, phrases)
    if found is None:
        return " ".join(title.split())
    match_start, match_end = found
    begin = max(0, match_start - _BEFORE)
    if begin > 0 and (space := text.find(" ", begin, match_start)) != -1:
        begin = space + 1
    end = min(len(text), match_end + _AFTER)
    if end < len(text) and (space := text.rfind(" ", match_end, end)) != -1:
        end = space
    prefix = _ELLIPSIS if begin > 0 else ""
    suffix = _ELLIPSIS if end < len(text) else ""
    return f"{prefix}{text[begin:end]}{suffix}"


def _first_match(text: str, phrases: Sequence[Phrase]) -> tuple[int, int] | None:
    """Character span of the earliest place any phrase occurs as adjacent words."""
    found = tokens(text)
    words = [token.word for token in found]
    for index in range(len(words)):
        for phrase in phrases:
            width = len(phrase)
            if tuple(words[index : index + width]) == phrase:
                return found[index].start, found[index + width - 1].end
    return None
