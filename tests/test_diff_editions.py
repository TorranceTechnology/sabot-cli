"""DIF-1 to DIF-4: `diff-editions` classifies span changes from the span files alone."""

from pathlib import Path

import pytest

from conftest import FIXTURES, CliResult, CliRunner
from source_vaults import error_code, segmented_vault, snapshot, write_text

RULES_E1 = FIXTURES / "rules-e1.txt"
RULES_E2 = FIXTURES / "rules-e2.txt"


def _diff(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("diff-editions", "dw1", "dw2", "--vault", str(vault), "--json", *options)


def _classes(result: CliResult) -> dict[str, list[str]]:
    classes = result.json()["classes"]
    assert isinstance(classes, dict)
    return classes


def test_diff_classifies_each_change_kind(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given Driftwood editions 1 and 2, with one span of each planted kind
    before = snapshot(driftwood_vault)

    # When they are diffed
    result = _diff(run_cli, driftwood_vault)

    # Then each planted change lands in its class, the rest is unchanged, and nothing is written
    classes = _classes(result)
    assert result.returncode == 0
    assert classes["editorial"] == ["3.1"]
    assert classes["substantive"] == ["5.1a"]
    assert classes["added"] == ["2.5", "6.7a", "9.4"]
    assert classes["removed"] == ["4.2a", "6.8", "7.3"]
    assert result.json()["moved"] == [{"from": "4.2a", "to": "2.5"}]
    changed = {"3.1", "5.1a", "2.5", "6.7a", "9.4", "4.2a", "6.8", "7.3"}
    assert changed.isdisjoint(classes["unchanged"])
    assert len(classes["unchanged"]) == 46
    assert snapshot(driftwood_vault) == before


def test_editorial_change_needs_no_source_text(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given two editions of one rule that differ only in a pronoun and in punctuation
    first = write_text(tmp_path / "e1.txt", "1.1  She may draw a card, then she must wait.\n")
    second = write_text(tmp_path / "e2.txt", "1.1  They may draw a card; then they must wait.\n")
    vault = segmented_vault(run_cli, tmp_path / "vault", {"dw1": first, "dw2": second})

    # When they are diffed with no text options
    result = _diff(run_cli, vault)

    # Then the rule is editorial rather than substantive
    assert result.returncode == 0
    assert _classes(result)["editorial"] == ["1.1"]
    assert _classes(result)["substantive"] == []


def test_moved_with_edit_is_paired_when_text_is_given(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a rule moved and lightly reworded between the editions (6.8 became 6.7a)
    texts = ("--old-text", str(RULES_E1), "--new-text", str(RULES_E2))

    # When the editions are diffed without and with both texts
    without = _diff(run_cli, driftwood_vault)
    with_texts = _diff(run_cli, driftwood_vault, *texts)

    # Then the removed and added locators are paired only with the texts, with the word count,
    # and a substantive span reports how many words changed
    assert without.json()["paired"] == []
    assert without.json()["changedWords"] == {}
    assert with_texts.returncode == 0
    assert with_texts.json()["paired"] == [{"changedWords": 2, "new": "6.7a", "old": "6.8"}]
    assert with_texts.json()["changedWords"] == {"5.1a": 2}
    # The exact move 4.2a to 2.5 is a move, not a pair
    assert with_texts.json()["moved"] == [{"from": "4.2a", "to": "2.5"}]


def test_diff_never_prints_text_from_either_edition(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given both texts
    texts = ("--old-text", str(RULES_E1), "--new-text", str(RULES_E2))

    # When the diff is written as JSON and as text
    json_out = _diff(run_cli, driftwood_vault, *texts).stdout
    text_out = run_cli(
        "diff-editions", "dw1", "dw2", "--vault", str(driftwood_vault), *texts
    ).stdout

    # Then no sentence of the changed rules appears in either
    for output in (json_out, text_out):
        lowered = output.lower()
        assert "launch at most" not in lowered
        assert "shuffle the" not in lowered
        assert "following flood phase" not in lowered


def test_affected_notes_are_the_citers_of_changed_spans(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault on edition 1
    # When the editions are diffed
    result = _diff(run_cli, driftwood_vault)

    # Then the re-review set is exactly the citers of substantive, removed or moved spans;
    # the editorial citer (cl.setup) and the citer of a subtree that only gained a span
    # (cl.solo-refused) are not in it
    assert result.json()["affectedNotes"] == [
        "act.launch-boat",
        "cl.launch-bonus",
        "ph.ebb",
        "win.first-to-target",
    ]


def test_scope_limits_the_diff_to_locators_under_a_prefix(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a scope of section 5 and the locator 6.8 (a prefix matches at a . or / boundary)
    # When the editions are diffed
    result = _diff(run_cli, driftwood_vault, "--scope", "5", "--scope", "6.8")

    # Then only spans under those prefixes are classified, and the re-review set shrinks too
    classes = _classes(result)
    assert classes["substantive"] == ["5.1a"]
    assert classes["removed"] == ["6.8"]
    assert classes["added"] == []
    assert classes["editorial"] == []
    assert all(locator.startswith("5") for locator in classes["unchanged"])
    assert result.json()["scope"] == ["5", "6.8"]
    assert result.json()["affectedNotes"] == ["act.launch-boat", "cl.launch-bonus"]


def test_scope_prefix_does_not_match_a_longer_number(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the prefix 1, which must not take in 10-numbered locators (the fixture has none) but
    # does take its children
    # When diffed
    classes = _classes(_diff(run_cli, driftwood_vault, "--scope", "1"))

    # Then section 1 and its rules are all that is classified
    assert classes["unchanged"] == ["1", "1.1", "1.2", "1.3", "1.4"]


def test_diff_text_output_lists_each_changed_class(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the fixture editions
    # When the diff is written for people
    result = run_cli("diff-editions", "dw1", "dw2", "--vault", str(driftwood_vault))

    # Then the changed spans and the re-review set are listed and unchanged ones are counted
    assert result.returncode == 0
    assert "unchanged: 46" in result.stdout
    assert "  4.2a -> 2.5" in result.stdout
    assert "  win.first-to-target" in result.stdout


def test_diff_refuses_unusable_input(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given each way the input can be unusable
    vault = str(driftwood_vault)
    texts = {"--old-text": str(RULES_E1), "--new-text": str(RULES_E2)}
    cases: dict[str, tuple[tuple[str, ...], str]] = {
        "span-file-missing": (("dw1", "dw9", "--vault", vault), "span-file-missing"),
        "source-key-invalid": (("dw1", "DW 2", "--vault", vault), "source-key-invalid"),
        "editions-identical": (("dw1", "dw1", "--vault", vault), "editions-identical"),
        "text-options-incomplete": (
            ("dw1", "dw2", "--vault", vault, "--old-text", texts["--old-text"]),
            "text-options-incomplete",
        ),
        "text-mismatch": (
            ("dw1", "dw2", "--vault", vault, "--old-text", texts["--new-text"],
             "--new-text", texts["--old-text"]),
            "text-mismatch",
        ),
        "file-unreadable": (
            ("dw1", "dw2", "--vault", vault, "--old-text", str(tmp_path / "nope.txt"),
             "--new-text", texts["--new-text"]),
            "file-unreadable",
        ),
        "not-a-vault": (("dw1", "dw2", "--vault", str(tmp_path)), "not-a-vault"),
    }  # fmt: skip

    for name, (arguments, code) in cases.items():
        # When the command runs
        result = run_cli("diff-editions", *arguments, "--json")

        # Then it refuses with that error code and exit 3
        assert result.returncode == 3, name
        assert error_code(result) == code, name


@pytest.mark.parametrize("flag", ["--old-text", "--new-text"])
def test_diff_refuses_text_that_is_not_utf8(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path, flag: str
) -> None:
    # Given a text file that is not UTF-8
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00")
    other = "--new-text" if flag == "--old-text" else "--old-text"
    good = RULES_E2 if other == "--new-text" else RULES_E1

    # When it is given as either text
    result = _diff(run_cli, driftwood_vault, flag, str(bad), other, str(good))

    # Then the command refuses
    assert result.returncode == 3
    assert error_code(result) == "text-undecodable"
