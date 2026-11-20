"""Compile a vault into the machine graph a downstream generator reads."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from sabot_cli.canonical import FORMAT_VERSION, JsonValue, canonical_json
from sabot_cli.rules.ruleset import RULESET_FILE, Ruleset
from sabot_cli.rules.schema import Schema, merge_extensions
from sabot_cli.sources import SPANS_DIRECTORY
from sabot_cli.vault import Vault

_SPAN_FILE_PATTERN = "*.jsonl"


@dataclass(frozen=True)
class GraphNode:
    """One note as the graph records it."""

    id: str
    type: str
    path: str
    paraphrase: str
    attributes: Mapping[str, JsonValue]


@dataclass(frozen=True)
class GraphEdge:
    """One edge; `derived` marks an inverse that compile computed rather than read."""

    source: str
    edge: str
    target: str
    derived: bool


@dataclass(frozen=True)
class Graph:
    """The compiled graph: nodes by id, edges sorted, and the digest of the vault it came from."""

    ruleset: str
    schema_version: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    extensions: Mapping[str, JsonValue]
    extension_count: int
    vault_digest: str

    def to_json(self) -> str:
        """The graph as canonical JSON text."""
        return canonical_json(
            {
                "edges": [
                    {"derived": e.derived, "edge": e.edge, "from": e.source, "to": e.target}
                    for e in self.edges
                ],
                "extensions": self.extensions,
                "format": FORMAT_VERSION,
                "nodes": [
                    {
                        "attributes": n.attributes,
                        "id": n.id,
                        "paraphrase": n.paraphrase,
                        "path": n.path,
                        "type": n.type,
                    }
                    for n in self.nodes
                ],
                "ruleset": self.ruleset,
                "schemaVersion": self.schema_version,
                "vaultDigest": self.vault_digest,
            }
        )

    def notes_by_type(self) -> dict[str, int]:
        """How many notes of each type the graph holds."""
        return dict(sorted(Counter(node.type for node in self.nodes).items()))

    def edges_by_type(self, *, derived: bool) -> dict[str, int]:
        """How many authored (or derived) edges of each type the graph holds."""
        counts = Counter(edge.edge for edge in self.edges if edge.derived is derived)
        return dict(sorted(counts.items()))


@dataclass(frozen=True)
class BuildProblem:
    """An error finding that stops `compile_graph` from running, as `compile` reports it."""

    code: str
    note: str | None
    path: str
    message: str


def compile_graph(vault: Vault, schema: Schema, ruleset: Ruleset) -> Graph:
    """Compile a lint-clean vault into a graph.

    `schema` is the fixed schema; the ruleset's extensions are merged here. Raises `ValueError`
    when an edge targets a note that does not exist, which lint refuses before this runs.
    """
    merged = merge_extensions(schema, ruleset)
    authored: set[tuple[str, str, str]] = set()
    nodes: list[GraphNode] = []
    non_attributes = {"id", "type"} | merged.link_properties
    for note_id in sorted(vault.notes):
        note = vault.notes[note_id]
        for link in note.links:
            # An authored inverse is an error elsewhere; the graph derives inverses itself.
            if link.target is None or link.property not in merged.edge_types:
                continue
            if link.target not in vault.notes:
                raise ValueError(
                    f"{note_id} {link.property} points at the missing note {link.target}"
                )
            authored.add((note_id, link.property, link.target))
        nodes.append(
            GraphNode(
                id=note_id,
                type=str(note.properties.get("type", "")),
                path=note.path.as_posix(),
                paraphrase=_paraphrase(note.body),
                attributes={
                    key: _json_safe(value)
                    for key, value in note.properties.items()
                    if key not in non_attributes
                },
            )
        )
    edges = [GraphEdge(*edge, derived=False) for edge in authored]
    edges.extend(
        GraphEdge(target, merged.edge_types[edge].inverse, source, derived=True)
        for source, edge, target in authored
    )
    return Graph(
        ruleset=ruleset.ruleset,
        schema_version=ruleset.schema_version,
        nodes=tuple(nodes),
        edges=tuple(sorted(set(edges), key=lambda e: (e.source, e.edge, e.target, e.derived))),
        extensions=ruleset.extensions.to_json(),
        extension_count=ruleset.extensions.count(),
        vault_digest=_digest(vault),
    )


def _paraphrase(body: str) -> str:
    """The body text between the first `# ` heading and the first `## ` heading, trimmed."""
    lines = body.split("\n")
    start = next((index + 1 for index, line in enumerate(lines) if line.startswith("# ")), 0)
    kept: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") or line == "##":
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _digest(vault: Vault) -> str:
    """SHA-256 over the sorted paths and bytes of the notes, the ruleset file and the span files.

    Each file contributes its relative path and the SHA-256 of its bytes. The span files are the
    `*.jsonl` files directly under `spans/`; a hidden file there, such as one a file manager
    leaves behind, or any other file does not change the digest.
    """
    files = {note.path for note in vault.notes.values()}
    files.add(PurePosixPath(RULESET_FILE))
    spans = vault.root / SPANS_DIRECTORY
    if spans.is_dir():
        files.update(
            PurePosixPath(path.relative_to(vault.root).as_posix())
            for path in spans.glob(_SPAN_FILE_PATTERN)
            if path.is_file() and not path.name.startswith(".")
        )
    digest = hashlib.sha256()
    for relative in sorted(files, key=lambda path: path.as_posix()):
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256((vault.root / relative).read_bytes()).digest())
    return digest.hexdigest()


def _json_safe(value: object) -> JsonValue:
    """Front matter values are plain YAML data; anything else is recorded as its JSON text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return json.dumps(value, default=str)
