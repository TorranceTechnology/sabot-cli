"""Every command that works on a vault loads its ruleset and schema the same way."""

from pathlib import Path

import pytest

from conftest import CliRunner
from invocations import COMMAND_NAMES, invocation
from support.vault_state import vault_state

# `lint` reports an unusable ruleset as a finding, and `init` has no vault yet.
LOADING_COMMANDS = tuple(name for name in COMMAND_NAMES if name not in {"init", "lint"})


@pytest.mark.parametrize("name", LOADING_COMMANDS)
@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("schemaVersion: 1", "schemaVersion: 2", "schema-version-unsupported"),
        ("parent: rule-process", "parent: no-such-type", "ruleset-invalid"),
    ],
    ids=["unsupported-schema-version", "extension-with-no-parent"],
)
def test_a_command_refuses_a_ruleset_the_schema_cannot_be_built_from(
    name: str,
    old: str,
    new: str,
    code: str,
    run_cli: CliRunner,
    driftwood_vault: Path,
    tmp_path: Path,
) -> None:
    # Given a vault whose ruleset names a schema version we do not read, or an extension whose
    # parent is not a fixed type
    ruleset = driftwood_vault / "_ruleset.yaml"
    text = ruleset.read_text(encoding="utf-8")
    assert old in text
    ruleset.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    before = vault_state(driftwood_vault)

    # When the command runs against it
    result = run_cli(name, *invocation(name, tmp_path), "--vault", str(driftwood_vault), "--json")

    # Then it is refused with the same code whichever command it is, and the vault is untouched
    assert result.returncode == 3
    assert result.json()["error"]["code"] == code  # type: ignore[index]
    assert vault_state(driftwood_vault) == before
