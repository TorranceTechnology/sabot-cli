"""Shared fixtures: an in-process CLI runner and copies of the Driftwood fixture vault."""

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from sabot_cli.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "driftwood"
FIXTURE_VAULT = FIXTURES / "vault"


@dataclass(frozen=True)
class CliResult:
    """What one in-process run of the command line produced."""

    returncode: int
    stdout: str
    stderr: str

    def json(self) -> dict[str, object]:
        """Parse stdout as the single JSON document a --json run writes."""
        document = json.loads(self.stdout)
        assert isinstance(document, dict), "stdout is not one JSON object"
        return document


type CliRunner = Callable[..., CliResult]


@pytest.fixture
def run_cli(capsys: pytest.CaptureFixture[str]) -> CliRunner:
    """Run `sabot_cli.cli.main` in-process with the given arguments."""

    def run(*argv: str) -> CliResult:
        returncode = main(list(argv))
        captured = capsys.readouterr()
        return CliResult(returncode, captured.out, captured.err)

    return run


def copy_vault(destination: Path, *, reverse: bool = False) -> Path:
    """Copy the fixture vault file by file, creating files in sorted or reverse order."""
    if not FIXTURE_VAULT.is_dir():
        pytest.skip("the Driftwood fixture vault is not present")
    files = sorted(path for path in FIXTURE_VAULT.rglob("*") if path.is_file())
    for source in reversed(files) if reverse else files:
        target = destination / source.relative_to(FIXTURE_VAULT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return destination


@pytest.fixture
def driftwood_vault(tmp_path: Path) -> Path:
    """A fresh, writable copy of the fixture vault."""
    return copy_vault(tmp_path / "vault")


@pytest.fixture
def driftwood_vault_twin(tmp_path: Path) -> Path:
    """A second copy whose files were created in the opposite order, under another name."""
    return copy_vault(tmp_path / "twin" / "vault-twin", reverse=True)
