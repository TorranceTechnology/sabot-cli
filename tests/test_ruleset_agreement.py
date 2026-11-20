"""Lint checks that each registered source and the source note it names agree."""

from pathlib import Path

import pytest

from conftest import CliRunner
from support.plant import register_second_edition


def _ruleset_findings(run_cli: CliRunner, vault: Path) -> list[str]:
    result = run_cli("lint", "--vault", str(vault), "--json")
    findings = result.json()["findings"]
    assert isinstance(findings, list)
    ruleset = [f for f in findings if f["class"] == "ruleset-invalid"]  # type: ignore[index, call-overload]
    assert result.returncode == (1 if ruleset else 0)
    return [str(f["message"]) for f in ruleset]  # type: ignore[index, call-overload]


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {path.name}"
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def test_a_registration_naming_another_editions_note_is_refused(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the ruleset's dw1 entry pointing at the note of the unregistered dw2
    _edit(driftwood_vault / "_ruleset.yaml", "note: src.dw1", "note: src.dw2")

    # When the vault is linted
    messages = _ruleset_findings(run_cli, driftwood_vault)

    # Then the key and the note's own key are named
    assert messages == ["source 'dw1' names the note 'src.dw2', whose key is 'dw2'"]


def test_a_registration_naming_a_note_that_is_not_a_source_note_is_refused(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the ruleset's dw1 entry pointing at a clause
    _edit(driftwood_vault / "_ruleset.yaml", "note: src.dw1", "note: cl.setup")

    # When the vault is linted
    messages = _ruleset_findings(run_cli, driftwood_vault)

    # Then the registration is refused as naming something that is not a source note
    assert messages == ["source 'dw1' names the note 'cl.setup', which is not a source note"]


@pytest.mark.parametrize(
    ("file", "old", "new", "expected"),
    [
        (
            "sources/src.dw2.md",
            "supersedes: dw1\n",
            "supersedes: dw9\n",
            "source 'dw2' names the note 'src.dw2', which supersedes 'dw9' while the ruleset "
            "says 'dw1'",
        ),
        (
            "sources/src.dw2.md",
            "supersedes: dw1\n",
            "",
            "source 'dw2' names the note 'src.dw2', which supersedes None while the ruleset "
            "says 'dw1'",
        ),
        (
            "_ruleset.yaml",
            "    supersedes: dw1\n",
            "",
            "source 'dw2' names the note 'src.dw2', which supersedes 'dw1' while the ruleset "
            "says None",
        ),
    ],
    ids=["different-key", "note-says-nothing", "ruleset-says-nothing"],
)
def test_a_registration_and_its_note_must_agree_on_what_is_superseded(
    run_cli: CliRunner, driftwood_vault: Path, file: str, old: str, new: str, expected: str
) -> None:
    # Given edition two registered as the successor of edition one, then one side edited
    register_second_edition(driftwood_vault)
    _edit(driftwood_vault / file, old, new)

    # When the vault is linted
    messages = _ruleset_findings(run_cli, driftwood_vault)

    # Then the disagreement is reported once, naming both values
    assert messages == [expected]


def test_a_registration_that_agrees_with_its_note_is_not_reported(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition two registered as the successor of edition one, on both sides
    register_second_edition(driftwood_vault)

    # When the vault is linted
    # Then the ruleset has no finding
    assert _ruleset_findings(run_cli, driftwood_vault) == []
