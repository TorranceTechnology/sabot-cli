"""LNT-1, LNT-3 to LNT-5: `lint` over the clean vault, the strict switch and extensions."""

from pathlib import Path

from conftest import CliResult, CliRunner
from support.plant import plant

_EXTENSION_NOTE = """---
id: gauge.tide
type: gauge
title: Tide gauge
status: mapped
confidence: high
rev: 1
schema: 1
source:
  - "dw1#6.6"
---

# Tide gauge

A value read off the marker position.
"""
_DEFINING_CLAUSE = """---
id: cl.gauge-definition
type: clause
title: Gauge definition
status: mapped
confidence: high
rev: 1
schema: 1
modality: definition
source:
  - "dw1#6.6"
defines:
  - "[[gauge.tide]]"
---

# Gauge definition

The clause says what the gauge stands for.
"""


def _lint(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("lint", "--vault", str(vault), "--json", *options)


def _findings(result: CliResult) -> list[dict[str, str]]:
    findings = result.json()["findings"]
    assert isinstance(findings, list)
    return findings


def _declare_extension(vault: Path) -> None:
    ruleset = vault / "_ruleset.yaml"
    text = ruleset.read_text(encoding="utf-8")
    anchor = "  nodeTypes:\n"
    assert anchor in text
    declared = anchor + "    gauge:\n      parent: characteristic\n      prefix: gauge.\n"
    ruleset.write_bytes(text.replace(anchor, declared, 1).encode("utf-8"))


def test_clean_fixture_vault_has_no_findings(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the Driftwood vault
    # When it is linted with --strict
    result = _lint(run_cli, driftwood_vault, "--strict")

    # Then nothing is reported
    assert result.returncode == 0
    assert _findings(result) == []
    assert result.json()["counts"] == {"error": 0, "warning": 0}


def test_strict_raises_pending_stale_and_open_questions(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given one pending note, one stale note and one open question without a resolution
    plant(driftwood_vault, "pending")
    plant(driftwood_vault, "stale")
    plant(driftwood_vault, "question-open")

    # When it is linted without and with --strict
    relaxed = _lint(run_cli, driftwood_vault)
    strict = _lint(run_cli, driftwood_vault, "--strict")

    # Then each is a warning that only --strict turns into a failure
    expected = ["pending", "question-open", "stale"]
    assert relaxed.returncode == 0
    assert sorted(f["class"] for f in _findings(relaxed)) == expected
    assert {f["severity"] for f in _findings(relaxed)} == {"warning"}
    assert strict.returncode == 1
    assert sorted(f["class"] for f in _findings(strict)) == expected
    assert {f["severity"] for f in _findings(strict)} == {"error"}


def test_extension_is_checked_as_its_parent(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a node type declared with the parent `characteristic`, a note of it, and a clause
    # that `defines` it, an edge whose range names only the parent
    _declare_extension(driftwood_vault)
    (driftwood_vault / "model" / "characteristics" / "gauge.tide.md").write_bytes(
        _EXTENSION_NOTE.encode("utf-8")
    )
    (driftwood_vault / "clauses" / "cl.gauge-definition.md").write_bytes(
        _DEFINING_CLAUSE.encode("utf-8")
    )

    # When it is linted with --strict
    result = _lint(run_cli, driftwood_vault, "--strict")

    # Then the edge passes as it would for the parent
    assert _findings(result) == []
    assert result.returncode == 0


def test_an_extension_is_refused_where_its_parent_would_be(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the same extension, and a note of it that authors an edge no characteristic may
    _declare_extension(driftwood_vault)
    note = _EXTENSION_NOTE.replace("---\n\n#", 'invokes:\n  - "[[proc.tide-rise]]"\n---\n\n#', 1)
    (driftwood_vault / "model" / "characteristics" / "gauge.tide.md").write_bytes(
        note.encode("utf-8")
    )
    (driftwood_vault / "clauses" / "cl.gauge-definition.md").write_bytes(
        _DEFINING_CLAUSE.encode("utf-8")
    )

    # When it is linted
    result = _lint(run_cli, driftwood_vault)

    # Then the domain is refused exactly as for a characteristic
    assert [(f["class"], f["note"]) for f in _findings(result)] == [("edge-domain", "gauge.tide")]
    assert result.returncode == 1


def test_orphan_note_warns_and_fails_strict(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a note with no authored and no inbound edge
    plant(driftwood_vault, "orphan")

    # When it is linted without and with --strict
    relaxed = _lint(run_cli, driftwood_vault)
    strict = _lint(run_cli, driftwood_vault, "--strict")

    # Then it fails only under --strict
    assert relaxed.returncode == 0
    assert [(f["class"], f["severity"]) for f in _findings(relaxed)] == [("orphan", "warning")]
    assert strict.returncode == 1
    assert [(f["class"], f["severity"]) for f in _findings(strict)] == [("orphan", "error")]


def test_strict_leaves_the_other_warnings_as_warnings(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a body link and an overrides-and-exception pair, which are warnings in every mode
    plant(driftwood_vault, "body-link")
    plant(driftwood_vault, "overrides-and-exception")

    # When it is linted with --strict
    result = _lint(run_cli, driftwood_vault, "--strict")

    # Then both stay warnings and the exit code is 0
    assert result.returncode == 0
    assert {(f["class"], f["severity"]) for f in _findings(result)} == {
        ("body-link", "warning"),
        ("overrides-and-exception", "warning"),
    }


def test_findings_are_one_per_line_sorted_by_file_class_and_message(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given two defects in different files
    plant(driftwood_vault, "link-dangling")
    plant(driftwood_vault, "orphan")

    # When it is linted as text
    result = run_cli("lint", "--vault", str(driftwood_vault))

    # Then each finding is `<severity> <class> <note> <message>`, ordered by file
    lines = result.stdout.splitlines()
    assert [line.split()[:3] for line in lines] == [
        ["error", "link-dangling", "og.high-water"],
        ["warning", "orphan", "term.stray"],
    ]


def test_an_unusable_ruleset_is_a_finding_not_a_refusal(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ruleset file with a key the format does not have
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_bytes(ruleset.read_bytes() + b"surprise: true\n")

    # When it is linted
    result = _lint(run_cli, driftwood_vault)

    # Then the problem is reported as a ruleset-invalid finding with exit 1
    assert result.returncode == 1
    assert [(f["class"], f["path"]) for f in _findings(result)] == [
        ("ruleset-invalid", "_ruleset.yaml")
    ]


def test_an_unsupported_schema_version_is_a_finding(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ruleset that asks for a schema version this tool does not read
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_bytes(ruleset.read_bytes().replace(b"schemaVersion: 1", b"schemaVersion: 7", 1))

    # When it is linted
    result = _lint(run_cli, driftwood_vault)

    # Then it is a ruleset-invalid finding rather than a refusal
    assert result.returncode == 1
    assert [f["class"] for f in _findings(result)] == ["ruleset-invalid"]


def test_extensions_that_cannot_be_merged_do_not_cascade(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given an extension node type whose parent is not a fixed type, used by a note
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_bytes(
        ruleset.read_bytes().replace(b"parent: rule-process", b"parent: nowhere", 1)
    )

    # When it is linted
    result = _lint(run_cli, driftwood_vault)

    # Then the declaration is the one thing reported, not every note of the undeclared type
    assert result.returncode == 1
    assert {f["class"] for f in _findings(result)} == {"ruleset-invalid"}


def test_a_directory_that_is_not_a_vault_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a directory with no ruleset file
    # When lint runs on it
    result = _lint(run_cli, tmp_path)

    # Then it is refused, not reported as a finding
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "not-a-vault"  # type: ignore[index]


def test_list_classes_needs_no_vault(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a directory that is not a vault
    # When the classes are listed
    result = run_cli("lint", "--list-classes", "--vault", str(tmp_path / "absent"), "--json")

    # Then the list is written and the exit code is 0
    assert result.returncode == 0
    classes = {entry["class"]: entry for entry in result.json()["classes"]}  # type: ignore[attr-defined, union-attr]
    assert classes["orphan"]["severity"] == "warning"
    assert classes["orphan"]["strictSeverity"] == "error"
    assert classes["body-link"]["strictSeverity"] == "warning"
    assert classes["link-dangling"]["severity"] == "error"


def test_list_classes_prints_a_table_for_people(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given no vault
    # When the classes are listed as text
    result = run_cli("lint", "--list-classes", "--vault", str(tmp_path))

    # Then each class is on a line with its severity, strict-promoted ones marked
    lines = {line.split()[0]: line for line in result.stdout.splitlines()[1:]}
    assert lines["orphan"].endswith("warning (error under --strict)")
    assert lines["link-dangling"].endswith("error")
