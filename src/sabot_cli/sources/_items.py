"""An item is one span before it is hashed: a locator, a place in the tree and a text range."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """A span's own text is the prose in [start, end); a parent's range excludes its children.

    `leading_sentence` makes the first line of the range a sentence by itself, which is how a
    heading counts as the first sentence of the section it opens.
    """

    locator: str
    parent: str | None
    page: int
    start: int
    end: int
    leading_sentence: bool = False
