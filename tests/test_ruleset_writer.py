"""`register_source` edits `_ruleset.yaml` the way the ingest command will need it to."""

from pathlib import Path

import pytest

from conftest import copy_vault
from sabot_cli.rules import RulesetError, load_ruleset, register_source
from support.plant import register_second_edition


def test_register_source_matches_the_fixture_helper_and_keeps_the_rest(
    driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given two copies of the vault, one registered through the fixture helper
    reference = copy_vault(tmp_path / "reference")
    register_second_edition(reference)

    # When the same source is registered through the public writer
    register_source(driftwood_vault, "dw2", "src.dw2", supersedes="dw1")

    # Then the parsed rulesets are equal, with the extensions and scope untouched
    written = load_ruleset(driftwood_vault)
    assert written == load_ruleset(reference)
    assert [source.key for source in written.sources] == ["dw1", "dw2"]
    assert written.extensions.count() == 2
    assert written.scope == {"dw1": ()}


def test_register_source_writes_stable_text_with_keys_in_order(driftwood_vault: Path) -> None:
    # Given a vault with one registered source
    # When two more are registered
    register_source(driftwood_vault, "dw2", "src.dw2", supersedes="dw1")
    register_source(driftwood_vault, "dw3", "src.dw3")
    text = (driftwood_vault / "_ruleset.yaml").read_text(encoding="utf-8")

    # Then the file keeps its top-level key order, lists sources in order and ends in one newline
    keys = [line.split(":")[0] for line in text.splitlines() if line and not line.startswith(" ")]
    assert keys == ["ruleset", "schemaVersion", "sources", "scope", "extensions"]
    assert text.index("key: dw1") < text.index("key: dw2") < text.index("key: dw3")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text


def test_register_source_refuses_a_key_already_registered(driftwood_vault: Path) -> None:
    # Given a vault in which dw1 is registered
    before = (driftwood_vault / "_ruleset.yaml").read_bytes()

    # When dw1 is registered again
    # Then it is refused by name and the file is unchanged
    with pytest.raises(RulesetError) as raised:
        register_source(driftwood_vault, "dw1", "src.dw1")
    assert raised.value.code == "source-key-registered"
    assert (driftwood_vault / "_ruleset.yaml").read_bytes() == before
