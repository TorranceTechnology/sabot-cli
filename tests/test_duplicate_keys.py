"""A key written twice in front matter or in the ruleset is refused, never read as last-wins."""

from pathlib import Path

from conftest import CliRunner

HOST_NOTE = Path("clauses") / "cl.setup.md"


def _lint(run_cli: CliRunner, vault: Path, *options: str) -> dict[str, list[dict[str, str]]]:
    document = run_cli("lint", "--vault", str(vault), "--json", *options).json()
    return document  # type: ignore[return-value]


def test_a_note_with_a_repeated_property_is_frontmatter_invalid(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note that says `status: mapped` and later `status: refused`
    note = driftwood_vault / HOST_NOTE
    text = note.read_text(encoding="utf-8")
    assert "\nstatus: mapped\n" in text
    note.write_bytes(
        text.replace("\nstatus: mapped\n", "\nstatus: mapped\nstatus: refused\n", 1).encode()
    )

    # When the vault is linted, even strictly
    result = run_cli("lint", "--strict", "--vault", str(driftwood_vault), "--json")

    # Then the note is refused for what it is, naming the key, instead of passing as refused
    assert result.returncode == 1
    findings = result.json()["findings"]
    assert [(f["class"], f["path"]) for f in findings] == [
        ("frontmatter-invalid", HOST_NOTE.as_posix())
    ]  # type: ignore[union-attr, index]
    assert "duplicate key 'status'" in findings[0]["message"]  # type: ignore[index]


def test_the_commands_that_read_notes_skip_a_note_with_a_repeated_property(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the same note with a repeated property
    note = driftwood_vault / HOST_NOTE
    text = note.read_text(encoding="utf-8")
    note.write_bytes(
        text.replace("\nstatus: mapped\n", "\nstatus: mapped\nstatus: refused\n", 1).encode()
    )

    # When search reads the vault
    result = run_cli("search", "--where", "has:status", "--vault", str(driftwood_vault), "--json")

    # Then the note is left out and named on stderr, as any unreadable note is
    assert "cl.setup" not in result.stdout
    assert f"skipped {HOST_NOTE.as_posix()}" in result.stderr


def test_a_ruleset_with_a_repeated_key_is_ruleset_invalid(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ruleset that names its schema version twice
    ruleset = driftwood_vault / "_ruleset.yaml"
    ruleset.write_bytes(
        ruleset.read_bytes().replace(
            b"schemaVersion: 1\n", b"schemaVersion: 1\nschemaVersion: 1\n", 1
        )
    )

    # When the vault is linted
    result = run_cli("lint", "--vault", str(driftwood_vault), "--json")

    # Then lint reports it as a ruleset finding
    assert result.returncode == 1
    findings = result.json()["findings"]
    assert [f["class"] for f in findings] == ["ruleset-invalid"]  # type: ignore[union-attr, index]
    assert "duplicate key 'schemaVersion'" in findings[0]["message"]  # type: ignore[index]

    # When another command reads the vault
    refused = run_cli("coverage", "--vault", str(driftwood_vault), "--json")

    # Then it refuses with the same code
    assert (refused.returncode, refused.json()["error"]["code"]) == (3, "ruleset-invalid")  # type: ignore[index]
