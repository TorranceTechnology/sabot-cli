"""The rule-based modes: turn lines into items without looking at what the words mean."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace

from sabot_cli.sources._items import Continuation, Item
from sabot_cli.sources._lines import Line, LineIndex, is_page_furniture
from sabot_cli.sources._normalise import digest, normalise
from sabot_cli.sources._sentences import split_sentences
from sabot_cli.sources._types import Numbering

_NUMBER_START: dict[Numbering, re.Pattern[str]] = {
    "dotted": re.compile(r"^(\d+(?:\.\d+)+\.?|\d+\.)\s+(?=\S)"),
    "dotted-letter": re.compile(r"^(\d+(?:\.\d+)+[a-z]?\.?|\d+\.)\s+(?=\S)"),
}
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")
_SENTENCE_END = ".!?:;)\"'\u201d\u2019"


_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)([a-z]?)$")


def _expected_numbers(locator: str) -> set[str]:
    """The numbers a list of rules reaches next after `locator`.

    Its first child (`4.2` then `4.2.1` or `4.2a`), its next sibling (`4.2` then `4.3`,
    `4.2a` then `4.2b`) and the next sibling of each ancestor (`4.2c` then `4.3`, then `5`).
    A number that is none of these may be a decimal inside a sentence that a line broke before.
    """
    match = _NUMBER.match(locator.rsplit("/", 1)[-1])
    if match is None:
        return set()
    base, letter = match.groups()
    parts = base.split(".")
    expected: set[str] = set()
    if letter:
        if letter < "z":
            expected.add(f"{base}{chr(ord(letter) + 1)}")
    else:
        expected |= {f"{base}.1", f"{base}a"}
    for depth in range(len(parts), 0, -1):
        expected.add(".".join([*parts[: depth - 1], str(int(parts[depth - 1]) + 1)]))
    return expected


def _wraps_onto(
    previous: str | None, rest: str, *, number: str, open_locator: str | None, separated: bool
) -> bool:
    """Whether a line that begins with a rule number is a wrapped continuation instead.

    A decimal that a long line happens to break before (`... a bonus of` / `2.5 times ...`)
    reads as a rule number. It is a continuation only when all of these hold: a rule is open to
    continue; the line follows the one before it with no blank line between; its number is not
    the one the list reaches next (see `_expected_numbers`); the line before stops short of the
    end of a sentence; and the text after the number starts in lower case, which a rule does not.
    """
    if separated or not previous or open_locator is None:
        return False
    if number in _expected_numbers(open_locator):
        return False
    return previous[-1] not in _SENTENCE_END and rest[:1].islower()


def _prose_lines(index: LineIndex) -> Iterator[tuple[Line, bool]]:
    """Lines that carry prose, each with whether a blank line (or the start) comes before it.

    Page furniture is skipped without counting as a separator, since a page number can sit
    between the halves of a wrapped line; a blank line does count.
    """
    separated = True
    for line in index.lines:
        if not line.text:
            separated = True
        elif not is_page_furniture(line.text):
            yield line, separated
            separated = False


def _nearest_numbered_parent(locator: str, known: set[str]) -> str | None:
    """The closest shorter number that exists: `4.2a` under `4.2`, `4.2` under `4`."""
    candidate = locator
    while True:
        if candidate[-1].isalpha():
            candidate = candidate[:-1]
        elif "." in candidate:
            candidate = candidate.rsplit(".", 1)[0]
        else:
            return None
        if candidate in known:
            return candidate


@dataclass
class _Open:
    """An item still collecting continuation lines."""

    locator: str
    parent: str | None
    page: int
    start: int
    end: int
    leading_sentence: bool = False

    def freeze(self) -> Item:
        return Item(
            self.locator, self.parent, self.page, self.start, self.end, self.leading_sentence
        )


def _continuation(index: LineIndex, line: Line, number: str, span: str) -> Continuation:
    return Continuation(index.text.count("\n", 0, line.start) + 1, number, span)


def numbered(
    index: LineIndex, numbering: Numbering, skip_pages: int
) -> tuple[list[Item], list[Continuation]]:
    """One item per numbered line, continuation lines joined; parents follow the numbers.

    The second result lists each numbered line that was read as a continuation instead of a
    rule, so the decision is visible to whoever reviews the segmentation.
    """
    pattern = _NUMBER_START[numbering]
    opened: list[_Open] = []
    continuations: list[Continuation] = []
    previous: str | None = None
    for line, separated in _prose_lines(index):
        if line.page <= skip_pages:
            continue
        match = pattern.match(line.text)
        wrapped = match is not None and _wraps_onto(
            previous,
            line.text[match.end() :],
            number=match.group(1).rstrip("."),
            open_locator=opened[-1].locator if opened else None,
            separated=separated,
        )
        previous = line.text
        if match is not None and not wrapped:
            opened.append(
                _Open(match.group(1).rstrip("."), None, line.page, line.text_offset, line.text_end)
            )
        elif opened:
            if match is not None:
                continuations.append(
                    _continuation(index, line, match.group(1).rstrip("."), opened[-1].locator)
                )
            opened[-1].end = line.text_end
    known = {item.locator for item in opened}
    items = [
        replace(item.freeze(), parent=_nearest_numbered_parent(item.locator, known))
        for item in opened
    ]
    return items, continuations


def slug(title: str) -> str:
    """Lowercase ASCII letters and digits of a heading, runs of anything else becoming `-`."""
    decomposed = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def headings(index: LineIndex, numbering: Numbering) -> tuple[list[Item], list[Continuation]]:
    """Items for `#` headings, with numbered lines under a heading as its children.

    The second result lists each numbered line that was read as a continuation, as `numbered`
    does.
    """
    pattern = _NUMBER_START[numbering]
    opened: list[_Open] = []
    continuations: list[Continuation] = []
    stack: list[tuple[int, _Open]] = []
    current: _Open | None = None
    previous: str | None = None
    used: set[str] = set()
    for line, separated in _prose_lines(index):
        heading = _HEADING.match(line.text)
        if heading is not None:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1].locator if stack else None
            name = slug(heading.group(2))
            base = f"{parent}/{name}" if parent else name
            # A heading repeated under the same parent keeps its first locator; later ones
            # count up (`notes`, `notes-2`), skipping a locator another heading already has.
            locator, occurrence = base, 1
            while locator in used:
                occurrence += 1
                locator = f"{base}-{occurrence}"
            used.add(locator)
            title_start = line.text_offset + heading.start(2)
            current = _Open(
                locator, parent, line.page, title_start, line.text_offset + heading.end(2), True
            )
            stack.append((level, current))
            opened.append(current)
            previous = None
            continue
        if not stack:
            continue
        number = pattern.match(line.text)
        if (
            number is not None
            and current is not None
            and _wraps_onto(
                previous,
                line.text[number.end() :],
                number=number.group(1).rstrip("."),
                open_locator=current.locator,
                separated=separated,
            )
        ):
            continuations.append(
                _continuation(index, line, number.group(1).rstrip("."), current.locator)
            )
            number = None
        previous = line.text
        if number is not None:
            owner = stack[-1][1].locator
            current = _Open(
                f"{owner}/{number.group(1).rstrip('.')}",
                owner,
                line.page,
                line.text_offset,
                line.text_end,
            )
            opened.append(current)
        elif current is not None:
            current.end = line.text_end
    return [item.freeze() for item in opened], continuations


def sentences(index: LineIndex) -> list[Item]:
    """One item per sentence, paragraphs (runs of prose lines) being split independently."""
    items: list[Item] = []
    paragraph: list[Line] = []
    seen: Counter[str] = Counter()

    def flush() -> None:
        if not paragraph:
            return
        joined = index.joined(paragraph[0].text_offset, paragraph[-1].text_end)
        for begin, finish in split_sentences(joined.text):
            start = joined.source_offset(begin)
            end = joined.source_offset(finish - 1) + 1
            text_digest = digest(normalise(joined.text[begin:finish]))
            locator = f"s-{text_digest[:8]}"
            seen[locator] += 1
            if seen[locator] > 1:
                # A sentence repeated verbatim keeps its first locator; later ones count up.
                locator = f"{locator}-{seen[locator]}"
            items.append(Item(locator, None, index.page_at(start), start, end))
        paragraph.clear()

    for line in index.lines:
        if line.text and not is_page_furniture(line.text):
            paragraph.append(line)
        else:
            flush()
    flush()
    return items
