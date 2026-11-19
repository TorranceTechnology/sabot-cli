"""Schema v1 as typed objects, and the merge of a ruleset's extensions into it."""

import re
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from types import MappingProxyType

from sabot_cli.rules.ruleset import (
    ExtEdgeType,
    Ruleset,
    RulesetError,
)
from sabot_cli.vault import load_yaml

SUPPORTED_VERSION = 1
ANY = "*"
_EXTENSION_ATTRIBUTE_TYPES = ("text", "int", "bool", "enum", "list")
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_PREFIX = re.compile(r"^[a-z][a-z0-9-]*\.$")


@dataclass(frozen=True)
class NodeType:
    """A node type; `parent` is None for a fixed type and the fixed type it extends otherwise."""

    name: str
    prefix: str
    side: str
    note: str
    parent: str | None = None

    @property
    def extension(self) -> bool:
        """Whether the ruleset declared this type."""
        return self.parent is not None


@dataclass(frozen=True)
class EdgeType:
    """An edge type; `domain` and `range` hold node types, with `*` for any."""

    name: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    inverse: str
    acyclic: bool
    symmetric: bool
    note: str
    parent: str | None = None

    @property
    def extension(self) -> bool:
        """Whether the ruleset declared this edge type."""
        return self.parent is not None


@dataclass(frozen=True)
class Attribute:
    """A front-matter attribute and the rule its value must meet."""

    name: str
    type: str
    required: bool
    note: str
    on: tuple[str, ...] = ()
    of: str | None = None
    pattern: str | None = None
    minimum: int | None = None
    values: tuple[str, ...] = ()
    values_by_type: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    values_from: str | None = None
    extension: bool = False


@dataclass(frozen=True)
class Requirement:
    """What a node type or a status demands of a note beyond the required attributes."""

    attributes: tuple[str, ...] = ()
    any_edge: tuple[str, ...] = ()
    any_inbound_edge: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    """A schema: the fixed v1 or, after `merge_extensions`, v1 with a ruleset's additions."""

    version: int
    statuses: tuple[str, ...]
    attributes: Mapping[str, Attribute]
    node_types: Mapping[str, NodeType]
    edge_types: Mapping[str, EdgeType]
    construct_kinds: tuple[str, ...]
    type_requirements: Mapping[str, Requirement]
    status_requirements: Mapping[str, Requirement]

    @property
    def link_properties(self) -> frozenset[str]:
        """Every edge name and inverse name; inverses are read so that authoring one is seen."""
        return frozenset(
            name for edge in self.edge_types.values() for name in (edge.name, edge.inverse)
        )

    def resolve_type(self, name: str) -> str | None:
        """The fixed node type a type is checked as, or None when the type is unknown."""
        node_type = self.node_types.get(name)
        if node_type is None:
            return None
        return node_type.parent or node_type.name


@cache
def load_schema(version: int = 1) -> Schema:
    """Return the fixed schema of a version; the packaged data is read once and never changed."""
    if version != SUPPORTED_VERSION:
        raise RulesetError(
            "schema-version-unsupported",
            f"schema version {version} is not supported; this tool reads {SUPPORTED_VERSION}",
        )
    text = (resources.files("sabot_cli.rules") / "schema_v1.yaml").read_text(encoding="utf-8")
    return parse_schema(load_yaml(text))


def parse_schema(raw: object) -> Schema:
    """Build a `Schema` from the parsed schema file, refusing a name clash.

    No edge name or inverse name may equal an attribute name, because both are front-matter
    keys and a key would then mean two things. Raises `ValueError` on any malformed input.
    """
    document = _mapping(raw, "schema")
    statuses = _strings(document.get("statuses"), "statuses")
    attributes = {
        name: _attribute(name, entry)
        for name, entry in _mapping(document.get("attributes"), "attributes").items()
    }
    node_types = {
        name: _node_type(name, entry)
        for name, entry in _mapping(document.get("node-types"), "node-types").items()
    }
    edge_types = {
        name: _edge_type(name, entry)
        for name, entry in _mapping(document.get("edge-types"), "edge-types").items()
    }
    clashes = sorted(
        {name for edge in edge_types.values() for name in (edge.name, edge.inverse)}
        & set(attributes)
    )
    if clashes:
        raise ValueError(f"edge or inverse names equal attribute names: {', '.join(clashes)}")
    version = document.get("schema-version")
    if not isinstance(version, int):
        raise ValueError("schema-version must be an integer")
    return Schema(
        version=version,
        statuses=statuses,
        attributes=MappingProxyType(attributes),
        node_types=MappingProxyType(node_types),
        edge_types=MappingProxyType(edge_types),
        construct_kinds=_strings(document.get("construct-kinds"), "construct-kinds"),
        type_requirements=MappingProxyType(_requirements(document.get("type-requirements"))),
        status_requirements=MappingProxyType(_requirements(document.get("status-requirements"))),
    )


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{where} must be a mapping with text keys")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where} must be a list of text")
    return tuple(str(item) for item in value)


def _text(entry: Mapping[str, object], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{where}.{key} must be text")
    return value


def _attribute(name: str, raw: object) -> Attribute:
    entry = _mapping(raw, f"attributes.{name}")
    by_type = {
        node_type: _strings(values, f"attributes.{name}.values-by-type.{node_type}")
        for node_type, values in _mapping(
            entry.get("values-by-type") or {}, f"attributes.{name}.values-by-type"
        ).items()
    }
    minimum = entry.get("minimum")
    pattern = entry.get("pattern")
    of = entry.get("of")
    values_from = entry.get("values-from")
    return Attribute(
        name=name,
        type=_text(entry, "type", f"attributes.{name}"),
        required=entry.get("required") is True,
        note=_text(entry, "note", f"attributes.{name}"),
        on=_strings(entry.get("on"), f"attributes.{name}.on"),
        of=of if isinstance(of, str) else None,
        pattern=pattern if isinstance(pattern, str) else None,
        minimum=minimum if isinstance(minimum, int) else None,
        values=_strings(entry.get("values"), f"attributes.{name}.values"),
        values_by_type=by_type,
        values_from=values_from if isinstance(values_from, str) else None,
    )


def _node_type(name: str, raw: object) -> NodeType:
    entry = _mapping(raw, f"node-types.{name}")
    where = f"node-types.{name}"
    return NodeType(
        name,
        _text(entry, "prefix", where),
        _text(entry, "side", where),
        _text(entry, "note", where),
    )


def _edge_type(name: str, raw: object) -> EdgeType:
    where = f"edge-types.{name}"
    entry = _mapping(raw, where)
    return EdgeType(
        name=name,
        domain=_strings(entry.get("domain"), f"{where}.domain"),
        range=_strings(entry.get("range"), f"{where}.range"),
        inverse=_text(entry, "inverse", where),
        acyclic=entry.get("acyclic") is True,
        symmetric=entry.get("symmetric") is True,
        note=_text(entry, "note", where),
    )


def _requirements(raw: object) -> dict[str, Requirement]:
    return {
        name: Requirement(
            attributes=_strings(_mapping(entry, name).get("attributes"), f"{name}.attributes"),
            any_edge=_strings(_mapping(entry, name).get("any-edge"), f"{name}.any-edge"),
            any_inbound_edge=_strings(
                _mapping(entry, name).get("any-inbound-edge"), f"{name}.any-inbound-edge"
            ),
        )
        for name, entry in _mapping(raw or {}, "requirements").items()
    }


def ruleset_problems(
    schema: Schema, ruleset: Ruleset, note_ids: Collection[str] | None = None
) -> tuple[str, ...]:
    """Say what is wrong with a ruleset's declarations, as sorted one-line messages.

    `schema` is the fixed schema. Sources are checked against `note_ids` only when it is given.
    """
    problems: list[str] = []
    if ruleset.schema_version != schema.version:
        problems.append(
            f"schemaVersion {ruleset.schema_version} differs from schema version {schema.version}"
        )
    problems.extend(_source_problems(ruleset, note_ids))
    problems.extend(_extension_problems(schema, ruleset))
    return tuple(sorted(problems))


def _source_problems(ruleset: Ruleset, note_ids: Collection[str] | None) -> list[str]:
    problems: list[str] = []
    keys = [source.key for source in ruleset.sources]
    for source in ruleset.sources:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", source.key):
            problems.append(f"source key {source.key!r} is not a valid key")
        if keys.count(source.key) > 1:
            problems.append(f"source key {source.key!r} is registered more than once")
        if note_ids is not None and source.note not in note_ids:
            problems.append(f"source {source.key!r} names the missing note {source.note!r}")
        if source.supersedes is not None and source.supersedes not in keys:
            problems.append(
                f"source {source.key!r} supersedes the unregistered {source.supersedes!r}"
            )
        if source.supersedes == source.key:
            problems.append(f"source {source.key!r} supersedes itself")
    problems.extend(
        f"scope names the unregistered source {key!r}" for key in ruleset.scope if key not in keys
    )
    return list(dict.fromkeys(problems))


def _extension_problems(schema: Schema, ruleset: Ruleset) -> list[str]:
    extensions = ruleset.extensions
    problems: list[str] = []
    reserved = set(schema.attributes) | {
        name for edge in schema.edge_types.values() for name in (edge.name, edge.inverse)
    }
    declared_types = set(extensions.node_types)
    prefixes = {node_type.prefix: name for name, node_type in schema.node_types.items()}
    for name, node_type in sorted(extensions.node_types.items()):
        where = f"node type {name!r}"
        if not _NAME.match(name):
            problems.append(f"{where} is not a lowercase hyphenated name")
        if name in schema.node_types:
            problems.append(f"{where} redefines a fixed node type")
        if node_type.parent not in schema.node_types:
            problems.append(
                f"{where} has the parent {node_type.parent!r}, which is not a fixed node type"
            )
        if not _PREFIX.match(node_type.prefix):
            problems.append(f"{where} has the malformed prefix {node_type.prefix!r}")
        elif node_type.prefix in prefixes:
            problems.append(f"{where} reuses the prefix of {prefixes[node_type.prefix]!r}")
        else:
            prefixes[node_type.prefix] = name
    known_types = set(schema.node_types) | declared_types | {ANY}
    uses = Counter(
        used for name, edge in extensions.edge_types.items() for used in {name, edge.inverse}
    )
    for name, edge in sorted(extensions.edge_types.items()):
        where = f"edge type {name!r}"
        if not _NAME.match(name):
            problems.append(f"{where} is not a lowercase hyphenated name")
        problems.extend(
            f"{where} uses the name {used!r}, which is already taken"
            for used in sorted({name, edge.inverse})
            if used in reserved or uses[used] > 1
        )
        if edge.parent not in schema.edge_types:
            problems.append(
                f"{where} has the parent {edge.parent!r}, which is not a fixed edge type"
            )
        problems.extend(
            f"{where} names the unknown node type {node_type!r}"
            for node_type in (*(edge.domain or ()), *(edge.range or ()))
            if node_type not in known_types
        )
    for name, attribute in sorted(extensions.attributes.items()):
        where = f"attribute {name!r}"
        if not _NAME.match(name):
            problems.append(f"{where} is not a lowercase hyphenated name")
        if name in reserved or name in extensions.edge_types:
            problems.append(f"{where} reuses a fixed attribute or edge name")
        if attribute.type not in _EXTENSION_ATTRIBUTE_TYPES:
            problems.append(f"{where} has the unknown type {attribute.type!r}")
        if attribute.type == "enum" and not attribute.values:
            problems.append(f"{where} is an enum with no values")
        problems.extend(
            f"{where} applies to the unknown node type {node_type!r}"
            for node_type in attribute.on
            if node_type not in known_types - {ANY}
        )
    for name in sorted(extensions.enums):
        fixed = schema.attributes.get(name)
        if fixed is None or fixed.type != "enum" or fixed.values_from is not None:
            problems.append(f"enum {name!r} is not a fixed enum attribute that can be extended")
    return problems


def merge_extensions(schema: Schema, ruleset: Ruleset) -> Schema:
    """Return `schema` with the ruleset's extensions added, each marked with its parent.

    Raises `RulesetError` (`ruleset-invalid`) when the declarations have problems, because a
    merge over them would be undefined.
    """
    if problems := sorted(_extension_problems(schema, ruleset)):
        raise RulesetError("ruleset-invalid", f"_ruleset.yaml: {problems[0]}")
    extensions = ruleset.extensions
    node_types = dict(schema.node_types)
    for name, declared in extensions.node_types.items():
        parent = schema.node_types[declared.parent]
        node_types[name] = NodeType(
            name, declared.prefix, parent.side, declared.note, declared.parent
        )
    edge_types = dict(schema.edge_types)
    for name, edge in extensions.edge_types.items():
        edge_types[name] = _merged_edge(schema, name, edge)
    attributes = dict(schema.attributes)
    for name, declared_attribute in extensions.attributes.items():
        attributes[name] = Attribute(
            name=name,
            type=declared_attribute.type,
            required=False,
            note="Declared by the ruleset.",
            on=declared_attribute.on,
            values=declared_attribute.values,
            extension=True,
        )
    for name, added in extensions.enums.items():
        fixed = attributes[name]
        attributes[name] = Attribute(
            name=fixed.name,
            type=fixed.type,
            required=fixed.required,
            note=fixed.note,
            on=fixed.on,
            of=fixed.of,
            pattern=fixed.pattern,
            minimum=fixed.minimum,
            values=_appended(fixed.values, added),
            values_by_type={
                node_type: _appended(values, added)
                for node_type, values in fixed.values_by_type.items()
            },
            values_from=fixed.values_from,
            extension=fixed.extension,
        )
    return Schema(
        version=schema.version,
        statuses=schema.statuses,
        attributes=MappingProxyType(attributes),
        node_types=MappingProxyType(node_types),
        edge_types=MappingProxyType(edge_types),
        construct_kinds=schema.construct_kinds,
        type_requirements=schema.type_requirements,
        status_requirements=schema.status_requirements,
    )


def _merged_edge(schema: Schema, name: str, edge: ExtEdgeType) -> EdgeType:
    parent = schema.edge_types[edge.parent]
    return EdgeType(
        name=name,
        domain=edge.domain if edge.domain is not None else parent.domain,
        range=edge.range if edge.range is not None else parent.range,
        inverse=edge.inverse,
        acyclic=edge.acyclic,
        symmetric=edge.symmetric,
        note=edge.note,
        parent=edge.parent,
    )


def _appended(values: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        return values
    return values + tuple(value for value in added if value not in values)
