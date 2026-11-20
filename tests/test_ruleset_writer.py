"""`ruleset_with_source` edits `_ruleset.yaml` the way the ingest command needs it to."""

from pathlib import Path

import pytest

from conftest import CliRunner, copy_vault
from sabot_cli.rules import RulesetError, load_ruleset, ruleset_with_source
from support.plant import register_second_edition

RULESET = "_ruleset.yaml"


def _register(vault: Path, key: str, note: str, supersedes: str | None = None) -> None:
    (vault / RULESET).write_bytes(ruleset_with_source(vault, key, note, supersedes))


def test_ruleset_with_source_matches_the_fixture_helper_and_keeps_the_rest(
    driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given two copies of the vault, one registered through the fixture helper
    reference = copy_vault(tmp_path / "reference")
    register_second_edition(reference)

    # When the same source is registered through the public writer
    _register(driftwood_vault, "dw2", "src.dw2", supersedes="dw1")

    # Then the parsed rulesets are equal, with the extensions and scope untouched
    written = load_ruleset(driftwood_vault)
    assert written == load_ruleset(reference)
    assert [source.key for source in written.sources] == ["dw1", "dw2"]
    assert written.extensions.count() == 2
    assert written.scope == {"dw1": ()}
    assert (driftwood_vault / RULESET).read_bytes() == (reference / RULESET).read_bytes()


def test_ruleset_with_source_writes_stable_text_with_keys_in_order(driftwood_vault: Path) -> None:
    # Given a vault with one registered source
    # When two more are registered
    _register(driftwood_vault, "dw2", "src.dw2", supersedes="dw1")
    _register(driftwood_vault, "dw3", "src.dw3")
    text = (driftwood_vault / RULESET).read_text(encoding="utf-8")

    # Then the file keeps its top-level key order, lists sources in order and ends in one newline
    keys = [line.split(":")[0] for line in text.splitlines() if line and not line.startswith(" ")]
    assert keys == ["ruleset", "schemaVersion", "sources", "scope", "extensions"]
    assert text.index("key: dw1") < text.index("key: dw2") < text.index("key: dw3")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text


def test_ruleset_with_source_refuses_a_key_already_registered(driftwood_vault: Path) -> None:
    # Given a vault in which dw1 is registered
    before = (driftwood_vault / RULESET).read_bytes()

    # When dw1 is registered again
    # Then it is refused by name and the file is unchanged
    with pytest.raises(RulesetError) as raised:
        ruleset_with_source(driftwood_vault, "dw1", "src.dw1")
    assert raised.value.code == "source-key-registered"
    assert (driftwood_vault / RULESET).read_bytes() == before


def test_ruleset_with_source_keeps_comments_and_layout(driftwood_vault: Path) -> None:
    # Given a ruleset with comments, a flow list and a block scalar elsewhere
    text = (driftwood_vault / RULESET).read_text(encoding="utf-8")
    annotated = "# the Driftwood ruleset\n" + text.replace(
        "scope:", "# which spans are in scope\nscope:"
    )
    (driftwood_vault / RULESET).write_text(annotated, encoding="utf-8")

    # When a source is registered
    _register(driftwood_vault, "dw2", "src.dw2", supersedes="dw1")

    # Then every original line is still there, in place, and only the entry was added
    written = (driftwood_vault / RULESET).read_text(encoding="utf-8")
    added = "  - key: dw2\n    note: src.dw2\n    supersedes: dw1\n"
    assert written == annotated.replace("    note: src.dw1\n", "    note: src.dw1\n" + added)


def test_ruleset_with_source_quotes_a_key_that_yaml_would_read_as_a_number(
    driftwood_vault: Path,
) -> None:
    # Given a source key that reads as a float when unquoted
    # When it is registered
    _register(driftwood_vault, "1.5e5", "src.1.5e5")

    # Then the ruleset still loads and the key is the text that was given
    assert [source.key for source in load_ruleset(driftwood_vault).sources] == ["dw1", "1.5e5"]


@pytest.mark.parametrize("key", ["yes", "on", "off", "no", "y", "n", "0x1f", "2024-01-01", "1e3"])
def test_ruleset_with_source_quotes_a_key_that_another_yaml_reader_would_type(
    key: str, driftwood_vault: Path
) -> None:
    # Given a key that this repository's loader keeps as text but other YAML readers read as a
    # boolean, a number or a date
    # When it is registered
    _register(driftwood_vault, key, f"src.{key}")

    # Then the file quotes it, so every reader sees the text that was given
    text = (driftwood_vault / RULESET).read_text(encoding="utf-8")
    assert f'key: "{key}"\n' in text
    assert f'note: "src.{key}"' not in text  # a note id starts with a letter and has a dot
    assert [source.key for source in load_ruleset(driftwood_vault).sources] == ["dw1", key]


@pytest.mark.parametrize("key", ["dw3", "src.dw3", "yes-no", "onward", "noon"])
def test_ruleset_with_source_leaves_an_ordinary_key_unquoted(
    key: str, driftwood_vault: Path
) -> None:
    # Given a key no YAML reader types
    # When it is registered
    _register(driftwood_vault, key, "src.dw3")

    # Then it is written plain
    assert f"key: {key}\n" in (driftwood_vault / RULESET).read_text(encoding="utf-8")


def test_ingest_of_a_number_like_key_leaves_a_lintable_vault(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a text source and a key that reads as a float when unquoted
    source = driftwood_vault.parent / "source.txt"
    source.write_text("1. First rule.\n", encoding="utf-8")

    # When it is ingested
    result = run_cli(
        "ingest", str(source), "--key", "1.5e5", "--label", "L", "--title", "T",
        "--edition", "1", "--retrieved", "2026-01-01", "--vault", str(driftwood_vault),
    )  # fmt: skip

    # Then the vault still loads: the next command is not refused for an invalid ruleset
    assert result.returncode == 0
    assert run_cli("coverage", "--source", "dw1", "--vault", str(driftwood_vault)).returncode != 3


def test_ruleset_with_source_extends_an_empty_flow_list(tmp_path: Path) -> None:
    # Given a ruleset that registers nothing yet, as `init` writes it
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / RULESET).write_text(
        "ruleset: x\nschemaVersion: 1\nsources: []\nscope: {}\nextensions: {}\n", encoding="utf-8"
    )

    # When a source is registered
    _register(vault, "a1", "src.a1")

    # Then the entry stands as a block list and the other keys are unchanged
    assert (vault / RULESET).read_text(encoding="utf-8") == (
        "ruleset: x\nschemaVersion: 1\nsources:\n  - key: a1\n    note: src.a1\n"
        "scope: {}\nextensions: {}\n"
    )


def test_ruleset_with_source_keeps_crlf_line_endings(tmp_path: Path) -> None:
    # Given a ruleset saved with CRLF line endings and a byte order mark
    vault = tmp_path / "vault"
    vault.mkdir()
    original = "ruleset: x\nschemaVersion: 1\nsources:\n  - key: a1\n    note: src.a1\nscope: {}\n"
    (vault / RULESET).write_bytes(b"\xef\xbb\xbf" + original.replace("\n", "\r\n").encode("utf-8"))

    # When a source is registered
    data = ruleset_with_source(vault, "a2", "src.a2")

    # Then the mark and the CRLF endings are kept
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\r\n") == data.count(b"\n")
    assert b"  - key: a2\r\n    note: src.a2\r\n" in data


def test_ruleset_with_source_refuses_a_sources_entry_it_cannot_extend(tmp_path: Path) -> None:
    # Given a ruleset whose sources are written as a flow list with an entry
    vault = tmp_path / "vault"
    vault.mkdir()
    text = "ruleset: x\nschemaVersion: 1\nsources: [{key: a1, note: src.a1}]\nscope: {}\n"
    (vault / RULESET).write_text(text, encoding="utf-8")

    # When another source is registered
    # Then it is refused by name and the file is unchanged
    with pytest.raises(RulesetError) as raised:
        ruleset_with_source(vault, "a2", "src.a2")
    assert raised.value.code == "ruleset-not-editable"
    assert (vault / RULESET).read_text(encoding="utf-8") == text


def test_ruleset_with_source_refuses_a_result_that_would_change_anything_else(
    tmp_path: Path,
) -> None:
    # Given a ruleset whose id is a quoted scalar spanning lines, one of which reads as a
    # `sources:` entry
    vault = tmp_path / "vault"
    vault.mkdir()
    text = 'ruleset: "x\nsources:\n  - y"\nschemaVersion: 1\nscope: {}\n'
    (vault / RULESET).write_text(text, encoding="utf-8")

    # When a source is registered
    # Then the insertion is caught by re-parsing, refused by name and the file is unchanged
    with pytest.raises(RulesetError) as raised:
        ruleset_with_source(vault, "a2", "src.a2")
    assert raised.value.code == "ruleset-rewrite-invalid"
    assert (vault / RULESET).read_text(encoding="utf-8") == text
