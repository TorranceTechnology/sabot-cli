"""QRY-1 to QRY-4: `search` without terms selects notes by front-matter filters."""

from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from search_vaults import NoteSpec, result_ids, result_rows, write_vault


def _search(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("search", *options, "--vault", str(vault), "--json")


def test_filter_by_type_and_status(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the fixture vault, whose structural notes are one clause and two sources
    # When searching for clauses that are structural or refused
    result = _search(
        run_cli,
        driftwood_vault,
        "--type",
        "clause",
        "--status",
        "structural",
        "--status",
        "refused",
    )

    # Then exactly those clauses are returned, sorted by id, with the row columns
    assert result.returncode == 0
    assert result_ids(result) == ["cl.layout", "cl.solo-refused"]
    first = result_rows(result)[0]
    assert set(first) == {"id", "path", "status", "title", "type"}
    assert (first["id"], first["path"], first["type"]) == (
        "cl.layout",
        "clauses/cl.layout.md",
        "clause",
    )


def test_filter_by_type_alone_is_sorted_by_id_without_a_default_limit(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault of 54 notes
    # When every note is selected by a property they all hold
    result = _search(run_cli, driftwood_vault, "--where", "has:id")

    # Then all of them are listed, in id order, with no cut
    ids = result_ids(result)
    assert len(ids) == 54
    assert ids == sorted(ids)


def test_filter_by_link_valued_property(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given clauses that link the Moor phase through `part-of`
    # When filtering by the target id, written bare or as a wikilink
    bare = _search(run_cli, driftwood_vault, "--where", "part-of=ph.moor")
    linked = _search(run_cli, driftwood_vault, "--where", "part-of=[[ph.moor]]")

    # Then the notes linking that phase are returned both ways
    assert result_ids(bare) == ["cl.hand-limit", "cl.hand-limit-draft"]
    assert result_ids(linked) == result_ids(bare)


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(
            ("--where", "tags=cargo"),
            ["act.sail-out", "ch.capacity", "cl.overload", "ct.cargo", "ex.overload-wreck"],
            id="list-contains",
        ),
        pytest.param(
            ("--type", "zone", "--where", "visibility!=public"),
            ["zone.hand", "zone.stock"],
            id="not-equal",
        ),
        pytest.param(
            ("--type", "clause", "--where", "title~TIDE"),
            ["cl.high-tide-float", "cl.tide-stay", "cl.tide-wrap"],
            id="substring-ignores-case",
        ),
        pytest.param(("--where", "has:condition"), ["rel.launch-needs-card"], id="has"),
        pytest.param(
            ("--type", "keyword", "--where", "missing:modality"), ["kw.buoyant"], id="missing"
        ),
        pytest.param(
            ("--where", "ordered=false", "--type", "zone"),
            ["zone.hand", "zone.harbour"],
            id="boolean-compares-as-true-or-false",
        ),
    ],
)
def test_filter_operators_contains_substring_and_presence(
    options: tuple[str, ...], expected: list[str], run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the fixture vault
    # When a filter with one of the operators is used
    result = _search(run_cli, driftwood_vault, *options)

    # Then it selects the expected set
    assert result_ids(result) == expected


def test_not_equal_accepts_a_note_that_lacks_the_property(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given one note with a `kind` property and one without
    vault = write_vault(
        tmp_path / "vault",
        [
            NoteSpec("a", "A", extra={"kind": "x"}),
            NoteSpec("b", "B", extra={"kind": "y"}),
            NoteSpec("c", "C"),
        ],
    )

    # When filtering with !=
    result = _search(run_cli, vault, "--where", "kind!=x")

    # Then the note without the property is kept, because it does not equal the value
    assert result_ids(result) == ["b", "c"]


def test_repeated_filters_of_one_kind_are_alternatives_and_kinds_combine(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given zones and clauses that are both tagged or untagged
    either = _search(run_cli, driftwood_vault, "--where", "tags=cargo", "--where", "tags=hand")
    narrowed = _search(
        run_cli, driftwood_vault, "--where", "tags=cargo", "--where", "tags=hand", "--type", "zone"
    )

    # Then two --where options give the union, and adding --type keeps only that type
    assert {"ct.cargo", "cl.hand-limit"} <= set(result_ids(either))
    assert result_ids(narrowed) == [
        note_id for note_id in result_ids(either) if str(note_id).startswith("zone.")
    ]


def test_search_skips_and_names_an_unparseable_note(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault with one note whose front matter is not YAML
    (driftwood_vault / "clauses" / "cl.broken.md").write_text(
        "---\nid: [unclosed\n---\n\nbody\n", encoding="utf-8"
    )

    # When searching
    result = _search(run_cli, driftwood_vault, "--type", "clause")

    # Then the other clauses are answered and the broken note is named on stderr by relative path
    assert result.returncode == 0
    assert "cl.layout" in result_ids(result)
    assert "clauses/cl.broken.md" in result.stderr
    assert str(driftwood_vault) not in result.stderr


@pytest.mark.parametrize("expression", ["noperator", "=value", "has:", "missing:a=b", "a b=c"])
def test_a_malformed_filter_is_a_usage_error(
    expression: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a filter that is none of the supported forms
    # When searching
    result = _search(run_cli, driftwood_vault, "--where", expression)

    # Then the command exits 2, names the problem on stderr and writes no document
    assert result.returncode == 2
    assert result.stdout == ""
    assert "cannot read the filter" in result.stderr


def test_a_search_with_no_term_and_no_filter_is_a_usage_error(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given neither terms nor filters
    # When searching
    result = _search(run_cli, driftwood_vault)

    # Then the command exits 2 without writing a document
    assert result.returncode == 2
    assert result.stdout == ""


def test_fields_add_properties_to_each_row(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a clause with a list property and an integer property
    # When extra fields are requested
    result = _search(
        run_cli, driftwood_vault, "--where", "launch-cost=1", "--fields", "tags, launch-cost,absent"
    )

    # Then each row carries them, as lists, integers and null
    (row,) = result_rows(result)
    assert row["tags"] == ["launch"]
    assert row["launch-cost"] == 1
    assert row["absent"] is None


def test_a_field_that_repeats_a_column_is_a_usage_error(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a --fields name that is already a column of every row
    # When searching
    result = _search(run_cli, driftwood_vault, "--type", "zone", "--fields", "title")

    # Then the command exits 2
    assert result.returncode == 2


def test_a_directory_that_is_not_a_vault_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a directory with no ruleset file
    # When searching
    result = _search(run_cli, tmp_path, "--type", "clause")

    # Then the command exits 3 with the JSON error document
    assert result.returncode == 3
    assert result.json()["error"] == {
        "code": "not-a-vault",
        "message": "the directory has no _ruleset.yaml",
    }
