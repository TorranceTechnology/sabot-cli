"""The rule-based modes: turn lines into items without looking at what the words mean."""

import re
import unicodedata
from dataclasses import dataclass, replace

from sabot_cli.sources._items import Item
from sabot_cli.sources._lines import Line, LineIndex, is_page_furniture
from sabot_cli.sources._normalise import digest, normalise
from sabot_cli.sources._sentences import split_sentences
from sabot_cli.sources._types import Numbering

_NUMBER_START: dict[Numbering, re.Pattern[str]] = {
    "dotted": re.compile(r"^(\d+(?:\.\d+)+\.?|\d+\.)\s+(?=\S)"),
    "dotted-letter": re.compile(r"^(\d+(?:\.\d+)+[a-z]?\.?|\d+\.)\s+(?=\S)"),
}
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")


def _content_lines(index: LineIndex) -> list[Line]:
    """Lines that carry prose: neither blank nor page furniture."""
    return [line for line in index.lines if line.text and not is_page_furniture(line.text)]


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


def numbered(index: LineIndex, numbering: Numbering, skip_pages: int) -> list[Item]:
    """One item per numbered line, continuation lines joined; parents follow the numbers."""
    pattern = _NUMBER_START[numbering]
    opened: list[_Open] = []
    for line in _content_lines(index):
        if line.page <= skip_pages:
            continue
        match = pattern.match(line.text)
        if match is not None:
            opened.append(
                _Open(match.group(1).rstrip("."), None, line.page, line.text_offset, line.text_end)
            )
        elif opened:
            opened[-1].end = line.text_end
    known = {item.locator for item in opened}
    return [
        replace(item.freeze(), parent=_nearest_numbered_parent(item.locator, known))
        for item in opened
    ]


def slug(title: str) -> str:
    """Lowercase ASCII letters and digits of a heading, runs of anything else becoming `-`."""
    folded = unicodedata.normalize("NFKD", title).casefold()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def headings(index: LineIndex, numbering: Numbering) -> list[Item]:
    """Items for `#` headings, with numbered lines under a heading as its children."""
    pattern = _NUMBER_START[numbering]
    opened: list[_Open] = []
    stack: list[tuple[int, _Open]] = []
    current: _Open | None = None
    for line in _content_lines(index):
        heading = _HEADING.match(line.text)
        if heading is not None:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1].locator if stack else None
            name = slug(heading.group(2))
            locator = f"{parent}/{name}" if parent else name
            title_start = line.text_offset + heading.start(2)
            current = _Open(
                locator, parent, line.page, title_start, line.text_offset + heading.end(2), True
            )
            stack.append((level, current))
            opened.append(current)
            continue
        if not stack:
            continue
        number = pattern.match(line.text)
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
    return [item.freeze() for item in opened]


def sentences(index: LineIndex) -> list[Item]:
    """One item per sentence, paragraphs (runs of prose lines) being split independently."""
    items: list[Item] = []
    paragraph: list[Line] = []

    def flush() -> None:
        if not paragraph:
            return
        joined = index.joined(paragraph[0].text_offset, paragraph[-1].text_end)
        for begin, finish in split_sentences(joined.text):
            start = joined.source_offset(begin)
            end = joined.source_offset(finish - 1) + 1
            text_digest = digest(normalise(joined.text[begin:finish]))
            items.append(Item(f"s-{text_digest[:8]}", None, index.page_at(start), start, end))
        paragraph.clear()

    for line in index.lines:
        if line.text and not is_page_furniture(line.text):
            paragraph.append(line)
        else:
            flush()
    flush()
    return items
