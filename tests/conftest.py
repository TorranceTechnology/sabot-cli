"""Shared fixtures: an in-process CLI runner and copies of the Driftwood fixture vault."""

import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from sabot_cli.cli import main
from sabot_cli.testing import set_directory_listing

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


type ListingOrder = Callable[[bool], None]


@pytest.fixture
def force_listing_order() -> Iterator[ListingOrder]:
    """Make the vault walk see every directory in name order (False) or reverse name order (True).

    File systems list directories in an order of their own, often the sorted one, so a copy
    made in another order proves nothing; this forces the order the walk is handed.
    """

    def force(reverse: bool) -> None:
        def listing(directory: Path) -> list[Path]:
            return sorted(directory.iterdir(), key=lambda entry: entry.name, reverse=reverse)

        set_directory_listing(listing)

    yield force
    set_directory_listing(None)


def copy_vault(destination: Path, *, reverse: bool = False) -> Path:
    """Copy the fixture vault file by file, creating files in sorted or reverse order."""
    assert FIXTURE_VAULT.is_dir(), f"the Driftwood fixture vault is missing: {FIXTURE_VAULT}"
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
