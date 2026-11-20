"""INI-1 and INI-2: `init` writes an empty valid vault, and refuses a directory in use."""

import os
from pathlib import Path

import pytest

from conftest import CliRunner
from support.filesystem import requires_posix_permissions


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _assert_lint_is_clean(run_cli: CliRunner, vault: Path) -> None:
    lint = run_cli("lint", "--vault", str(vault), "--json")
    assert lint.returncode == 0
    assert lint.json()["findings"] == []


def test_init_creates_an_empty_valid_vault(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given an empty directory
    vault = tmp_path / "new-vault"
    vault.mkdir()

    # When init runs
    result = run_cli("init", str(vault), "--ruleset", "driftwood", "--json")

    # Then it exits 0, lists its paths sorted and relative, and the vault is valid
    assert result.returncode == 0
    created = result.json()["created"]
    assert isinstance(created, list)
    assert created == sorted(created)
    assert "_ruleset.yaml" in created
    assert {"clauses/", "model/zones/", "build/obligations/", "spans/"} <= set(created)
    assert not any(str(path).startswith(("/", ".obsidian")) for path in created)
    assert (vault / "_ruleset.yaml").read_text(encoding="utf-8").startswith("ruleset: driftwood\n")
    skeleton = _snapshot(vault)
    _assert_lint_is_clean(run_cli, vault)
    # And a second, separate init gives the same paths and bytes
    other = tmp_path / "other-vault"
    again = run_cli("init", str(other), "--ruleset", "driftwood", "--json")
    assert again.returncode == 0
    assert again.json()["created"] == created
    assert _snapshot(other) == skeleton


def test_init_refuses_a_non_empty_directory(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a directory holding one file
    vault = tmp_path / "in-use"
    vault.mkdir()
    (vault / "notes.txt").write_text("keep me\n", encoding="utf-8")
    before = _snapshot(vault)

    # When init runs
    result = run_cli("init", str(vault), "--ruleset", "driftwood", "--json")

    # Then nothing is written and the refusal is named
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "directory-not-empty"  # type: ignore[index]
    assert _snapshot(vault) == before
    assert sorted(path.name for path in vault.iterdir()) == ["notes.txt"]


def test_init_refuses_a_path_that_is_a_file(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a path naming a file
    target = tmp_path / "a-file"
    target.write_text("x\n", encoding="utf-8")

    # When init runs
    result = run_cli("init", str(target), "--ruleset", "driftwood", "--json")

    # Then it is refused and the file is untouched
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "not-a-directory"  # type: ignore[index]
    assert target.read_text(encoding="utf-8") == "x\n"


@pytest.mark.parametrize("ruleset_id", ["Driftwood", "9lives", "has space", "under_score", ""])
def test_init_refuses_a_malformed_ruleset_id(
    ruleset_id: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an id that is not lowercase letters, digits and hyphens starting with a letter
    vault = tmp_path / "bad-id"

    # When init runs
    result = run_cli("init", str(vault), "--ruleset", ruleset_id, "--json")

    # Then it is refused and nothing is created
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "ruleset-id-invalid"  # type: ignore[index]
    assert not vault.exists()


def test_init_below_a_file_is_a_refusal_not_a_traceback(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a file where the vault's parent directory would have to be
    blocker = tmp_path / "blocker"
    blocker.write_text("a file\n", encoding="utf-8")

    # When init is asked to create a vault below it
    result = run_cli("init", str(blocker / "vault"), "--ruleset", "driftwood", "--json")

    # Then it refuses with write-failed and the file is untouched
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "write-failed"  # type: ignore[index]
    assert blocker.read_text(encoding="utf-8") == "a file\n"


@requires_posix_permissions
def test_init_under_a_read_only_parent_is_a_refusal_not_a_traceback(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a parent directory that cannot be written
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        if os.access(parent, os.W_OK):
            pytest.skip("this account can write read-only directories")

        # When init creates a vault in it
        result = run_cli("init", str(parent / "vault"), "--ruleset", "driftwood", "--json")
    finally:
        parent.chmod(0o755)

    # Then it refuses with write-failed and creates nothing
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "write-failed"  # type: ignore[index]
    assert list(parent.iterdir()) == []
