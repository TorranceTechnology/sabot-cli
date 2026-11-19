"""SCH-1 and SCH-2: `schema` shows the fixed v1 schema and a vault's extensions."""

from pathlib import Path

from conftest import CliRunner

NODE_TYPES = {
    "source", "clause", "term", "variant", "parameter", "ruling", "example", "open-question",
    "zone", "phase", "procedure", "action", "decision-point", "keyword-action", "card-type",
    "characteristic", "keyword", "rule-process", "timing-window", "trigger-pattern",
    "replacement-pattern", "ongoing-pattern", "relation", "construct", "test-obligation",
}  # fmt: skip
EDGE_TYPES = {
    "defines", "refines", "restates", "overrides", "exception-to", "precedes", "returns-to",
    "part-of", "invokes", "occurs-in", "requires", "triggers", "defers", "replaces",
    "decided-by", "parameterised-by", "applies-in", "references", "conflicts-with",
    "resolved-by", "illustrates", "implemented-by", "tested-by",
}  # fmt: skip


def _by_name(items: object) -> dict[str, dict[str, object]]:
    assert isinstance(items, list)
    return {item["name"]: item for item in items}


def test_schema_lists_fixed_types_and_edges(run_cli: CliRunner) -> None:
    # Given no vault
    # When schema --json runs
    result = run_cli("schema", "--json")

    # Then every fixed node type and edge type is present, and none is an extension
    assert result.returncode == 0
    document = result.json()
    node_types = _by_name(document["nodeTypes"])
    edge_types = _by_name(document["edgeTypes"])
    assert set(node_types) == NODE_TYPES
    assert set(edge_types) == EDGE_TYPES
    assert not any(item["extension"] for item in [*node_types.values(), *edge_types.values()])
    assert document["ruleset"] is None
    assert document["schemaVersion"] == 1
    # And the corrections to the earlier draft hold
    assert edge_types["tested-by"]["inverse"] == "verifies"
    assert "ruling" in edge_types["refines"]["domain"]  # type: ignore[operator]
    assert "cites" not in edge_types
    assert node_types["clause"]["prefix"] == "cl."
    assert node_types["zone"]["requires"]["attributes"] == [  # type: ignore[index]
        "ownership",
        "visibility",
        "ordered",
    ]
    assert edge_types["precedes"]["acyclic"] is True
    assert edge_types["conflicts-with"]["symmetric"] is True


def test_schema_marks_vault_extensions_with_parents(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault that declares a node type and an attribute
    # When schema --json runs against it
    result = run_cli("schema", "--vault", str(driftwood_vault), "--json")

    # Then the extension appears with its parent, and the fixed part is still all there
    assert result.returncode == 0
    document = result.json()
    node_types = _by_name(document["nodeTypes"])
    assert set(node_types) == NODE_TYPES | {"win-condition"}
    win = node_types["win-condition"]
    assert win["extension"] is True
    assert win["parent"] == "rule-process"
    assert win["prefix"] == "win."
    assert win["side"] == node_types["rule-process"]["side"]
    assert node_types["clause"]["extension"] is False
    attributes = _by_name(document["attributes"])
    assert attributes["launch-cost"]["extension"] is True
    assert attributes["launch-cost"]["on"] == ["action"]
    assert attributes["modality"]["extension"] is False
    assert document["ruleset"] == "driftwood"


def test_schema_selects_one_type_or_one_edge(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a vault with an extension type
    # When one type, and then one edge, is asked for
    one_type = run_cli(
        "schema", "--vault", str(driftwood_vault), "--type", "win-condition", "--json"
    )
    one_edge = run_cli("schema", "--edge", "part-of", "--json")

    # Then only that entry is shown, and an extension type shows what its parent requires
    assert one_type.returncode == 0
    assert [t["name"] for t in one_type.json()["nodeTypes"]] == ["win-condition"]  # type: ignore[union-attr, index]
    assert one_type.json()["edgeTypes"] == []
    assert one_edge.returncode == 0
    assert [e["name"] for e in one_edge.json()["edgeTypes"]] == ["part-of"]  # type: ignore[union-attr, index]
    assert one_edge.json()["nodeTypes"] == []


def test_schema_refuses_an_unknown_name_or_a_directory_that_is_not_a_vault(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a name the schema does not have, and a directory that is not a vault
    # When schema runs
    unknown_type = run_cli("schema", "--type", "no-such-type", "--json")
    unknown_edge = run_cli("schema", "--edge", "no-such-edge", "--json")
    not_a_vault = run_cli("schema", "--vault", str(tmp_path), "--json")

    # Then each is refused by name with exit 3
    assert [r.returncode for r in (unknown_type, unknown_edge, not_a_vault)] == [3, 3, 3]
    assert unknown_type.json()["error"]["code"] == "unknown-type"  # type: ignore[index]
    assert unknown_edge.json()["error"]["code"] == "unknown-edge"  # type: ignore[index]
    assert not_a_vault.json()["error"]["code"] == "not-a-vault"  # type: ignore[index]


def test_schema_text_output_is_a_table_of_every_type(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault with an extension
    # When schema runs without --json
    result = run_cli("schema", "--vault", str(driftwood_vault))

    # Then plain text lists the fixed and the declared names, the extension marked
    assert result.returncode == 0
    assert "win-condition" in result.stdout
    assert "extends rule-process" in result.stdout
    assert "launch-cost" in result.stdout
    assert "part-of" in result.stdout
    assert not result.stdout.lstrip().startswith("{")
