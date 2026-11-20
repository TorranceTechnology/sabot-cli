"""The `schema` command."""

import argparse
from collections.abc import Mapping
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import load_rules
from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
)
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import (
    Attribute,
    EdgeType,
    NodeType,
    Requirement,
    RulesetError,
    Schema,
    load_schema,
)

NAME = "schema"


def register(subparsers: Subparsers) -> None:
    """Add the `schema` subparser."""
    parser = subparsers.add_parser(NAME, help="show the schema a vault is written to")
    parser.add_argument("--type", help="show one node type")
    parser.add_argument("--edge", help="show one edge type")
    add_vault_option(parser, optional=True)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    vault_root: Path | None = args.vault
    ruleset_id: str | None = None
    try:
        schema = load_schema()
        if vault_root is not None:
            rules = load_rules(vault_root)
            schema = rules.schema
            ruleset_id = rules.ruleset.ruleset
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    node_name: str | None = args.type
    edge_name: str | None = args.edge
    if node_name is not None and node_name not in schema.node_types:
        return output.refuse(
            NAME, "unknown-type", f"no node type named {node_name!r}", as_json=as_json
        )
    if edge_name is not None and edge_name not in schema.edge_types:
        return output.refuse(
            NAME, "unknown-edge", f"no edge type named {edge_name!r}", as_json=as_json
        )
    selected = node_name is not None or edge_name is not None
    node_types = (
        [schema.node_types[node_name]]
        if node_name
        else ([] if selected else sorted(schema.node_types.values(), key=lambda t: t.name))
    )
    edge_types = (
        [schema.edge_types[edge_name]]
        if edge_name
        else ([] if selected else sorted(schema.edge_types.values(), key=lambda t: t.name))
    )
    attributes = [] if selected else sorted(schema.attributes.values(), key=lambda a: a.name)
    if as_json:
        output.write_json(_document(schema, ruleset_id, node_types, edge_types, attributes))
    else:
        output.write_text(_text(schema, node_types, edge_types, attributes))
    return int(ExitCode.OK)


def _document(
    schema: Schema,
    ruleset_id: str | None,
    node_types: list[NodeType],
    edge_types: list[EdgeType],
    attributes: list[Attribute],
) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "command": NAME,
        "edgeTypes": [_edge_json(edge) for edge in edge_types],
        "format": output.FORMAT_VERSION,
        "nodeTypes": [_node_json(schema, node) for node in node_types],
        "ruleset": ruleset_id,
        "schemaVersion": schema.version,
    }
    if attributes:
        document["attributes"] = [_attribute_json(attribute) for attribute in attributes]
        document["constructKinds"] = list(schema.construct_kinds)
        document["statuses"] = list(schema.statuses)
        document["statusRequirements"] = _requirements_json(schema.status_requirements)
    return document


def _node_json(schema: Schema, node: NodeType) -> dict[str, JsonValue]:
    fixed = schema.resolve_type(node.name)
    requirement = schema.type_requirements.get(fixed or "", Requirement())
    return {
        "extension": node.extension,
        "name": node.name,
        "note": node.note,
        "parent": node.parent,
        "prefix": node.prefix,
        "requires": _requirement_json(requirement),
        "side": node.side,
    }


def _edge_json(edge: EdgeType) -> dict[str, JsonValue]:
    return {
        "acyclic": edge.acyclic,
        "domain": list(edge.domain),
        "extension": edge.extension,
        "inverse": edge.inverse,
        "name": edge.name,
        "note": edge.note,
        "parent": edge.parent,
        "range": list(edge.range),
        "symmetric": edge.symmetric,
    }


def _attribute_json(attribute: Attribute) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "extension": attribute.extension,
        "name": attribute.name,
        "note": attribute.note,
        "on": list(attribute.on),
        "required": attribute.required,
        "type": attribute.type,
    }
    optional: Mapping[str, JsonValue] = {
        "minimum": attribute.minimum,
        "of": attribute.of,
        "pattern": attribute.pattern,
        "values": list(attribute.values) or None,
        "valuesByType": {k: list(v) for k, v in sorted(attribute.values_by_type.items())} or None,
        "valuesFrom": attribute.values_from,
    }
    document.update({key: value for key, value in optional.items() if value is not None})
    return document


def _requirement_json(requirement: Requirement) -> dict[str, JsonValue]:
    return {
        "anyEdge": list(requirement.any_edge),
        "anyInboundEdge": list(requirement.any_inbound_edge),
        "attributes": list(requirement.attributes),
    }


def _requirements_json(requirements: Mapping[str, Requirement]) -> dict[str, JsonValue]:
    return {name: _requirement_json(item) for name, item in sorted(requirements.items())}


def _text(
    schema: Schema,
    node_types: list[NodeType],
    edge_types: list[EdgeType],
    attributes: list[Attribute],
) -> str:
    sections: list[str] = []
    if node_types:
        rows = [
            [
                n.name,
                n.prefix,
                n.side,
                f"extends {n.parent}" if n.parent else "fixed",
                _needs(schema, n),
            ]
            for n in node_types
        ]
        sections.append(
            output.format_table(["node type", "prefix", "side", "origin", "requires"], rows)
        )
    if edge_types:
        rows = [
            [
                e.name,
                ",".join(e.domain),
                ",".join(e.range),
                e.inverse,
                _flags(e),
                f"extends {e.parent}" if e.parent else "fixed",
            ]
            for e in edge_types
        ]
        sections.append(
            output.format_table(
                ["edge type", "domain", "range", "inverse", "flags", "origin"], rows
            )
        )
    if attributes:
        rows = [
            [
                a.name,
                a.type,
                "required" if a.required else "optional",
                ",".join(a.on) or "any",
                "declared" if a.extension else "fixed",
            ]
            for a in attributes
        ]
        sections.append(output.format_table(["attribute", "type", "use", "on", "origin"], rows))
    return "\n".join(sections)


def _needs(schema: Schema, node: NodeType) -> str:
    requirement = schema.type_requirements.get(schema.resolve_type(node.name) or "", Requirement())
    parts = [*requirement.attributes]
    if requirement.any_edge:
        parts.append("edge:" + "|".join(requirement.any_edge))
    if requirement.any_inbound_edge:
        parts.append("inbound:" + "|".join(requirement.any_inbound_edge))
    return ",".join(parts) or "-"


def _flags(edge: EdgeType) -> str:
    return (
        ",".join(
            flag for flag, on in (("acyclic", edge.acyclic), ("symmetric", edge.symmetric)) if on
        )
        or "-"
    )
