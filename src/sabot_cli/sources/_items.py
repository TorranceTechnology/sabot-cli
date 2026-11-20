"""An item is one span before it is hashed: a locator, a place in the tree and a text range."""

from dataclasses import dataclass
from typing import Literal


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


type ContinuationKind = Literal["wrapped", "unopened", "furniture"]


@dataclass(frozen=True)
class Continuation:
    """A line that begins with a number and did not open a rule.

    `line` counts physical lines of the text from 1, `number` is the number the line begins
    with, and `span` is the locator of the span the line was joined to, or None when it is in no
    span. `kind` says why it did not open a rule: `wrapped`, a decimal that a line broke before
    and that was joined to the rule before it; `unopened`, a line that looks numbered but is not
    in the form a rule is read in (a bare number on a line of its own, `4.2)`, `5 Scoring`, a
    lettered number in dotted mode), which joined the open rule or, with none open, is in no
    span; `furniture`, a page number or contents line that begins with a number and was dropped,
    so it is in no span.
    """

    line: int
    number: str
    span: str | None
    kind: ContinuationKind = "wrapped"
