"""The vault, the fixed and merged schemas and the lookups the checks share."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from sabot_cli.rules._checks._model import Report
from sabot_cli.rules._checks._spans_shim import (
    SpanFileError,
    SpanIndex,
    SpanRef,
    parse_span_ref,
    read_spans,
)
from sabot_cli.rules.ruleset import Ruleset
from sabot_cli.rules.schema import Schema
from sabot_cli.vault import Note, Vault

SPANS_DIRECTORY = "spans"


@dataclass(frozen=True)
class Edge:
    """An authored edge whose value is a wikilink: `source` names `target` through `name`."""

    source: Note
    name: str
    target: str


@dataclass(frozen=True)
class Citation:
    """One entry of a note's `source` list: parsed, or the reason it could not be."""

    raw: str
    ref: SpanRef | None
    problem: str | None


def is_present(value: object) -> bool:
    """Whether a front-matter value counts as given: not null, empty text or an empty list."""
    return value is not None and value != "" and value != []


class LintContext:
    """Everything a check reads. Built once per lint run over a vault and its merged schema."""

    def __init__(
        self, vault: Vault, fixed_schema: Schema, schema: Schema, ruleset: Ruleset
    ) -> None:
        self.vault = vault
        self.fixed_schema = fixed_schema
        self.schema = schema
        self.ruleset = ruleset
        self.notes: tuple[Note, ...] = tuple(
            sorted(vault.notes.values(), key=lambda note: note.path.as_posix())
        )
        self._spans: dict[str, tuple[SpanIndex | None, str | None]] = {}

    def report(self, note: Note, message: str) -> Report:
        """A report lying in `note`."""
        return Report(note.path.as_posix(), note.id, message)

    def declared_type(self, note: Note) -> str | None:
        """The note's `type` when it is text, known or not."""
        value = note.properties.get("type")
        return value if isinstance(value, str) else None

    def known_type(self, note: Note) -> str | None:
        """The note's type when the schema, with the ruleset's extensions, declares it."""
        declared = self.declared_type(note)
        return declared if declared in self.schema.node_types else None

    def checked_type(self, note: Note) -> str | None:
        """The fixed node type the note is checked as; an extension type checks as its parent."""
        declared = self.known_type(note)
        return None if declared is None else self.schema.resolve_type(declared)

    def type_names(self, note: Note) -> frozenset[str]:
        """The note's own type and the fixed type it is checked as, for domain-style lookups."""
        names = {name for name in (self.known_type(note), self.checked_type(note)) if name}
        return frozenset(names)

    def target_type_names(self, target: str) -> frozenset[str]:
        """`type_names` of the note with this id, empty when there is none or its type is odd."""
        note = self.vault.notes.get(target)
        return frozenset() if note is None else self.type_names(note)

    @cached_property
    def authored_edges(self) -> tuple[Edge, ...]:
        """Every well-formed link under an edge name, in note order; inverse names are excluded."""
        return tuple(
            Edge(note, link.property, link.target)
            for note in self.notes
            for link in note.links
            if link.target is not None and link.property in self.schema.edge_types
        )

    @cached_property
    def linked_ids(self) -> frozenset[str]:
        """Ids named by a well-formed link of another note, under an edge or an inverse name."""
        return frozenset(
            link.target
            for note in self.notes
            for link in note.links
            if link.target is not None and link.target != note.id
        )

    def edge_targets(self, note: Note, names: frozenset[str] | set[str]) -> tuple[str, ...]:
        """Ids the note names through any of the given edge names, in written order."""
        return tuple(
            link.target for link in note.links if link.target is not None and link.property in names
        )

    @cached_property
    def inbound_by_edge(self) -> Mapping[tuple[str, str], int]:
        """How many authored edges of each name point at each note: key is (edge name, target)."""
        counts: dict[tuple[str, str], int] = {}
        for edge in self.authored_edges:
            counts[(edge.name, edge.target)] = counts.get((edge.name, edge.target), 0) + 1
        return counts

    def citations(self, note: Note) -> tuple[Citation, ...]:
        """The note's `source` entries. A `source` that is not a list yields nothing; the value
        check reports it."""
        value = note.properties.get("source")
        if not isinstance(value, list):
            return ()
        found: list[Citation] = []
        for item in value:
            if not isinstance(item, str):
                continue
            try:
                found.append(Citation(item, parse_span_ref(item), None))
            except ValueError as error:
                found.append(Citation(item, None, str(error)))
        return tuple(found)

    def span_index(self, key: str) -> tuple[SpanIndex | None, str | None]:
        """The span file of a source key, read once, or None with the reason it cannot be."""
        if key not in self._spans:
            path = self.vault.root / SPANS_DIRECTORY / f"{key}.jsonl"
            if not path.is_file():
                self._spans[key] = (None, f"there is no span file {SPANS_DIRECTORY}/{key}.jsonl")
            else:
                try:
                    self._spans[key] = (read_spans(path), None)
                except SpanFileError as error:
                    self._spans[key] = (None, str(error))
        return self._spans[key]
