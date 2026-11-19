"""The values `sabot_cli.search` accepts and returns."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sabot_cli.vault import Note

type Direction = Literal["out", "in", "both"]


@dataclass(frozen=True)
class EdgeSpec:
    """The edges a traversal may follow, supplied by the caller so no schema is needed here.

    `inverse_of` maps each name in `names` to the name its edge goes by when followed backwards.
    A symmetric edge maps to itself.
    """

    names: frozenset[str]
    inverse_of: Mapping[str, str]


@dataclass(frozen=True, order=True)
class Hop:
    """One step of a path, read in the direction of travel under the name shown to the caller."""

    source: str
    edge: str
    target: str


@dataclass(frozen=True)
class Reached:
    """A note met by a traversal; `note` is None when a link names an id that no note carries.

    `via` is the shortest path found first, in the order described on `traverse`.
    """

    id: str
    note: Note | None
    distance: int
    via: tuple[Hop, ...]


@dataclass(frozen=True)
class Hit:
    """A ranked full-text match; `rank` counts from 1 and `score` is rounded to 4 decimals."""

    note: Note
    rank: int
    score: float
    snippet: str


class InvalidFilterError(ValueError):
    """A filter expression is not one of the supported forms."""


class UnknownNoteError(LookupError):
    """A traversal was asked to start from an id no note carries."""
