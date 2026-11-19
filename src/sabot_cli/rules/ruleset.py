"""`_ruleset.yaml`: the ruleset's identity, its sources, its scope and its schema extensions."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from sabot_cli.output import JsonValue
from sabot_cli.vault import load_yaml

RULESET_FILE = "_ruleset.yaml"
_TOP_LEVEL_KEYS = frozenset({"ruleset", "schemaVersion", "sources", "scope", "extensions"})
_EXTENSION_KEYS = frozenset({"nodeTypes", "edgeTypes", "attributes", "enums"})
_SOURCE_KEYS = frozenset({"key", "note", "supersedes"})
_NODE_TYPE_KEYS = frozenset({"parent", "prefix", "note"})
_EDGE_TYPE_KEYS = frozenset(
    {"parent", "domain", "range", "inverse", "note", "acyclic", "symmetric"}
)
_ATTRIBUTE_KEYS = frozenset({"on", "type", "values"})


class RulesetError(Exception):
    """The vault or its ruleset file cannot be used; `code` is the refusal's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RulesetSource:
    """One registered source: its key, the id of its source note and what it supersedes."""

    key: str
    note: str
    supersedes: str | None = None


@dataclass(frozen=True)
class ExtNodeType:
    """A declared node type, checked as its fixed parent."""

    parent: str
    prefix: str
    note: str = ""


@dataclass(frozen=True)
class ExtEdgeType:
    """A declared edge type; an absent domain or range is the parent's."""

    parent: str
    inverse: str
    domain: tuple[str, ...] | None = None
    range: tuple[str, ...] | None = None
    note: str = ""
    acyclic: bool = False
    symmetric: bool = False


@dataclass(frozen=True)
class ExtAttribute:
    """A declared attribute: the node types it applies to, its type and any enum values."""

    on: tuple[str, ...]
    type: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Extensions:
    """Everything `_ruleset.yaml` adds to the fixed schema."""

    node_types: Mapping[str, ExtNodeType]
    edge_types: Mapping[str, ExtEdgeType]
    attributes: Mapping[str, ExtAttribute]
    enums: Mapping[str, tuple[str, ...]]

    def count(self) -> int:
        """The number of declared entries, which `compile` reports."""
        return len(self.node_types) + len(self.edge_types) + len(self.attributes) + len(self.enums)

    def to_json(self) -> dict[str, JsonValue]:
        """The declarations as the compiled graph records them."""
        return {
            "attributes": {
                name: {"on": list(item.on), "type": item.type, "values": list(item.values)}
                for name, item in sorted(self.attributes.items())
            },
            "edgeTypes": {name: _edge_json(item) for name, item in sorted(self.edge_types.items())},
            "enums": {name: list(values) for name, values in sorted(self.enums.items())},
            "nodeTypes": {
                name: {"note": item.note, "parent": item.parent, "prefix": item.prefix}
                for name, item in sorted(self.node_types.items())
            },
        }


def _edge_json(item: ExtEdgeType) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "acyclic": item.acyclic,
        "inverse": item.inverse,
        "note": item.note,
        "parent": item.parent,
        "symmetric": item.symmetric,
    }
    if item.domain is not None:
        document["domain"] = list(item.domain)
    if item.range is not None:
        document["range"] = list(item.range)
    return document


@dataclass(frozen=True)
class Ruleset:
    """The parsed `_ruleset.yaml` of a vault."""

    ruleset: str
    schema_version: int
    sources: tuple[RulesetSource, ...]
    scope: Mapping[str, tuple[str, ...]]
    extensions: Extensions


def load_ruleset(root: Path) -> Ruleset:
    """Read and structurally validate `<root>/_ruleset.yaml`.

    Raises `RulesetError` with code `not-a-vault` when the file is absent and `ruleset-invalid`
    when it is unreadable or does not have the shape of section 5.6. Whether the declarations
    make sense against the schema is a separate question, answered by `ruleset_problems`.
    """
    path = root / RULESET_FILE
    if not path.is_file():
        raise RulesetError("not-a-vault", f"the directory has no {RULESET_FILE}")
    try:
        loaded = load_yaml(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise RulesetError("ruleset-invalid", f"{RULESET_FILE} cannot be read: {error}") from error
    return _parse(loaded)


def _parse(loaded: object) -> Ruleset:
    top = _mapping(loaded, RULESET_FILE, _TOP_LEVEL_KEYS)
    ruleset_id = top.get("ruleset")
    if not isinstance(ruleset_id, str) or not ruleset_id:
        raise _invalid("ruleset", "must be a non-empty id")
    version = top.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _invalid("schemaVersion", "must be an integer")
    sources = tuple(
        _parse_source(item, index)
        for index, item in enumerate(_list(top.get("sources"), "sources"))
    )
    scope_raw = _mapping(top.get("scope"), "scope")
    scope = {key: _text_list(value, f"scope.{key}") for key, value in scope_raw.items()}
    return Ruleset(ruleset_id, version, sources, scope, _parse_extensions(top.get("extensions")))


def _parse_source(item: object, index: int) -> RulesetSource:
    where = f"sources[{index}]"
    entry = _mapping(item, where, _SOURCE_KEYS)
    key, note = entry.get("key"), entry.get("note")
    if not isinstance(key, str) or not isinstance(note, str):
        raise _invalid(where, "needs a text key and a text note")
    supersedes = entry.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise _invalid(f"{where}.supersedes", "must be a source key")
    return RulesetSource(key, note, supersedes)


def _parse_extensions(raw: object) -> Extensions:
    section = _mapping(raw, "extensions", _EXTENSION_KEYS)
    node_types: dict[str, ExtNodeType] = {}
    for name, value in _mapping(section.get("nodeTypes"), "extensions.nodeTypes").items():
        where = f"extensions.nodeTypes.{name}"
        entry = _mapping(value, where, _NODE_TYPE_KEYS)
        node_types[name] = ExtNodeType(
            _required_text(entry, "parent", where),
            _required_text(entry, "prefix", where),
            _optional_text(entry, "note", where),
        )
    edge_types: dict[str, ExtEdgeType] = {}
    for name, value in _mapping(section.get("edgeTypes"), "extensions.edgeTypes").items():
        where = f"extensions.edgeTypes.{name}"
        entry = _mapping(value, where, _EDGE_TYPE_KEYS)
        edge_types[name] = ExtEdgeType(
            parent=_required_text(entry, "parent", where),
            inverse=_required_text(entry, "inverse", where),
            domain=_optional_list(entry, "domain", where),
            range=_optional_list(entry, "range", where),
            note=_optional_text(entry, "note", where),
            acyclic=_optional_bool(entry, "acyclic", where),
            symmetric=_optional_bool(entry, "symmetric", where),
        )
    attributes: dict[str, ExtAttribute] = {}
    for name, value in _mapping(section.get("attributes"), "extensions.attributes").items():
        where = f"extensions.attributes.{name}"
        entry = _mapping(value, where, _ATTRIBUTE_KEYS)
        attributes[name] = ExtAttribute(
            on=_text_list(entry.get("on"), f"{where}.on"),
            type=_required_text(entry, "type", where),
            values=_text_list(entry.get("values"), f"{where}.values"),
        )
    enums = {
        name: _text_list(value, f"extensions.enums.{name}")
        for name, value in _mapping(section.get("enums"), "extensions.enums").items()
    }
    return Extensions(node_types, edge_types, attributes, enums)


def _invalid(where: str, reason: str) -> RulesetError:
    return RulesetError("ruleset-invalid", f"{RULESET_FILE}: {where} {reason}")


def _mapping(
    value: object, where: str, allowed: frozenset[str] | None = None
) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _invalid(where, "must be a mapping with text keys")
    if allowed is not None and (unknown := sorted(set(value) - allowed)):
        raise _invalid(where, f"has unknown keys: {', '.join(unknown)}")
    return value


def _list(value: object, where: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(where, "must be a list")
    return value


def _text_list(value: object, where: str) -> tuple[str, ...]:
    items = _list(value, where)
    if not all(isinstance(item, str) for item in items):
        raise _invalid(where, "must hold text only; quote values that YAML would read as numbers")
    return tuple(str(item) for item in items)


def _required_text(entry: Mapping[str, object], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise _invalid(f"{where}.{key}", "must be non-empty text")
    return value


def _optional_text(entry: Mapping[str, object], key: str, where: str) -> str:
    value = entry.get(key, "")
    if not isinstance(value, str):
        raise _invalid(f"{where}.{key}", "must be text")
    return value


def _optional_bool(entry: Mapping[str, object], key: str, where: str) -> bool:
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise _invalid(f"{where}.{key}", "must be true or false")
    return value


def _optional_list(entry: Mapping[str, object], key: str, where: str) -> tuple[str, ...] | None:
    if key not in entry:
        return None
    return _text_list(entry[key], f"{where}.{key}")


class _IndentedDumper(yaml.SafeDumper):
    """A safe dumper that indents list items under their key, as the shipped files do."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def register_source(root: Path, key: str, note_id: str, supersedes: str | None = None) -> None:
    """Append a source to `_ruleset.yaml`, keeping its other content and key order.

    PyYAML drops comments and normalises quoting and list layout; the result is stable, so a
    second rewrite of its own output changes nothing. Raises `RulesetError` when `key` is
    already registered or the file is not a ruleset.
    """
    path = root / RULESET_FILE
    ruleset = load_ruleset(root)
    if any(source.key == key for source in ruleset.sources):
        raise RulesetError("source-key-registered", f"the source key {key!r} is already registered")
    document = load_yaml(path.read_bytes().decode("utf-8-sig"))
    assert isinstance(document, dict)  # load_ruleset has just parsed it as a mapping
    sources = document.get("sources")
    if not isinstance(sources, list):
        sources = []
        document["sources"] = sources
    entry: dict[str, str] = {"key": key, "note": note_id}
    if supersedes is not None:
        entry["supersedes"] = supersedes
    sources.append(entry)
    text = yaml.dump(
        document,
        Dumper=_IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    path.write_bytes(text.encode("utf-8"))
