"""The content guard refuses guarded terms and names none of them.

The term used here is invented; its hash lives only in this test's own hash list.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent
GUARD = REPOSITORY / "ci" / "check_content_guard.py"
INVENTED_TERM = "zorblax quillfeather"


def _run_guard(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GUARD), *arguments], capture_output=True, text=True)


def _hash_of(term: str) -> str:
    result = _run_guard("--hash", term)
    assert result.returncode == 0
    return result.stdout.strip()


@pytest.fixture
def scratch_repository(tmp_path: Path) -> Path:
    """An empty git repository plus a hash list that guards only the invented term."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (tmp_path / "hashes.txt").write_text(
        f"# test-only list\n{_hash_of(INVENTED_TERM)}\n", encoding="utf-8"
    )
    return repository


def _scan(repository: Path) -> subprocess.CompletedProcess[str]:
    return _run_guard("--root", str(repository), "--hashes", str(repository.parent / "hashes.txt"))


@pytest.mark.parametrize(
    "text",
    [
        "A deck called Zorblax Quillfeather appears here.",
        "zorblax\n  QUILLFEATHER, split across lines",
        "mentions zorblax-quillfeather with a hyphen",
    ],
)
def test_a_planted_term_is_refused(scratch_repository: Path, text: str) -> None:
    # Given a tracked file holding the term in some spelling
    (scratch_repository / "notes.md").write_text(text + "\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it exits 1, names the file, and does not print the term
    assert result.returncode == 1
    assert "notes.md" in result.stdout
    assert "zorblax" not in (result.stdout + result.stderr).lower()


def test_a_guarded_file_name_is_refused(scratch_repository: Path) -> None:
    # Given a file whose name holds the term
    (scratch_repository / "zorblax-quillfeather.md").write_text("harmless\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it exits 1
    assert result.returncode == 1


def test_a_single_word_of_the_term_alone_is_allowed(scratch_repository: Path) -> None:
    # Given a file with only one word of a two-word term
    (scratch_repository / "notes.md").write_text("zorblax is fine alone\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then nothing is reported
    assert result.returncode == 0
    assert result.stdout == ""


def test_a_binary_file_is_not_scanned(scratch_repository: Path) -> None:
    # Given a binary file whose bytes contain the term
    (scratch_repository / "blob.bin").write_bytes(b"\0" + INVENTED_TERM.encode() + b"\0")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then nothing is reported
    assert result.returncode == 0


def test_the_repository_passes_its_own_guard() -> None:
    # Given the checkout and the shipped hash list
    # When the guard scans it
    result = _run_guard("--root", str(REPOSITORY))

    # Then nothing is reported
    assert result.returncode == 0, result.stdout
