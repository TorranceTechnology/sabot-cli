"""The objects a loaded vault is made of."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Link:
    """One value of a link-valued property.

    `target` is None when `raw` is not a wikilink, so a checker can report it by name.
    """

    property: str
    target: str | None
    raw: str


@dataclass(frozen=True)
class Note:
    """A note: its front matter, its body and the links its link-valued properties hold."""

    id: str
    path: PurePosixPath
    properties: Mapping[str, object]
    body: str
    links: tuple[Link, ...]


class ProblemKind(StrEnum):
    """Why the reader could not use a file."""

    UNREADABLE = "unreadable"
    DUPLICATE_ID = "duplicate-id"


@dataclass(frozen=True)
class Problem:
    """A file the reader could not turn into a note, or could not keep."""

    path: PurePosixPath
    kind: ProblemKind
    message: str


@dataclass(frozen=True)
class Vault:
    """Every readable note under a root, keyed by id, and the problems met on the way."""

    root: Path
    notes: Mapping[str, Note]
    problems: tuple[Problem, ...]
