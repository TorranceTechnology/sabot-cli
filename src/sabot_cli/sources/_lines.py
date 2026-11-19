"""The extracted text as lines with page numbers and offsets, and the text a range holds.

Offsets are character offsets into the extracted text. A form feed starts a new page, which is
how `pdftotext -layout` separates them; text without one is a single page.
"""

import bisect
import re
from dataclasses import dataclass

_PAGE_NUMBER = re.compile(r"^(?:-\s*\d+\s*-|page\s+\d+(?:\s+of\s+\d+)?)$", re.IGNORECASE)
_DOT_LEADER = re.compile(r"\.{6,}\s*\d+$")


@dataclass(frozen=True)
class Line:
    """One physical line: where it starts, its content without the newline, and its page."""

    start: int
    content: str
    page: int

    @property
    def text(self) -> str:
        """The content without surrounding whitespace."""
        return self.content.strip()

    @property
    def text_offset(self) -> int:
        """The offset of the first character that is not whitespace."""
        return self.start + len(self.content) - len(self.content.lstrip())

    @property
    def text_end(self) -> int:
        """The offset just past the last character that is not whitespace."""
        return self.start + len(self.content.rstrip())


def is_page_furniture(text: str) -> bool:
    """Whether a stripped line is a page number or a contents entry with a dot leader."""
    return bool(_PAGE_NUMBER.match(text) or _DOT_LEADER.search(text))


@dataclass(frozen=True)
class JoinedText:
    """The prose of a range: its content lines joined by single spaces, with offset lookup."""

    text: str
    _starts: tuple[int, ...]
    _offsets: tuple[int, ...]

    def source_offset(self, index: int) -> int:
        """The offset in the extracted text of a character of `text`."""
        piece = bisect.bisect_right(self._starts, index) - 1
        return self._offsets[piece] + (index - self._starts[piece])

    @property
    def first_line_length(self) -> int:
        """The length of the first joined line, which is all of `text` when there is one."""
        return self._starts[1] - 1 if len(self._starts) > 1 else len(self.text)

    @property
    def first_offset(self) -> int | None:
        """Where the first character of `text` lies in the extracted text; None if empty."""
        return self._offsets[0] if self._offsets else None


class LineIndex:
    """All lines of one text, found by offset without rescanning it."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines: list[Line] = []
        offset = 0
        for page_number, page in enumerate(text.split("\f"), start=1):
            for content in page.split("\n"):
                self.lines.append(Line(offset, content, page_number))
                offset += len(content) + 1
        self._starts = [line.start for line in self.lines]

    def line_at(self, offset: int) -> Line:
        """The line holding `offset`; offsets past the end belong to the last line."""
        return self.lines[max(0, bisect.bisect_right(self._starts, offset) - 1)]

    def page_at(self, offset: int) -> int:
        """The page of the first non-whitespace character at or after `offset`."""
        for line in self.lines[bisect.bisect_right(self._starts, offset) - 1 :]:
            if line.text and line.text_end > offset:
                return line.page
        return self.lines[-1].page

    def joined(self, start: int, end: int) -> JoinedText:
        """The prose in [start, end): page furniture and blank lines dropped, lines joined."""
        pieces: list[tuple[int, str]] = []
        first = max(0, bisect.bisect_right(self._starts, start) - 1)
        for line in self.lines[first:]:
            if line.start >= end:
                break
            low = max(start, line.start) - line.start
            high = min(end, line.start + len(line.content)) - line.start
            piece = line.content[low:high]
            stripped = piece.strip()
            if stripped and not is_page_furniture(stripped):
                pieces.append((line.start + low + len(piece) - len(piece.lstrip()), stripped))
        starts: list[int] = []
        position = 0
        for _, stripped in pieces:
            starts.append(position)
            position += len(stripped) + 1
        return JoinedText(
            " ".join(stripped for _, stripped in pieces),
            tuple(starts),
            tuple(offset for offset, _ in pieces),
        )
