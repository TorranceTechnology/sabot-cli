"""LNT-P and LNT-2: every lint class has a planted defect that trips it, and only it."""

from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from sabot_cli.vault import load_vault
from support.filesystem import requires_device_file_names
from support.plant import LINT_DEFECTS, PlantedDefect, plant

# Warnings that `--strict` raises to errors; the other two warnings stay warnings.
BUILD_READINESS_WARNINGS = frozenset(
    {"orphan", "pending", "question-open", "source-superseded", "stale"}
)
PLAIN_WARNINGS = frozenset({"body-link", "overrides-and-exception"})
SUPERSEDED_SOURCE = "dw1"


def _lint(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("lint", "--vault", str(vault), "--json", *options)


def _findings(result: CliResult) -> list[dict[str, str]]:
    findings = result.json()["findings"]
    assert isinstance(findings, list)
    return findings


def _citers_of(vault: Path, source_key: str) -> set[str]:
    """Ids of the notes citing a source, read the way a person would: from `source:` entries."""
    notes = load_vault(vault, frozenset()).notes
    return {
        note.id
        for note in notes.values()
        if any(
            isinstance(item, str) and item.startswith(f"{source_key}#")
            for item in (note.properties.get("source") or [])  # type: ignore[attr-defined]
        )
    }


def _assert_reported_where_planted(
    defect: str, planted: PlantedDefect, findings: list[dict[str, str]], vault: Path
) -> None:
    notes = {finding["note"] for finding in findings}
    paths = {finding["path"] for finding in findings}
    if planted.expected_notes:
        assert notes == set(planted.expected_notes), f"{defect} was reported on {sorted(notes)}"
    elif defect == "frontmatter-invalid":
        # An unreadable note has no id to name; it is reported by its file.
        assert (notes, paths) == ({None}, {planted.path.as_posix()})  # type: ignore[comparison-overlap]
    elif defect in ("ruleset-invalid", "transaction-incomplete"):
        assert (notes, paths) == ({None}, {planted.path.as_posix()})  # type: ignore[comparison-overlap]
    else:
        assert defect == "source-superseded"
        # Every citer of the superseded edition is reported, and nothing else is.
        assert notes == _citers_of(vault, SUPERSEDED_SOURCE)


@pytest.mark.parametrize(
    "defect",
    [
        pytest.param(defect, marks=requires_device_file_names)
        if defect == "id-reserved-name"
        else defect
        for defect in LINT_DEFECTS
    ],
)
def test_lint_refuses_each_planted_class(
    defect: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the clean vault with one defect of this class planted
    planted = plant(driftwood_vault, defect)

    # When it is linted
    result = _lint(run_cli, driftwood_vault)

    # Then exactly that class is reported, where it was planted, with its own severity and exit
    findings = _findings(result)
    assert {finding["class"] for finding in findings} == {defect}
    _assert_reported_where_planted(defect, planted, findings, driftwood_vault)
    is_warning = defect in BUILD_READINESS_WARNINGS | PLAIN_WARNINGS
    assert {finding["severity"] for finding in findings} == {"warning" if is_warning else "error"}
    assert result.returncode == (0 if is_warning else 1)


@pytest.mark.parametrize("defect", sorted(BUILD_READINESS_WARNINGS | PLAIN_WARNINGS))
def test_strict_fails_only_the_build_readiness_warnings(
    defect: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the clean vault with one warning-class defect planted
    plant(driftwood_vault, defect)

    # When it is linted with --strict
    result = _lint(run_cli, driftwood_vault, "--strict")

    # Then the build-readiness classes fail the run and the other warnings do not
    raised = defect in BUILD_READINESS_WARNINGS
    assert result.returncode == (1 if raised else 0)
    assert {finding["severity"] for finding in _findings(result)} == {
        "error" if raised else "warning"
    }


def test_every_lint_class_has_a_probe(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given the classes `lint --list-classes` reports
    result = run_cli("lint", "--list-classes", "--vault", str(tmp_path), "--json")

    # When they are compared with the classes the probes plant
    listed = {entry["class"] for entry in result.json()["classes"]}  # type: ignore[attr-defined, union-attr]

    # Then the two sets are equal
    assert listed == set(LINT_DEFECTS)
