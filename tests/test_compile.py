"""CMP-1 to CMP-4: `compile` writes a deterministic graph and refuses what it cannot compile."""

import json
from pathlib import Path

import pytest

from conftest import CliRunner
from support.filesystem import require_case_insensitive
from support.plant import plant
from support.vault_state import vault_state

GRAPH = Path("_index") / "graph.json"


def _graph(vault: Path) -> dict[str, object]:
    document = json.loads((vault / GRAPH).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_compile_adds_inverse_edges_marked_derived(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault whose cl.hand-limit is part-of ph.moor
    # When it is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then the graph holds the authored edge and its has-part inverse, marked derived
    assert result.returncode == 0
    edges = _graph(driftwood_vault)["edges"]
    assert {"derived": False, "edge": "part-of", "from": "cl.hand-limit", "to": "ph.moor"} in edges  # type: ignore[operator]
    assert {"derived": True, "edge": "has-part", "from": "ph.moor", "to": "cl.hand-limit"} in edges  # type: ignore[operator]


def test_compile_refuses_a_vault_with_lint_errors(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault with a dangling link
    plant(driftwood_vault, "link-dangling")

    # When it is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then it exits 1, names the finding and writes no graph
    assert result.returncode == 1
    findings = result.json()["findings"]
    assert [finding["class"] for finding in findings] == ["link-dangling"]  # type: ignore[index, union-attr]
    assert not (driftwood_vault / GRAPH).exists()


@pytest.mark.parametrize("defect", ["frontmatter-invalid", "id-duplicate", "link-dangling"])
def test_compile_refuses_each_defect_it_cannot_survive(
    defect: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault with one defect planted
    plant(driftwood_vault, defect)

    # When it is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then exactly that class is reported, as an error, and nothing is written
    assert result.returncode == 1
    findings = result.json()["findings"]
    assert [finding["class"] for finding in findings] == [defect]  # type: ignore[index, union-attr]
    assert {finding["severity"] for finding in findings} == {"error"}  # type: ignore[index, union-attr]
    assert not (driftwood_vault / GRAPH).exists()


def test_check_detects_a_stale_index(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a compiled vault
    assert run_cli("compile", "--vault", str(driftwood_vault)).returncode == 0
    assert run_cli("compile", "--check", "--vault", str(driftwood_vault)).returncode == 0

    # When one note is edited
    note = driftwood_vault / "clauses" / "cl.setup.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nAn extra sentence.\n", encoding="utf-8")
    stale = run_cli("compile", "--check", "--vault", str(driftwood_vault), "--json")

    # Then --check reports the difference and writes nothing
    assert stale.returncode == 1
    assert stale.json()["upToDate"] is False
    assert "An extra sentence." not in (driftwood_vault / GRAPH).read_text(encoding="utf-8")

    # And after recompiling it passes
    assert run_cli("compile", "--vault", str(driftwood_vault)).returncode == 0
    assert run_cli("compile", "--check", "--vault", str(driftwood_vault)).returncode == 0


def test_check_fails_when_no_graph_was_written(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a vault that has never been compiled
    # When --check runs
    result = run_cli("compile", "--check", "--vault", str(driftwood_vault), "--json")

    # Then it fails and still writes nothing
    assert result.returncode == 1
    assert not (driftwood_vault / "_index").exists()


def test_summary_counts_extensions_and_types(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a vault that declares a node type and an attribute
    # When it is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then the summary reports both extensions and the types and edges that were read
    summary = result.json()
    assert result.returncode == 0
    assert summary["extensionCount"] == 2
    assert summary["extensionsByKind"] == {
        "attributes": 1,
        "edgeTypes": 0,
        "enums": 0,
        "nodeTypes": 1,
    }
    assert summary["notes"] == 54
    assert summary["notesByType"]["win-condition"] == 1  # type: ignore[index]
    assert summary["derivedEdges"] == summary["authoredEdges"]
    assert summary["authoredEdgesByType"]["part-of"] == 4  # type: ignore[index]
    assert summary["derivedEdgesByType"]["has-part"] == 4  # type: ignore[index]
    assert summary["path"] == "_index/graph.json"
    assert len(str(summary["vaultDigest"])) == 64


def test_compile_twice_gives_identical_bytes(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a vault compiled once
    run_cli("compile", "--vault", str(driftwood_vault))
    first = (driftwood_vault / GRAPH).read_bytes()

    # When it is compiled again
    run_cli("compile", "--vault", str(driftwood_vault))

    # Then the graph and its digest are unchanged, though the index file now exists
    assert (driftwood_vault / GRAPH).read_bytes() == first


def test_compile_out_writes_elsewhere_and_names_it_without_a_path(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given an output path outside the vault
    target = tmp_path / "elsewhere" / "graph-copy.json"

    # When the vault is compiled to it
    result = run_cli("compile", "--vault", str(driftwood_vault), "--out", str(target), "--json")

    # Then the file is written there only, and the report carries just its name
    assert result.returncode == 0
    assert result.json()["path"] == "graph-copy.json"
    assert target.is_file()
    assert not (driftwood_vault / "_index").exists()


def test_compile_keeps_yes_and_dates_as_text_and_booleans_as_booleans(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note edited the way Obsidian might: yes, a bare date and a number-like time,
    # under attributes the ruleset declares as text
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_text(
        ruleset.read_text(encoding="utf-8").replace(
            "  attributes:\n",
            "  attributes:\n    flavour: {type: text}\n    when: {type: text}\n"
            "    clock: {type: text}\n",
            1,
        ),
        encoding="utf-8",
    )
    note = driftwood_vault / "clauses" / "cl.setup.md"
    text = note.read_text(encoding="utf-8")
    note.write_text(
        text.replace("rev: ", "flavour: yes\nwhen: 2026-01-15\nclock: 1:30\nrev: ", 1),
        encoding="utf-8",
    )

    # When the vault is compiled
    assert run_cli("compile", "--vault", str(driftwood_vault)).returncode == 0

    # Then those values stay text, while true and false stay booleans
    nodes = {node["id"]: node["attributes"] for node in _graph(driftwood_vault)["nodes"]}  # type: ignore[attr-defined]
    assert nodes["cl.setup"]["flavour"] == "yes"
    assert nodes["cl.setup"]["when"] == "2026-01-15"
    assert nodes["cl.setup"]["clock"] == "1:30"
    assert nodes["zone.hand"]["ordered"] is False


def test_paraphrase_is_the_text_between_the_title_and_the_first_section(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note with a title, a paraphrase and a Notes section
    # When the vault is compiled
    assert run_cli("compile", "--vault", str(driftwood_vault)).returncode == 0

    # Then the graph holds the paraphrase only
    nodes = {node["id"]: node for node in _graph(driftwood_vault)["nodes"]}  # type: ignore[attr-defined]
    paraphrase = nodes["cl.hand-limit-draft"]["paraphrase"]
    assert paraphrase.startswith("An earlier extraction read the limit as six cards.")
    assert paraphrase.endswith("remains visible.")
    assert "Replaced" not in paraphrase
    assert "#" not in paraphrase


def test_digest_ignores_the_vault_location_and_file_creation_order(
    run_cli: CliRunner, driftwood_vault: Path, driftwood_vault_twin: Path
) -> None:
    # Given two copies of the vault at different paths, built in opposite orders
    # When both are compiled
    run_cli("compile", "--vault", str(driftwood_vault))
    run_cli("compile", "--vault", str(driftwood_vault_twin))

    # Then the graphs are byte-identical
    assert (driftwood_vault / GRAPH).read_bytes() == (driftwood_vault_twin / GRAPH).read_bytes()


def test_digest_ignores_files_in_spans_that_are_not_span_files(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the digest of the clean vault
    before = run_cli("compile", "--vault", str(driftwood_vault), "--json").json()["vaultDigest"]

    # When a file manager's hidden file, a stray text file and a nested file appear in spans/
    spans = driftwood_vault / "spans"
    (spans / ".DS_Store").write_bytes(b"\x00\x01 finder")
    (spans / "notes.txt").write_text("scratch\n", encoding="utf-8")
    (spans / ".hidden.jsonl").write_text("{}\n", encoding="utf-8")
    (spans / "old").mkdir()
    (spans / "old" / "dw0.jsonl").write_text("{}\n", encoding="utf-8")
    after = run_cli("compile", "--vault", str(driftwood_vault), "--json").json()["vaultDigest"]

    # Then the digest is unchanged, so the committed graph still reads as up to date
    assert after == before
    assert run_cli("compile", "--check", "--vault", str(driftwood_vault)).returncode == 0


def test_digest_follows_the_content_of_a_span_file(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the digest of the clean vault
    before = run_cli("compile", "--vault", str(driftwood_vault), "--json").json()["vaultDigest"]

    # When a span file changes, here by a trailing blank line the reader ignores
    span_file = driftwood_vault / "spans" / "dw1.jsonl"
    span_file.write_bytes(span_file.read_bytes() + b"\n")
    after = run_cli("compile", "--vault", str(driftwood_vault), "--json").json()["vaultDigest"]

    # Then the digest changes
    assert after != before


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("parent: rule-process\n", None),
        ("parent: no-such-type\n", "ruleset-invalid"),
    ],
)
def test_compile_refuses_an_extension_whose_parent_is_not_a_fixed_type(
    replacement: str, code: str | None, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ruleset whose extension names a parent (valid, or not a fixed node type)
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_text(
        ruleset.read_text(encoding="utf-8").replace("parent: rule-process\n", replacement),
        encoding="utf-8",
    )

    # When the vault is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then the valid one compiles and the other is refused by name
    if code is None:
        assert result.returncode == 0
    else:
        assert result.returncode == 3
        assert result.json()["error"]["code"] == code  # type: ignore[index]
        assert not (driftwood_vault / "_index").exists()


@pytest.mark.parametrize(
    "ruleset_text",
    [
        "ruleset: [unclosed\n",
        "schemaVersion: 1\nsources: []\n",
        "ruleset: driftwood\nschemaVersion: 1\nsurprise: true\n",
        "ruleset: driftwood\nschemaVersion: 1\nscope:\n  dw1: [1]\n",
        "ruleset: driftwood\nschemaVersion: 1\nsources:\n  - key: dw1\n",
    ],
)
def test_compile_refuses_a_structurally_invalid_ruleset(
    ruleset_text: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ruleset file that is unparseable, incomplete, has an unknown key or a number
    # where text belongs
    (driftwood_vault / "_ruleset.yaml").write_text(ruleset_text, encoding="utf-8")

    # When the vault is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then it is refused as invalid input and nothing is written
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "ruleset-invalid"  # type: ignore[index]
    assert not (driftwood_vault / "_index").exists()


def test_compile_refuses_a_directory_that_is_not_a_vault(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a directory with no _ruleset.yaml
    # When compile runs on it
    result = run_cli("compile", "--vault", str(tmp_path), "--json")

    # Then it is refused with exit 3
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "not-a-vault"  # type: ignore[index]


def test_the_walk_skips_readmes_and_directories_starting_with_underscore_or_dot(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given files that are not notes: a README, an Obsidian folder and an underscore folder
    (driftwood_vault / "README.md").write_text("not a note\n", encoding="utf-8")
    for folder in (".obsidian", "_drafts"):
        (driftwood_vault / folder).mkdir()
        (driftwood_vault / folder / "scratch.md").write_text("no front matter\n", encoding="utf-8")
    (driftwood_vault / "clauses" / "README.md").write_text("also not a note\n", encoding="utf-8")

    # When the vault is compiled
    result = run_cli("compile", "--vault", str(driftwood_vault), "--json")

    # Then none of them is read, so nothing is refused and the note count is unchanged
    assert result.returncode == 0
    assert result.json()["notes"] == 54


@pytest.mark.parametrize(
    "inside",
    [
        "clauses/cl.setup.md",
        "_ruleset.yaml",
        "spans/dw1.jsonl",
        "graph.json",
        "sources/graph.json",
        "_indexed/graph.json",
        ".sabot-lock",
        ".sabot-txn-aaaaaaaaaaaa/a.json",
        "_index/.sabot-new-x",
    ],
)
def test_compile_out_inside_the_vault_is_refused_anywhere_but_below_the_index_directory(
    inside: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a path inside the vault that is not below _index/: a note, the ruleset, a span
    # file, a stray name or a name the write transaction owns
    before = vault_state(driftwood_vault)

    # When the graph is to be written there
    result = run_cli(
        "compile", "--out", str(driftwood_vault / inside), "--vault", str(driftwood_vault),
        "--json",
    )  # fmt: skip

    # Then compile refuses, without a traceback, and the vault is byte-identical
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "out-inside-vault"  # type: ignore[index]
    assert vault_state(driftwood_vault) == before


def test_compile_out_names_a_note_in_another_letter_case_as_inside_the_vault(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a file system that ignores letter case, and a note spelled in capitals
    require_case_insensitive(driftwood_vault.parent)
    before = vault_state(driftwood_vault)

    # When the graph is to be written over it
    result = run_cli(
        "compile", "--out", str(driftwood_vault / "CLAUSES" / "cl.setup.md"),
        "--vault", str(driftwood_vault), "--json",
    )  # fmt: skip

    # Then compile refuses and the note is untouched
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "out-inside-vault"  # type: ignore[index]
    assert vault_state(driftwood_vault) == before


def test_compile_out_below_the_index_directory_is_written_through_the_transaction(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a path below _index/ other than the default
    target = driftwood_vault / "_index" / "nested" / "copy.json"

    # When the graph is written there
    result = run_cli("compile", "--out", str(target), "--vault", str(driftwood_vault), "--json")

    # Then it is written and nothing of the transaction remains
    assert result.returncode == 0
    assert result.json()["path"] == "_index/nested/copy.json"
    assert target.is_file()
    assert not list(driftwood_vault.glob(".sabot-*"))


def test_compile_out_refuses_a_directory(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given --out naming a directory
    # When compile runs
    result = run_cli(
        "compile", "--out", str(driftwood_vault / "clauses"), "--vault", str(driftwood_vault),
        "--json",
    )  # fmt: skip

    # Then it refuses instead of raising
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "out-is-a-directory"  # type: ignore[index]


def test_compile_out_under_an_unwritable_root_is_a_refusal(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given an --out whose parent would have to sit below a file
    blocker = tmp_path / "blocker"
    blocker.write_text("a file\n", encoding="utf-8")

    # When the graph is written there
    result = run_cli(
        "compile", "--out", str(blocker / "graph.json"), "--vault", str(driftwood_vault), "--json"
    )

    # Then compile refuses with write-failed, not a traceback
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "write-failed"  # type: ignore[index]


def test_compile_out_outside_the_vault_leaves_no_staged_file(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given an output directory outside the vault
    out = tmp_path / "out" / "graph.json"

    # When the graph is written there twice
    run_cli("compile", "--out", str(out), "--vault", str(driftwood_vault))
    run_cli("compile", "--out", str(out), "--vault", str(driftwood_vault))

    # Then the directory holds the graph alone
    assert [path.name for path in out.parent.iterdir()] == ["graph.json"]
