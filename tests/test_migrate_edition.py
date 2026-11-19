"""MIG-1 to MIG-4: `migrate-edition` moves citations to the successor edition."""

import difflib
import re
from pathlib import Path

from conftest import CliResult, CliRunner
from sabot_cli.vault import load_vault
from source_vaults import error_code, snapshot, write_text
from support.plant import register_second_edition

# Notes of the fixture vault that cite the spans the second edition changes.
EDITORIAL = "cl.setup"  # dw1#3.1 became editorial
SUBSTANTIVE = "act.launch-boat"  # dw1#5.1a
REMOVED = ("cl.launch-bonus", "win.first-to-target")  # dw1#6.8, dw1#7.3
MOVED = "ph.ebb"  # dw1#4.2a is dw2#2.5


def _migrate(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("migrate-edition", "dw1", "dw2", "--vault", str(vault), "--json", *options)


def _properties(vault: Path, note_id: str) -> dict[str, object]:
    return dict(load_vault(vault, frozenset()).notes[note_id].properties)


def _cited(vault: Path, note_id: str) -> list[str]:
    cited = _properties(vault, note_id)["source"]
    assert isinstance(cited, list)
    return [str(item) for item in cited]


def _outcomes(result: CliResult) -> dict[str, list[str]]:
    outcomes = result.json()["outcomes"]
    assert isinstance(outcomes, dict)
    return outcomes


def test_unchanged_and_moved_citations_are_repointed(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault on edition 1
    # When it is migrated to edition 2
    result = _migrate(run_cli, driftwood_vault)

    # Then notes citing only unchanged or moved spans cite edition 2 and are not stale
    assert result.returncode == 0
    repointed = _outcomes(result)["repointed"]
    stale = {EDITORIAL, SUBSTANTIVE, *REMOVED}
    assert stale.isdisjoint(repointed)
    for note_id in repointed:
        assert all(item.startswith("dw2#") for item in _cited(driftwood_vault, note_id))
        assert "stale" not in _properties(driftwood_vault, note_id)
    # A sentence and a subtree citation keep their suffix; a moved span takes its new locator
    assert _cited(driftwood_vault, "cl.turn-phases") == ["dw2#4.1/s1", "dw2#4.1/s2"]
    assert "dw2#9/*" in _cited(driftwood_vault, "cl.solo-refused")
    assert _cited(driftwood_vault, MOVED) == ["dw2#4.2", "dw2#2.5"]
    # The spans edition 2 added are listed for extraction; the moved one is not among them
    assert result.json()["added"] == ["6.7a", "9.4"]


def test_changed_citations_are_marked_stale_with_a_reason(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given notes citing the editorial, substantive and removed spans
    # When the vault is migrated
    result = _migrate(run_cli, driftwood_vault)

    # Then each is stale with the matching reason, and the substantive and removed ones still
    # cite edition 1 for every citation, including a span that did not change
    assert _outcomes(result)["stale-editorial"] == [EDITORIAL]
    assert _outcomes(result)["stale-substantive"] == [SUBSTANTIVE]
    assert _outcomes(result)["stale-removed"] == sorted(REMOVED)
    for note_id, reason in [
        (EDITORIAL, "editorial"),
        (SUBSTANTIVE, "substantive"),
        *((note, "removed") for note in REMOVED),
    ]:
        properties = _properties(driftwood_vault, note_id)
        assert properties["stale"] is True
        assert properties["stale-reason"] == reason
    assert all(c.startswith("dw2#") for c in _cited(driftwood_vault, EDITORIAL))
    for note_id in (SUBSTANTIVE, *REMOVED):
        assert all(c.startswith("dw1#") for c in _cited(driftwood_vault, note_id))
    # win.first-to-target also cites dw1#7.2, which did not change, and keeps it on edition 1
    assert _cited(driftwood_vault, "win.first-to-target") == ["dw1#7.2", "dw1#7.3"]


def test_a_note_takes_the_most_severe_outcome_of_the_spans_it_cites(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note citing an editorial, a substantive and a removed span
    note = driftwood_vault / "clauses" / "cl.setup.md"
    text = note.read_text(encoding="utf-8")
    write_text(
        note, text.replace('  - "dw1#3.4"\n', '  - "dw1#3.4"\n  - "dw1#5.1a"\n  - "dw1#7.3"\n')
    )

    # When it is migrated
    result = _migrate(run_cli, driftwood_vault)

    # Then removed outranks substantive and editorial, and no citation is re-pointed
    assert "cl.setup" in _outcomes(result)["stale-removed"]
    assert _properties(driftwood_vault, "cl.setup")["stale-reason"] == "removed"
    assert all(c.startswith("dw1#") for c in _cited(driftwood_vault, "cl.setup"))


def test_migration_edits_front_matter_lines_and_nothing_else(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault's files before migration
    before = {path: data.decode("utf-8") for path, data in snapshot(driftwood_vault).items()}

    # When it is migrated
    _migrate(run_cli, driftwood_vault)

    # Then every changed line is a citation or a stale line, and bodies, status and rev
    # are byte-identical
    citation = re.compile(r'^  - "dw[12]#[^"]+"$')
    for path, text in snapshot(driftwood_vault).items():
        after = text.decode("utf-8")
        if after == before[path]:
            continue
        for line in difflib.unified_diff(
            before[path].split("\n"), after.split("\n"), lineterm="", n=0
        ):
            if line.startswith(("---", "+++", "@@")):
                continue
            assert citation.match(line[1:]) or line[1:].startswith("stale"), (path, line)
        assert before[path].split("\n---\n", 1)[1] == after.split("\n---\n", 1)[1]


def test_flow_style_crlf_note_is_edited_in_place(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a note whose citations are a flow list with a comment, written with CRLF endings
    note = driftwood_vault / "clauses" / "cl.setup.md"
    text = note.read_text(encoding="utf-8")
    flow = """source: ["dw1#3.1", "dw1#3.2"] # keep this comment\n"""
    block = re.search(r"source:\n(?:  - .*\n)+", text)
    assert block is not None
    original = text.replace(block.group(), flow).replace("\n", "\r\n")
    write_text(note, original)

    # When it is migrated
    _migrate(run_cli, driftwood_vault)

    # Then only the references change on that line, the comment stays, and the stale lines
    # use the note's own line ending
    expected = original.replace("dw1#", "dw2#").replace(
        "\r\n---\r\n\r\n#", "\r\nstale: true\r\nstale-reason: editorial\r\n---\r\n\r\n#", 1
    )
    assert note.read_bytes().decode("utf-8") == expected


def test_a_stale_reason_never_softens(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a note already stale for a more severe reason than the migration finds
    note = driftwood_vault / "clauses" / "cl.setup.md"
    text = note.read_text(encoding="utf-8")
    write_text(
        note, text.replace("\n---\n\n#", "\nstale: true\nstale-reason: removed\n---\n\n#", 1)
    )
    before = note.read_bytes()

    # When it is migrated (its editorial citation is re-pointed)
    _migrate(run_cli, driftwood_vault)

    # Then the reason stays removed
    assert _properties(driftwood_vault, "cl.setup")["stale-reason"] == "removed"
    assert note.read_bytes().replace(b"dw2#", b"dw1#") == before


def test_migration_is_idempotent_and_dry_run_writes_nothing(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault on edition 1
    before = snapshot(driftwood_vault)

    # When it is migrated with --dry-run
    dry = _migrate(run_cli, driftwood_vault, "--dry-run")

    # Then no file changes, and the report says what a real run would change
    assert dry.returncode == 0
    assert snapshot(driftwood_vault) == before
    assert dry.json()["dryRun"] is True
    assert dry.json()["written"] is False
    would_change = dry.json()["changed"]

    # When it is migrated for real
    first = _migrate(run_cli, driftwood_vault)
    migrated = snapshot(driftwood_vault)

    # Then it changes exactly those files, and the dry run's outcomes were the real ones
    assert first.json()["changed"] == would_change
    assert _outcomes(first) == _outcomes(dry)
    assert {p for p in migrated if migrated[p] != before[p]} == set(would_change)

    # When it is run again
    second = _migrate(run_cli, driftwood_vault)

    # Then nothing changes: notes already on edition 2 are not cited, and held notes keep
    # their citations and their stale marks
    assert snapshot(driftwood_vault) == migrated
    assert second.json()["changed"] == []
    assert second.json()["written"] is False
    assert _outcomes(second)["repointed"] == []
    assert _outcomes(second)["stale-substantive"] == [SUBSTANTIVE]


def test_strict_lint_refuses_until_every_note_is_on_the_new_edition(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the migrated vault, with edition 2 registered as superseding edition 1
    register_second_edition(driftwood_vault)
    _migrate(run_cli, driftwood_vault)

    # When it is linted with --strict
    result = run_cli("lint", "--strict", "--vault", str(driftwood_vault), "--json")

    # Then it fails on the stale notes and on the notes still citing the superseded edition
    findings = result.json()["findings"]
    assert isinstance(findings, list)
    assert result.returncode == 1
    assert {f["class"] for f in findings} == {"stale", "source-superseded"}
    assert {f["note"] for f in findings if f["class"] == "stale"} == {
        EDITORIAL,
        SUBSTANTIVE,
        *REMOVED,
    }
    assert {f["note"] for f in findings if f["class"] == "source-superseded"} == {
        SUBSTANTIVE,
        *REMOVED,
    }

    # When the stale notes are fixed by hand: re-pointed to edition 2 and the marks cleared
    fixes = {
        "cl.setup": {},
        "act.launch-boat": {},
        "cl.launch-bonus": {"dw2#6.8": "dw2#6.7a"},
        "win.first-to-target": {'  - "dw2#7.3"\n': ""},
    }
    for note_id, replacements in fixes.items():
        file = next(driftwood_vault.rglob(f"{note_id}.md"))
        text = file.read_text(encoding="utf-8").replace("dw1#", "dw2#")
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = re.sub(r"stale: true\nstale-reason: \w+\n", "", text)
        write_text(file, text)
    fixed = run_cli("lint", "--strict", "--vault", str(driftwood_vault), "--json")

    # Then the strict lint passes
    assert fixed.json()["findings"] == []
    assert fixed.returncode == 0


def test_edition_two_needs_no_registration_to_be_migrated_to(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the shipped vault, whose ruleset does not list dw2
    assert "dw2" not in (driftwood_vault / "_ruleset.yaml").read_text(encoding="utf-8")

    # When it is migrated to dw2
    result = _migrate(run_cli, driftwood_vault)

    # Then the span file found by key is enough
    assert result.returncode == 0
    assert result.json()["counts"] == {
        "repointed": 45,
        "stale-editorial": 1,
        "stale-removed": 2,
        "stale-substantive": 1,
    }


def test_migrate_text_output_names_the_outcomes(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the fixture vault
    # When a dry run is written for people
    result = run_cli("migrate-edition", "dw1", "dw2", "--vault", str(driftwood_vault), "--dry-run")

    # Then the counts and the note ids are listed
    assert result.returncode == 0
    assert "dry run, nothing written" in result.stdout
    assert "stale-substantive: 1" in result.stdout
    assert "  act.launch-boat" in result.stdout
    assert "  9.4" in result.stdout


def test_a_citation_the_old_span_file_lacks_is_left_and_listed(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note citing a span that is not in dw1's span file
    note = next(driftwood_vault.rglob("cl.formats.md"))
    text = note.read_text(encoding="utf-8")
    write_text(note, text.replace('  - "dw1#7.2"\n', '  - "dw1#7.2"\n  - "dw1#99.9"\n'))

    # When it is migrated
    result = _migrate(run_cli, driftwood_vault)

    # Then the unknown citation stays where it is and is reported; the others are re-pointed
    assert result.json()["unresolved"] == [{"note": "cl.formats", "reference": "dw1#99.9"}]
    assert _cited(driftwood_vault, "cl.formats") == ["dw2#7.1", "dw2#7.2", "dw1#99.9"]


def test_migrate_refuses_unusable_input(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given each way the input can be unusable
    vault = str(driftwood_vault)
    cases = {
        "span-file-missing": ("dw1", "dw9", "--vault", vault),
        "source-key-invalid": ("DW 1", "dw2", "--vault", vault),
        "editions-identical": ("dw2", "dw2", "--vault", vault),
        "not-a-vault": ("dw1", "dw2", "--vault", str(tmp_path)),
    }

    for code, arguments in cases.items():
        before = snapshot(driftwood_vault)

        # When the command runs
        result = run_cli("migrate-edition", *arguments, "--json")

        # Then it refuses with that code, exit 3, and writes nothing
        assert result.returncode == 3, code
        assert error_code(result) == code
        assert snapshot(driftwood_vault) == before


def test_a_note_that_cannot_be_rewritten_safely_refuses_the_whole_run(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note whose citation is written with a YAML escape, so its text never holds the
    # reference the parsed value names
    note = next(driftwood_vault.rglob("cl.formats.md"))
    text = note.read_text(encoding="utf-8")
    write_text(note, text.replace('"dw1#7.1"', '"dw\\u0031#7.1"'))
    before = snapshot(driftwood_vault)

    # When it is migrated
    result = _migrate(run_cli, driftwood_vault)

    # Then the run refuses and no note, however clean, has been written
    assert result.returncode == 3
    assert error_code(result) == "migration-rewrite-invalid"
    assert snapshot(driftwood_vault) == before
